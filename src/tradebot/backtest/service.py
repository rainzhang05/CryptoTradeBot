"""Backtest orchestration and simulate-mode execution service."""

from __future__ import annotations

import csv
import json
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
from tradebot.config import AppConfig
from tradebot.data.integrity import read_candles
from tradebot.data.models import Candle
from tradebot.data.storage import canonical_candle_file, write_json
from tradebot.logging_config import get_logger
from tradebot.model.service import ModelService
from tradebot.research.service import ResearchService
from tradebot.strategy.service import StrategyEngine


class BacktestService:
    """Run deterministic backtests and latest-snapshot simulation cycles."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.paths = config.resolved_paths()
        self.data_settings = config.resolved_data_settings()
        self.logger = get_logger("tradebot.backtest")
        self.model_service = ModelService(config)
        self.research_service = ResearchService(config)
        self.strategy_engine = StrategyEngine(config)

    def run_backtest(
        self,
        assets: tuple[str, ...] | None = None,
        force_features: bool = False,
        cancellation_token: CancellationToken | None = None,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> BacktestRunSummary:
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        self.logger.info(
            "backtest started",
            extra={"assets": list(assets or ()), "force_features": force_features},
        )
        feature_store = self.research_service.build_feature_store(
            assets=assets,
            force=force_features,
            cancellation_token=cancellation_token,
        )
        if feature_store.row_count <= 0:
            raise ValueError("Feature store does not contain enough rows for backtesting")

        rows = self._load_feature_rows(Path(feature_store.dataset_file))
        rows_by_timestamp = self._rows_by_timestamp(rows)
        active_model_id: str | None = None
        selected_assets = tuple(feature_store.selected_assets)
        bars_by_asset = self._load_daily_bars(selected_assets)
        aligned_timestamps = self._common_timestamps(bars_by_asset)
        next_timestamp_map = {
            aligned_timestamps[index]: aligned_timestamps[index + 1]
            for index in range(len(aligned_timestamps) - 1)
        }

        portfolio = PortfolioState(cash_usd=self.config.backtest.initial_cash_usd)
        fills: list[FillEvent] = []
        decisions: list[DecisionSnapshot] = []
        equity_curve: list[EquityPoint] = []

        for timestamp in sorted(rows_by_timestamp):
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            execution_timestamp = next_timestamp_map.get(timestamp)
            if execution_timestamp is None:
                continue
            rows_for_timestamp = rows_by_timestamp[timestamp]
            rows_for_timestamp, active_model_id = (
                self.model_service.enrich_rows_with_active_predictions(
                dataset_id=feature_store.dataset_id,
                rows_by_asset=rows_for_timestamp,
                timestamp=timestamp,
                )
            )
            signal_bars = self._bars_at_timestamp(bars_by_asset, timestamp)
            execution_bars = self._bars_at_timestamp(bars_by_asset, execution_timestamp)
            if (
                len(execution_bars) != len(selected_assets)
                or len(signal_bars) != len(selected_assets)
            ):
                continue

            strategy_decision = self.strategy_engine.evaluate(
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

        if not equity_curve:
            raise ValueError("Backtest did not produce any executable decision points")

        run_id = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
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
        summary = BacktestRunSummary(
            run_id=run_id,
            report_file=str(report_path),
            fills_file=str(fills_path),
            equity_curve_file=str(equity_path),
            decisions_file=str(decisions_path),
            dataset_id=feature_store.dataset_id,
            decision_count=len(decisions),
            fill_count=len(fills),
            final_equity_usd=final_equity,
            total_return=(final_equity / self.config.backtest.initial_cash_usd) - 1,
            max_drawdown=max_drawdown,
            total_fees_usd=portfolio.fees_paid_usd,
        )
        payload = summary.to_dict() | {
            "portfolio": portfolio.to_dict(),
            "dataset_file": feature_store.dataset_file,
            "model_id": active_model_id,
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
    ) -> SimulationCycleSummary:
        state_path = simulate_state_file(self.paths.state_dir)
        portfolio = self._load_simulation_state(state_path)

        try:
            feature_store = self.research_service.build_feature_store(
                assets=assets,
                force=force_features,
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
        rows_for_timestamp, active_model_id = (
            self.model_service.enrich_rows_with_active_predictions(
            dataset_id=feature_store.dataset_id,
            rows_by_asset=rows_for_timestamp,
            timestamp=latest_timestamp,
            )
        )
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
            model_id=active_model_id,
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
            predictions=self._prediction_summary(rows_for_timestamp),
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

    @staticmethod
    def _prediction_summary(
        rows_by_asset: dict[str, dict[str, object]],
    ) -> dict[str, dict[str, float]]:
        summary: dict[str, dict[str, float]] = {}
        for asset, row in sorted(rows_by_asset.items()):
            keys = (
                "expected_return_score",
                "downside_risk_score",
                "sell_risk_score",
            )
            if not all(key in row for key in keys):
                continue
            summary[asset] = {
                key: float(row[key])  # type: ignore[arg-type]
                for key in keys
            }
        return summary
