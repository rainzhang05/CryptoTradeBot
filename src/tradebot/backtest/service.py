"""Backtest orchestration and simulate-mode execution service."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tradebot.backtest.execution import apply_decision
from tradebot.backtest.models import (
    BacktestRunSummary,
    DecisionSnapshot,
    EquityPoint,
    FillEvent,
    PortfolioState,
    SimulationCycleSummary,
)
from tradebot.backtest.storage import (
    backtest_decisions_file,
    backtest_equity_curve_file,
    backtest_fills_file,
    backtest_report_file,
    latest_backtest_report_file,
    simulate_state_file,
    write_csv_rows,
)
from tradebot.cancellation import CancellationToken
from tradebot.config import AppConfig, identify_strategy_preset
from tradebot.data.integrity import read_candles
from tradebot.data.models import Candle
from tradebot.data.storage import canonical_candle_file, write_json
from tradebot.logging_config import get_logger
from tradebot.research.service import ResearchService
from tradebot.strategy.models import ResearchStrategyProfile
from tradebot.strategy.service import StrategyEngine


class BacktestService:
    """Run deterministic backtests and latest-snapshot simulation cycles."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.paths = config.resolved_paths()
        self.data_settings = config.resolved_data_settings()
        self.logger = get_logger("tradebot.backtest")
        self.research_service = ResearchService(config)
        self.strategy_engine = StrategyEngine(config)

    def run_backtest(
        self,
        assets: tuple[str, ...] | None = None,
        force_features: bool = False,
        dataset_track: str | None = None,
        research_profile: ResearchStrategyProfile | None = None,
        start_timestamp: int | None = None,
        end_timestamp: int | None = None,
        cancellation_token: CancellationToken | None = None,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> BacktestRunSummary:
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        strategy_preset = identify_strategy_preset(self.config)
        self.logger.info(
            "backtest started",
            extra={
                "assets": list(assets or ()),
                "force_features": force_features,
                "dataset_track": dataset_track,
                "strategy_preset": strategy_preset,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
            },
        )
        feature_store = self.research_service.build_feature_store(
            assets=assets,
            force=force_features,
            dataset_track=dataset_track,
            cancellation_token=cancellation_token,
        )
        if feature_store.row_count <= 0:
            raise ValueError("Feature store does not contain enough rows for backtesting")
        selected_assets = tuple(feature_store.selected_assets)

        rows = self._load_feature_rows(Path(feature_store.dataset_file))
        rows_by_timestamp = self._rows_by_timestamp(rows)
        bars_by_asset = self._load_daily_bars(selected_assets)
        evaluation_timestamps = self._evaluation_timestamps(
            rows_by_timestamp,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )
        next_timestamp_map = {
            evaluation_timestamps[index]: evaluation_timestamps[index + 1]
            for index in range(len(evaluation_timestamps) - 1)
        }
        strategy_engine = StrategyEngine(self.config, research_profile)

        portfolio = PortfolioState(cash_usd=self.config.backtest.initial_cash_usd)
        fills: list[FillEvent] = []
        decisions: list[DecisionSnapshot] = []
        equity_curve: list[EquityPoint] = []

        for timestamp in evaluation_timestamps:
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            execution_timestamp = next_timestamp_map.get(timestamp)
            if execution_timestamp is None:
                continue
            rows_for_timestamp = rows_by_timestamp[timestamp]
            signal_bars = self._bars_at_timestamp(bars_by_asset, timestamp)
            execution_bars = self._bars_at_timestamp(bars_by_asset, execution_timestamp)
            if not self._has_required_bars(
                rows_for_timestamp=rows_for_timestamp,
                portfolio=portfolio,
                signal_bars=signal_bars,
                execution_bars=execution_bars,
            ):
                continue
            if not equity_curve:
                equity_curve.append(
                    EquityPoint(
                        timestamp=timestamp,
                        equity_usd=portfolio.cash_usd,
                        cash_usd=portfolio.cash_usd,
                        gross_exposure=0.0,
                    )
                )

            strategy_decision = strategy_engine.evaluate(
                timestamp=timestamp,
                rows_by_asset=rows_for_timestamp,
                portfolio=portfolio,
                prices_by_asset={asset: bar.close for asset, bar in signal_bars.items()},
            )
            decision = DecisionSnapshot(
                timestamp=execution_timestamp,
                regime_state=strategy_decision.regime_state,
                risk_state=strategy_decision.risk_state,
                exposure_fraction=strategy_decision.exposure_fraction,
                target_weights=strategy_decision.target_weights,
                scores=strategy_decision.scores,
                is_frozen=strategy_decision.is_frozen,
                freeze_reason=strategy_decision.freeze_reason,
                asset_actions={
                    asset: asset_decision.action
                    for asset, asset_decision in strategy_decision.asset_decisions.items()
                },
                asset_reasons={
                    asset: asset_decision.reason
                    for asset, asset_decision in strategy_decision.asset_decisions.items()
                },
            )
            portfolio, intents, cycle_fills, end_equity, gross_exposure = apply_decision(
                portfolio=portfolio,
                decision=decision,
                execution_bars=execution_bars,
                mark_bars=execution_bars,
                settings=self.config.backtest,
            )
            decisions.append(decision)
            fills.extend(cycle_fills)
            equity_curve.append(
                EquityPoint(
                    timestamp=execution_timestamp,
                    equity_usd=end_equity,
                    cash_usd=portfolio.cash_usd,
                    gross_exposure=gross_exposure,
                )
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "timestamp": execution_timestamp,
                        "decision_count": len(decisions),
                        "fill_count": len(fills),
                    }
                )

        if len(equity_curve) < 2:
            raise ValueError("Backtest did not produce any executable decision points")

        run_id = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
        report_path = backtest_report_file(self.paths.artifacts_dir, run_id)
        fills_path = backtest_fills_file(self.paths.artifacts_dir, run_id)
        equity_path = backtest_equity_curve_file(self.paths.artifacts_dir, run_id)
        decisions_path = backtest_decisions_file(self.paths.artifacts_dir, run_id)

        fill_rows = [fill.to_dict() for fill in fills]
        decision_rows = [self._decision_row(decision) for decision in decisions]
        equity_rows = [point.to_dict() for point in equity_curve]
        write_csv_rows(
            fills_path,
            fieldnames=[
                "timestamp",
                "asset",
                "side",
                "quantity",
                "fill_price",
                "gross_notional_usd",
                "fee_paid_usd",
                "realized_pnl_usd",
            ],
            rows=fill_rows,
        )
        write_csv_rows(
            decisions_path,
            fieldnames=[
                "timestamp",
                "regime_state",
                "risk_state",
                "exposure_fraction",
                "is_frozen",
                "freeze_reason",
                "target_weights_json",
                "scores_json",
                "asset_actions_json",
                "asset_reasons_json",
            ],
            rows=decision_rows,
        )
        write_csv_rows(
            equity_path,
            fieldnames=["timestamp", "equity_usd", "cash_usd", "gross_exposure"],
            rows=equity_rows,
        )

        max_drawdown = self._max_drawdown(equity_curve)
        final_equity = equity_curve[-1].equity_usd
        liquidation_metrics = self._liquidation_metrics(
            portfolio=portfolio,
            mark_bars=self._bars_at_timestamp(bars_by_asset, equity_curve[-1].timestamp),
        )
        metrics = self._performance_metrics(
            initial_cash=self.config.backtest.initial_cash_usd,
            equity_curve=equity_curve,
            fills=fills,
        )
        yearly_returns = self._yearly_returns(equity_curve, decisions, fills)
        benchmarks = self._benchmark_summary(
            bars_by_asset=bars_by_asset,
            start_timestamp=equity_curve[0].timestamp,
            end_timestamp=equity_curve[-1].timestamp,
        )
        diagnostics = self._decision_diagnostics(decisions)
        summary = BacktestRunSummary(
            run_id=run_id,
            report_file=str(report_path),
            fills_file=str(fills_path),
            equity_curve_file=str(equity_path),
            decisions_file=str(decisions_path),
            dataset_id=feature_store.dataset_id,
            strategy_preset=strategy_preset,
            decision_count=len(decisions),
            fill_count=len(fills),
            final_equity_usd=final_equity,
            total_return=(final_equity / self.config.backtest.initial_cash_usd) - 1,
            max_drawdown=max_drawdown,
            total_fees_usd=portfolio.fees_paid_usd,
            net_liquidation_equity_usd=cast(
                float | None,
                liquidation_metrics["net_liquidation_equity_usd"],
            ),
            net_liquidation_total_return=cast(
                float | None,
                liquidation_metrics["net_liquidation_total_return"],
            ),
            estimated_liquidation_fee_usd=cast(
                float | None,
                liquidation_metrics["estimated_liquidation_fee_usd"],
            ),
            estimated_liquidation_slippage_usd=cast(
                float | None,
                liquidation_metrics["estimated_liquidation_slippage_usd"],
            ),
            start_timestamp=equity_curve[0].timestamp,
            end_timestamp=equity_curve[-1].timestamp,
            cagr=cast(float | None, metrics["cagr"]),
            calmar_ratio=cast(float | None, metrics["calmar_ratio"]),
            annualized_volatility=cast(float | None, metrics["annualized_volatility"]),
            daily_sharpe=cast(float | None, metrics["daily_sharpe"]),
            turnover=cast(float | None, metrics["turnover"]),
            fee_to_gross_pnl_ratio=cast(float | None, metrics["fee_to_gross_pnl_ratio"]),
            days_invested=cast(int | None, metrics["days_invested"]),
            trades_per_year=cast(float | None, metrics["trades_per_year"]),
        )
        payload = summary.to_dict() | {
            "portfolio": portfolio.to_dict(),
            "dataset_file": feature_store.dataset_file,
            "dataset_track": feature_store.dataset_track,
            "strategy_preset": strategy_preset,
            "metrics": metrics,
            "yearly_returns": yearly_returns,
            "benchmarks": benchmarks,
            "diagnostics": diagnostics,
            "liquidation": liquidation_metrics,
            "period": {
                "start_timestamp": equity_curve[0].timestamp,
                "end_timestamp": equity_curve[-1].timestamp,
                "start_date": datetime.fromtimestamp(
                    equity_curve[0].timestamp,
                    tz=UTC,
                ).date().isoformat(),
                "end_date": datetime.fromtimestamp(
                    equity_curve[-1].timestamp,
                    tz=UTC,
                ).date().isoformat(),
                "years": metrics["period_years"],
            },
            "research_profile": strategy_engine.research_profile.to_dict(),
        }
        write_json(report_path, payload)
        write_json(latest_backtest_report_file(self.paths.artifacts_dir), payload)
        self.logger.info(
            "backtest completed",
            extra={
                "run_id": run_id,
                "dataset_id": feature_store.dataset_id,
                "decision_count": len(decisions),
                "fill_count": len(fills),
                "final_equity_usd": final_equity,
                "net_liquidation_equity_usd": liquidation_metrics["net_liquidation_equity_usd"],
            },
        )
        return summary

    def load_backtest_report(self, run_id: str | None = None) -> dict[str, Any]:
        if run_id is None:
            report_path = latest_backtest_report_file(self.paths.artifacts_dir)
        else:
            report_path = backtest_report_file(self.paths.artifacts_dir, run_id)
        if not report_path.exists():
            raise FileNotFoundError(f"Backtest report does not exist: {report_path}")
        return cast(dict[str, Any], json.loads(report_path.read_text(encoding="utf-8")))

    def simulate_latest_cycle(
        self,
        assets: tuple[str, ...] | None = None,
        force_features: bool = False,
        dataset_track: str | None = None,
    ) -> SimulationCycleSummary:
        state_path = simulate_state_file(self.paths.state_dir)
        portfolio = self._load_simulation_state(state_path)

        try:
            feature_store = self.research_service.build_feature_store(
                assets=assets,
                force=force_features,
                dataset_track=dataset_track,
            )
        except (FileNotFoundError, ValueError):
            self._write_simulation_state(state_path, portfolio)
            summary = SimulationCycleSummary(
                dataset_id=None,
                timestamp=None,
                status="waiting_for_data",
                regime_state=None,
                risk_state=None,
                equity_usd=portfolio.cash_usd,
                cash_usd=portfolio.cash_usd,
                fill_count=0,
                fills=[],
                state_file=str(state_path),
                holdings=self._holdings(portfolio),
            )
            self.logger.info("simulate cycle waiting for data")
            return summary

        if feature_store.row_count <= 0:
            self._write_simulation_state(state_path, portfolio)
            summary = SimulationCycleSummary(
                dataset_id=feature_store.dataset_id,
                timestamp=None,
                status="waiting_for_signals",
                regime_state=None,
                risk_state=None,
                equity_usd=portfolio.cash_usd,
                cash_usd=portfolio.cash_usd,
                fill_count=0,
                fills=[],
                state_file=str(state_path),
                holdings=self._holdings(portfolio),
            )
            self.logger.info(
                "simulate cycle waiting for signals",
                extra={"dataset_id": feature_store.dataset_id},
            )
            return summary

        rows = self._load_feature_rows(Path(feature_store.dataset_file))
        latest_timestamp = max(cast(int, row["timestamp"]) for row in rows)
        rows_for_timestamp = {
            str(row["asset"]): row
            for row in rows
            if cast(int, row["timestamp"]) == latest_timestamp
        }
        bars_by_asset = self._load_daily_bars(tuple(rows_for_timestamp))
        mark_bars = self._bars_at_timestamp(bars_by_asset, latest_timestamp)
        if len(mark_bars) != len(rows_for_timestamp):
            self._write_simulation_state(state_path, portfolio)
            summary = SimulationCycleSummary(
                dataset_id=feature_store.dataset_id,
                timestamp=latest_timestamp,
                status="waiting_for_data",
                regime_state=None,
                risk_state=None,
                equity_usd=portfolio.cash_usd,
                cash_usd=portfolio.cash_usd,
                fill_count=0,
                fills=[],
                state_file=str(state_path),
                holdings=self._holdings(portfolio),
            )
            self.logger.info(
                "simulate cycle waiting for aligned data",
                extra={"dataset_id": feature_store.dataset_id, "timestamp": latest_timestamp},
            )
            return summary

        strategy_decision = self.strategy_engine.evaluate(
            timestamp=latest_timestamp,
            rows_by_asset=rows_for_timestamp,
            portfolio=portfolio,
            prices_by_asset={asset: bar.close for asset, bar in mark_bars.items()},
        )
        decision = DecisionSnapshot(
            timestamp=latest_timestamp,
            regime_state=strategy_decision.regime_state,
            risk_state=strategy_decision.risk_state,
            exposure_fraction=strategy_decision.exposure_fraction,
            target_weights=strategy_decision.target_weights,
            scores=strategy_decision.scores,
            is_frozen=strategy_decision.is_frozen,
            freeze_reason=strategy_decision.freeze_reason,
            asset_actions={
                asset: asset_decision.action
                for asset, asset_decision in strategy_decision.asset_decisions.items()
            },
            asset_reasons={
                asset: asset_decision.reason
                for asset, asset_decision in strategy_decision.asset_decisions.items()
            },
        )
        portfolio, _, fills, end_equity, _ = apply_decision(
            portfolio=portfolio,
            decision=decision,
            execution_bars=mark_bars,
            mark_bars=mark_bars,
            settings=self.config.backtest,
        )
        self._write_simulation_state(state_path, portfolio)
        summary = SimulationCycleSummary(
            dataset_id=feature_store.dataset_id,
            timestamp=latest_timestamp,
            status="frozen" if strategy_decision.is_frozen else "ok",
            regime_state=strategy_decision.regime_state,
            risk_state=strategy_decision.risk_state,
            equity_usd=end_equity,
            cash_usd=portfolio.cash_usd,
            fill_count=len(fills),
            fills=fills,
            state_file=str(state_path),
            freeze_reason=strategy_decision.freeze_reason,
            holdings=self._holdings(portfolio),
            incidents=(
                [strategy_decision.freeze_reason]
                if strategy_decision.freeze_reason
                else []
            ),
            portfolio_drawdown=strategy_decision.portfolio_drawdown,
            target_weights=decision.target_weights,
            decision_actions=decision.asset_actions,
            decision_reasons=decision.asset_reasons,
        )
        self.logger.info(
            "simulate cycle completed",
            extra={
                "dataset_id": feature_store.dataset_id,
                "timestamp": latest_timestamp,
                "status": summary.status,
                "fill_count": len(fills),
                "freeze_reason": strategy_decision.freeze_reason,
            },
        )
        return summary

    def _load_daily_bars(self, assets: tuple[str, ...]) -> dict[str, dict[int, Candle]]:
        bars_by_asset: dict[str, dict[int, Candle]] = {}
        for asset in assets:
            path = canonical_candle_file(self.data_settings.canonical_dir, asset, "1d")
            bars_by_asset[asset] = {candle.timestamp: candle for candle in read_candles(path)}
        return bars_by_asset

    def _load_feature_rows(self, path: Path) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                parsed = {
                    key: self._parse_feature_value(key, value)
                    for key, value in row.items()
                    if key is not None and value is not None
                }
                rows.append(parsed)
        return rows

    def _rows_by_timestamp(
        self,
        rows: list[dict[str, object]],
    ) -> dict[int, dict[str, dict[str, object]]]:
        grouped: dict[int, dict[str, dict[str, object]]] = defaultdict(dict)
        for row in rows:
            grouped[cast(int, row["timestamp"])][str(row["asset"])] = row
        return dict(grouped)

    def _bars_at_timestamp(
        self,
        bars_by_asset: dict[str, dict[int, Candle]],
        timestamp: int,
    ) -> dict[str, Candle]:
        return {
            asset: bars[timestamp]
            for asset, bars in bars_by_asset.items()
            if timestamp in bars
        }

    def _evaluation_timestamps(
        self,
        rows_by_timestamp: dict[int, dict[str, dict[str, object]]],
        *,
        start_timestamp: int | None,
        end_timestamp: int | None,
    ) -> list[int]:
        timestamps = sorted(rows_by_timestamp)
        if start_timestamp is not None:
            timestamps = [timestamp for timestamp in timestamps if timestamp >= start_timestamp]
        if end_timestamp is not None:
            timestamps = [timestamp for timestamp in timestamps if timestamp <= end_timestamp]
        return timestamps

    def _has_required_bars(
        self,
        *,
        rows_for_timestamp: dict[str, dict[str, object]],
        portfolio: PortfolioState,
        signal_bars: dict[str, Candle],
        execution_bars: dict[str, Candle],
    ) -> bool:
        required_assets = set(rows_for_timestamp) | set(portfolio.positions)
        return all(
            asset in signal_bars and asset in execution_bars
            for asset in required_assets
        )

    def _common_timestamps(self, bars_by_asset: dict[str, dict[int, Candle]]) -> list[int]:
        timestamp_sets = [set(bars) for bars in bars_by_asset.values() if bars]
        if not timestamp_sets:
            return []
        return sorted(set.intersection(*timestamp_sets))

    def _max_drawdown(self, points: list[EquityPoint]) -> float:
        peak = points[0].equity_usd
        max_drawdown = 0.0
        for point in points:
            peak = max(peak, point.equity_usd)
            if peak <= 0:
                continue
            drawdown = (point.equity_usd / peak) - 1
            max_drawdown = min(max_drawdown, drawdown)
        return max_drawdown

    def _decision_row(self, decision: DecisionSnapshot) -> dict[str, object]:
        return {
            "timestamp": decision.timestamp,
            "regime_state": decision.regime_state,
            "risk_state": decision.risk_state,
            "exposure_fraction": decision.exposure_fraction,
            "is_frozen": decision.is_frozen,
            "freeze_reason": decision.freeze_reason,
            "target_weights_json": json.dumps(decision.target_weights, sort_keys=True),
            "scores_json": json.dumps(decision.scores, sort_keys=True),
            "asset_actions_json": json.dumps(decision.asset_actions, sort_keys=True),
            "asset_reasons_json": json.dumps(decision.asset_reasons, sort_keys=True),
        }

    def _performance_metrics(
        self,
        *,
        initial_cash: float,
        equity_curve: list[EquityPoint],
        fills: list[FillEvent],
    ) -> dict[str, object]:
        if not equity_curve:
            return {
                "period_years": None,
                "cagr": None,
                "calmar_ratio": None,
                "annualized_volatility": None,
                "daily_sharpe": None,
                "turnover": None,
                "fee_to_gross_pnl_ratio": None,
                "days_invested": None,
                "trades_per_year": None,
            }

        start_timestamp = equity_curve[0].timestamp
        end_timestamp = equity_curve[-1].timestamp
        elapsed_years = max((end_timestamp - start_timestamp) / 86_400 / 365.25, 0.0)
        total_return = (equity_curve[-1].equity_usd / initial_cash) - 1
        cagr = None
        if elapsed_years > 0:
            if equity_curve[-1].equity_usd > 0:
                cagr = (equity_curve[-1].equity_usd / initial_cash) ** (1 / elapsed_years) - 1
            else:
                cagr = -1.0

        daily_returns: list[float] = []
        for previous, current in zip(equity_curve[:-1], equity_curve[1:], strict=True):
            if previous.equity_usd <= 0:
                continue
            daily_returns.append((current.equity_usd / previous.equity_usd) - 1)

        annualized_volatility = None
        daily_sharpe = None
        if daily_returns:
            mean_return = sum(daily_returns) / len(daily_returns)
            variance = sum((value - mean_return) ** 2 for value in daily_returns) / len(
                daily_returns
            )
            standard_deviation = math.sqrt(variance)
            annualized_volatility = standard_deviation * math.sqrt(365)
            if standard_deviation > 0:
                daily_sharpe = (mean_return / standard_deviation) * math.sqrt(365)

        max_drawdown = self._max_drawdown(equity_curve)
        calmar_ratio = None
        if cagr is not None and max_drawdown < 0:
            calmar_ratio = cagr / abs(max_drawdown)

        gross_turnover = sum(fill.gross_notional_usd for fill in fills)
        gross_pnl_before_fees = equity_curve[-1].equity_usd - initial_cash + sum(
            fill.fee_paid_usd for fill in fills
        )
        fee_to_gross_pnl_ratio = None
        if gross_pnl_before_fees > 0:
            fee_to_gross_pnl_ratio = (
                sum(fill.fee_paid_usd for fill in fills) / gross_pnl_before_fees
            )

        trades_per_year = None
        if elapsed_years > 0:
            trades_per_year = len(fills) / elapsed_years

        return {
            "period_years": elapsed_years,
            "cagr": cagr,
            "calmar_ratio": calmar_ratio,
            "annualized_volatility": annualized_volatility,
            "daily_sharpe": daily_sharpe,
            "turnover": gross_turnover / initial_cash if initial_cash > 0 else None,
            "fee_to_gross_pnl_ratio": fee_to_gross_pnl_ratio,
            "days_invested": sum(1 for point in equity_curve if point.gross_exposure > 0),
            "trades_per_year": trades_per_year,
            "total_return": total_return,
        }

    def _liquidation_metrics(
        self,
        *,
        portfolio: PortfolioState,
        mark_bars: dict[str, Candle],
    ) -> dict[str, float]:
        fee_rate = self.config.backtest.fee_rate_bps / 10_000
        slippage_rate = self.config.backtest.slippage_bps / 10_000
        estimated_liquidation_fee_usd = 0.0
        estimated_liquidation_slippage_usd = 0.0
        net_liquidation_equity_usd = portfolio.cash_usd

        for asset, position in portfolio.positions.items():
            bar = mark_bars.get(asset)
            if bar is None or position.quantity <= 0:
                continue
            mark_notional = position.quantity * bar.close
            slippage_cost = max(mark_notional * slippage_rate, 0.0)
            gross_liquidation_notional = max(mark_notional - slippage_cost, 0.0)
            liquidation_fee = gross_liquidation_notional * fee_rate
            estimated_liquidation_slippage_usd += slippage_cost
            estimated_liquidation_fee_usd += liquidation_fee
            net_liquidation_equity_usd += gross_liquidation_notional - liquidation_fee

        return {
            "net_liquidation_equity_usd": net_liquidation_equity_usd,
            "net_liquidation_total_return": (
                (net_liquidation_equity_usd / self.config.backtest.initial_cash_usd) - 1
            ),
            "estimated_liquidation_fee_usd": estimated_liquidation_fee_usd,
            "estimated_liquidation_slippage_usd": estimated_liquidation_slippage_usd,
        }

    def _yearly_returns(
        self,
        equity_curve: list[EquityPoint],
        decisions: list[DecisionSnapshot],
        fills: list[FillEvent],
    ) -> dict[str, dict[str, object]]:
        yearly: dict[int, dict[str, object]] = {}
        decision_counts: dict[int, int] = defaultdict(int)
        fill_counts: dict[int, int] = defaultdict(int)
        for decision in decisions:
            year = datetime.fromtimestamp(decision.timestamp, tz=UTC).year
            decision_counts[year] += 1
        for fill in fills:
            year = datetime.fromtimestamp(fill.timestamp, tz=UTC).year
            fill_counts[year] += 1
        previous_equity: float | None = None
        for point in equity_curve:
            year = datetime.fromtimestamp(point.timestamp, tz=UTC).year
            start_equity = previous_equity if previous_equity is not None else point.equity_usd
            entry = yearly.setdefault(
                year,
                {
                    "start_equity_usd": start_equity,
                    "end_equity_usd": point.equity_usd,
                },
            )
            entry["end_equity_usd"] = point.equity_usd
            previous_equity = point.equity_usd

        payload: dict[str, dict[str, object]] = {}
        for year, entry in sorted(yearly.items()):
            start_equity = cast(float, entry["start_equity_usd"])
            end_equity = cast(float, entry["end_equity_usd"])
            profit_usd = end_equity - start_equity
            payload[str(year)] = {
                "start_equity_usd": start_equity,
                "end_equity_usd": end_equity,
                "profit_usd": profit_usd,
                "total_return": 0.0 if start_equity <= 0 else (end_equity / start_equity) - 1,
                "decision_count": decision_counts.get(year, 0),
                "fill_count": fill_counts.get(year, 0),
            }
        return payload

    def _benchmark_summary(
        self,
        *,
        bars_by_asset: dict[str, dict[int, Candle]],
        start_timestamp: int,
        end_timestamp: int,
    ) -> dict[str, dict[str, float | None]]:
        benchmarks: dict[str, dict[str, float | None]] = {
            "cash": {"total_return": 0.0, "cagr": 0.0},
        }
        period_years = max((end_timestamp - start_timestamp) / 86_400 / 365.25, 0.0)

        btc_bars = bars_by_asset.get("BTC", {})
        if (
            start_timestamp in btc_bars
            and end_timestamp in btc_bars
            and btc_bars[start_timestamp].close > 0
        ):
            btc_total_return = (
                btc_bars[end_timestamp].close / btc_bars[start_timestamp].close
            ) - 1
            benchmarks["btc_buy_and_hold"] = {
                "total_return": btc_total_return,
                "cagr": self._benchmark_cagr(btc_total_return, period_years),
            }
        else:
            benchmarks["btc_buy_and_hold"] = {"total_return": None, "cagr": None}

        dynamic_equal_weight_total_return = self._dynamic_equal_weight_total_return(
            bars_by_asset=bars_by_asset,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )
        if dynamic_equal_weight_total_return is not None:
            benchmarks["equal_weight_active_universe_buy_and_hold"] = {
                "total_return": dynamic_equal_weight_total_return,
                "cagr": self._benchmark_cagr(dynamic_equal_weight_total_return, period_years),
            }
        else:
            benchmarks["equal_weight_active_universe_buy_and_hold"] = {
                "total_return": None,
                "cagr": None,
            }
        return benchmarks

    def _dynamic_equal_weight_total_return(
        self,
        *,
        bars_by_asset: dict[str, dict[int, Candle]],
        start_timestamp: int,
        end_timestamp: int,
    ) -> float | None:
        timestamps = sorted(
            {
                timestamp
                for bars in bars_by_asset.values()
                for timestamp in bars
                if start_timestamp <= timestamp <= end_timestamp
            }
        )
        if len(timestamps) < 2:
            return None
        equity = 1.0
        for previous_timestamp, current_timestamp in zip(
            timestamps[:-1],
            timestamps[1:],
            strict=True,
        ):
            asset_returns: list[float] = []
            for bars in bars_by_asset.values():
                previous_bar = bars.get(previous_timestamp)
                current_bar = bars.get(current_timestamp)
                if previous_bar is None or current_bar is None or previous_bar.close <= 0:
                    continue
                asset_returns.append((current_bar.close / previous_bar.close) - 1)
            if not asset_returns:
                continue
            equity *= 1 + (sum(asset_returns) / len(asset_returns))
        return equity - 1

    def _decision_diagnostics(
        self,
        decisions: list[DecisionSnapshot],
    ) -> dict[str, object]:
        regime_counts: dict[str, int] = defaultdict(int)
        risk_counts: dict[str, int] = defaultdict(int)
        action_counts: dict[str, int] = defaultdict(int)
        reason_counts: dict[str, int] = defaultdict(int)
        targeted_asset_frequency: dict[str, int] = defaultdict(int)
        average_exposure_fraction = 0.0
        for decision in decisions:
            regime_counts[decision.regime_state] += 1
            risk_counts[decision.risk_state] += 1
            average_exposure_fraction += decision.exposure_fraction
            for action in decision.asset_actions.values():
                action_counts[action] += 1
            for reason in decision.asset_reasons.values():
                reason_counts[reason] += 1
            for asset, weight in decision.target_weights.items():
                if weight > 0:
                    targeted_asset_frequency[asset] += 1
        decision_count = len(decisions)
        return {
            "average_exposure_fraction": (
                average_exposure_fraction / decision_count if decision_count else 0.0
            ),
            "regime_distribution": dict(sorted(regime_counts.items())),
            "risk_distribution": dict(sorted(risk_counts.items())),
            "action_counts": dict(sorted(action_counts.items())),
            "reason_counts": dict(sorted(reason_counts.items())),
            "targeted_asset_frequency": {
                asset: count / decision_count
                for asset, count in sorted(targeted_asset_frequency.items())
            },
        }

    @staticmethod
    def _benchmark_cagr(total_return: float, period_years: float) -> float | None:
        if period_years <= 0:
            return None
        base = 1 + total_return
        if base <= 0:
            return -1.0
        return float(base ** (1 / period_years) - 1)

    def _load_simulation_state(self, path: Path) -> PortfolioState:
        if not path.exists():
            return PortfolioState(cash_usd=self.config.backtest.initial_cash_usd)
        payload = json.loads(path.read_text(encoding="utf-8"))
        positions = {
            asset: self._position_from_payload(position)
            for asset, position in payload.get("positions", {}).items()
        }
        return PortfolioState(
            cash_usd=float(payload.get("cash_usd", self.config.backtest.initial_cash_usd)),
            positions=positions,
            realized_pnl_usd=float(payload.get("realized_pnl_usd", 0.0)),
            fees_paid_usd=float(payload.get("fees_paid_usd", 0.0)),
            peak_equity_usd=payload.get("peak_equity_usd"),
            last_timestamp=payload.get("last_timestamp"),
            last_regime=payload.get("last_regime"),
            last_risk_state=payload.get("last_risk_state"),
            freeze_reason=payload.get("freeze_reason"),
        )

    def _write_simulation_state(self, path: Path, portfolio: PortfolioState) -> None:
        write_json(path, portfolio.to_dict())

    def _parse_feature_value(self, key: str, value: str) -> object:
        if key in {"asset", "regime_state"}:
            return value
        if key == "timestamp":
            return int(value)
        return float(value)

    def _position_from_payload(self, payload: dict[str, Any]) -> Any:
        from tradebot.backtest.models import PositionState

        return PositionState(
            asset=str(payload["asset"]),
            quantity=float(payload["quantity"]),
            average_entry_price=float(payload["average_entry_price"]),
        )

    @staticmethod
    def _holdings(portfolio: PortfolioState) -> dict[str, float]:
        return {
            asset: position.quantity
            for asset, position in sorted(portfolio.positions.items())
        }
