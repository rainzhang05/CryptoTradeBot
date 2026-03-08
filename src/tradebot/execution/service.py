"""Live execution orchestration for Phase 7 Kraken spot trading."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import sleep as default_sleep
from typing import Any, cast

from tradebot.backtest.execution import build_order_intents
from tradebot.backtest.models import DecisionSnapshot, FillEvent, PortfolioState, PositionState
from tradebot.config import AppConfig
from tradebot.constants import FIXED_UNIVERSE
from tradebot.data.service import DataService
from tradebot.data.storage import write_json
from tradebot.data.symbols import ASSET_SYMBOLS
from tradebot.execution.kraken import KrakenClient, KrakenClientError
from tradebot.execution.models import (
    KrakenOrderState,
    LiveCycleSummary,
    LiveState,
    PairMetadata,
)
from tradebot.execution.storage import latest_live_status_file, live_state_file
from tradebot.model.service import ModelService
from tradebot.research.service import ResearchService
from tradebot.strategy.service import StrategyEngine


@dataclass(frozen=True)
class _AccountSnapshot:
    cash_usd: float
    positions: dict[str, PositionState]
    open_orders: dict[str, KrakenOrderState]
    balances_raw: dict[str, float]


class LiveExecutionService:
    """Run one live Kraken decision and execution cycle against current account state."""

    def __init__(
        self,
        config: AppConfig,
        *,
        kraken_client: KrakenClient | None = None,
        data_service: DataService | None = None,
        research_service: ResearchService | None = None,
        model_service: ModelService | None = None,
        strategy_engine: StrategyEngine | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config
        self.paths = config.resolved_paths()
        self.data_settings = config.resolved_data_settings()
        self.kraken_client = kraken_client or KrakenClient(
            api_key=config.secrets.kraken_api_key,
            api_secret=config.secrets.kraken_api_secret,
            otp=config.secrets.kraken_api_otp,
        )
        self.data_service = data_service or DataService(config)
        self.research_service = research_service or ResearchService(config)
        self.model_service = model_service or ModelService(config)
        self.strategy_engine = strategy_engine or StrategyEngine(config)
        self.sleep_fn = sleep_fn or default_sleep

    def run_cycle(self, assets: tuple[str, ...] | None = None) -> LiveCycleSummary:
        """Run one live account-sync, decision, and execution cycle."""
        selected_assets = self._select_assets(assets)
        state_path = live_state_file(self.paths.state_dir)
        report_path = latest_live_status_file(self.paths.artifacts_dir)
        state = self._load_state(state_path)
        incidents = list(state.incidents[-10:])

        try:
            system_status_payload = self.kraken_client.get_system_status()
        except KrakenClientError as exc:
            return self._freeze_summary(
                state=state,
                state_path=state_path,
                report_path=report_path,
                freeze_reason=f"exchange_connectivity:{exc}",
                system_status="unreachable",
                incidents=incidents + [f"exchange_connectivity:{exc}"],
            )

        system_status = str(system_status_payload["status"])
        if system_status != "online":
            return self._freeze_summary(
                state=state,
                state_path=state_path,
                report_path=report_path,
                freeze_reason=f"exchange_status:{system_status}",
                system_status=system_status,
                incidents=incidents + [f"exchange_status:{system_status}"],
            )

        if self.config.runtime.live_dead_man_switch_seconds > 0:
            try:
                self.kraken_client.cancel_all_orders_after(
                    self.config.runtime.live_dead_man_switch_seconds
                )
            except KrakenClientError as exc:
                return self._freeze_summary(
                    state=state,
                    state_path=state_path,
                    report_path=report_path,
                    freeze_reason=f"dead_man_switch:{exc}",
                    system_status=system_status,
                    incidents=incidents + [f"dead_man_switch:{exc}"],
                )

        try:
            pair_metadata = self._pair_metadata_for_assets(selected_assets)
            account = self._sync_account_state(selected_assets)
        except KrakenClientError as exc:
            return self._freeze_summary(
                state=state,
                state_path=state_path,
                report_path=report_path,
                freeze_reason=f"account_sync:{exc}",
                system_status=system_status,
                incidents=incidents + [f"account_sync:{exc}"],
            )

        try:
            self.data_service.complete_canonical(assets=selected_assets, allow_synthetic=False)
            dataset_id, latest_timestamp, rows_for_timestamp = (
                self.research_service.build_live_signal_rows(assets=selected_assets)
            )
        except Exception as exc:
            return self._freeze_summary(
                state=state,
                state_path=state_path,
                report_path=report_path,
                freeze_reason=f"data_refresh:{exc}",
                system_status=system_status,
                incidents=incidents + [f"data_refresh:{exc}"],
                cash_usd=account.cash_usd,
                positions=account.positions,
                open_orders=account.open_orders,
                holdings=self._holdings(account.positions),
            )

        latest_closed_timestamp = self._latest_closed_timestamp()
        if latest_timestamp is None or latest_timestamp < latest_closed_timestamp:
            return self._freeze_summary(
                state=state,
                state_path=state_path,
                report_path=report_path,
                freeze_reason="stale_daily_signals",
                system_status=system_status,
                incidents=incidents + ["stale_daily_signals"],
                dataset_id=dataset_id,
                cash_usd=account.cash_usd,
                positions=account.positions,
                open_orders=account.open_orders,
                holdings=self._holdings(account.positions),
            )

        if any(
            self._feature_float(row, "latest_source_is_kraken") < 1.0
            for row in rows_for_timestamp.values()
        ):
            return self._freeze_summary(
                state=state,
                state_path=state_path,
                report_path=report_path,
                freeze_reason="latest_signal_not_kraken_native",
                system_status=system_status,
                incidents=incidents + ["latest_signal_not_kraken_native"],
                dataset_id=dataset_id,
                timestamp=latest_timestamp,
                cash_usd=account.cash_usd,
                positions=account.positions,
                open_orders=account.open_orders,
                holdings=self._holdings(account.positions),
            )

        active_reference = self.model_service.load_latest_active_reference()
        if active_reference is None:
            return self._freeze_summary(
                state=state,
                state_path=state_path,
                report_path=report_path,
                freeze_reason="missing_active_model",
                system_status=system_status,
                incidents=incidents + ["missing_active_model"],
                dataset_id=dataset_id,
                timestamp=latest_timestamp,
                cash_usd=account.cash_usd,
                positions=account.positions,
                open_orders=account.open_orders,
                holdings=self._holdings(account.positions),
            )

        rows_for_timestamp, model_id = self.model_service.infer_rows_with_active_model(
            rows_for_timestamp
        )
        if model_id is None or any(
            prediction_key not in row
            for row in rows_for_timestamp.values()
            for prediction_key in (
                "expected_return_score",
                "downside_risk_score",
                "sell_risk_score",
            )
        ):
            return self._freeze_summary(
                state=state,
                state_path=state_path,
                report_path=report_path,
                freeze_reason="missing_model_predictions",
                system_status=system_status,
                incidents=incidents + ["missing_model_predictions"],
                dataset_id=dataset_id,
                timestamp=latest_timestamp,
                cash_usd=account.cash_usd,
                positions=account.positions,
                open_orders=account.open_orders,
                holdings=self._holdings(account.positions),
            )

        prices_by_asset = self._prices_by_asset(selected_assets)
        if any(asset not in prices_by_asset for asset in selected_assets):
            return self._freeze_summary(
                state=state,
                state_path=state_path,
                report_path=report_path,
                freeze_reason="missing_live_price",
                system_status=system_status,
                incidents=incidents + ["missing_live_price"],
                dataset_id=dataset_id,
                timestamp=latest_timestamp,
                cash_usd=account.cash_usd,
                positions=account.positions,
                open_orders=account.open_orders,
                holdings=self._holdings(account.positions),
                model_id=model_id,
            )

        equity_usd = self._portfolio_equity(account.cash_usd, account.positions, prices_by_asset)
        if state.last_decision_timestamp == latest_timestamp:
            updated_state = LiveState(
                cash_usd=account.cash_usd,
                positions=account.positions,
                open_orders=account.open_orders,
                recent_fills=state.recent_fills,
                last_decision_timestamp=state.last_decision_timestamp,
                last_model_id=state.last_model_id,
                last_regime=state.last_regime,
                last_risk_state=state.last_risk_state,
                peak_equity_usd=max(state.peak_equity_usd or equity_usd, equity_usd),
                consecutive_order_failures=state.consecutive_order_failures,
                freeze_reason=None,
                incidents=incidents,
                last_synced_at=self._now_iso(),
            )
            return self._persist_summary(
                summary=LiveCycleSummary(
                    dataset_id=dataset_id,
                    timestamp=latest_timestamp,
                    status="monitoring",
                    system_status=system_status,
                    connectivity_state="online",
                    regime_state=updated_state.last_regime,
                    risk_state=updated_state.last_risk_state,
                    equity_usd=equity_usd,
                    cash_usd=account.cash_usd,
                    fill_count=0,
                    fills=[],
                    holdings=self._holdings(account.positions),
                    open_order_count=len(account.open_orders),
                    incidents=incidents,
                    state_file=str(state_path),
                    freeze_reason=None,
                    model_id=model_id,
                    decision_executed=False,
                ),
                state=updated_state,
                state_path=state_path,
                report_path=report_path,
            )

        if account.open_orders:
            try:
                for txid in tuple(account.open_orders):
                    self.kraken_client.cancel_order(txid)
                    incidents.append(f"replaced_open_order:{txid}")
                account = self._sync_account_state(selected_assets)
            except KrakenClientError as exc:
                return self._freeze_summary(
                    state=state,
                    state_path=state_path,
                    report_path=report_path,
                    freeze_reason=f"order_management:{exc}",
                    system_status=system_status,
                    incidents=incidents + [f"order_management:{exc}"],
                    dataset_id=dataset_id,
                    timestamp=latest_timestamp,
                    cash_usd=account.cash_usd,
                    positions=account.positions,
                    open_orders=account.open_orders,
                    holdings=self._holdings(account.positions),
                    model_id=model_id,
                )

        portfolio = PortfolioState(
            cash_usd=account.cash_usd,
            positions=account.positions,
            peak_equity_usd=state.peak_equity_usd or equity_usd,
        )
        strategy_decision = self.strategy_engine.evaluate(
            timestamp=latest_timestamp,
            rows_by_asset=rows_for_timestamp,
            portfolio=portfolio,
            prices_by_asset=prices_by_asset,
        )
        if strategy_decision.is_frozen:
            return self._freeze_summary(
                state=state,
                state_path=state_path,
                report_path=report_path,
                freeze_reason=strategy_decision.freeze_reason or "strategy_frozen",
                system_status=system_status,
                incidents=incidents + [strategy_decision.freeze_reason or "strategy_frozen"],
                dataset_id=dataset_id,
                timestamp=latest_timestamp,
                cash_usd=account.cash_usd,
                positions=account.positions,
                open_orders=account.open_orders,
                holdings=self._holdings(account.positions),
                model_id=model_id,
                regime_state=strategy_decision.regime_state,
                risk_state=strategy_decision.risk_state,
            )

        decision_snapshot = DecisionSnapshot(
            timestamp=latest_timestamp,
            regime_state=strategy_decision.regime_state,
            risk_state=strategy_decision.risk_state,
            exposure_fraction=strategy_decision.exposure_fraction,
            target_weights=strategy_decision.target_weights,
            scores=strategy_decision.scores,
            is_frozen=False,
            freeze_reason=None,
            asset_actions={
                asset: asset_decision.action
                for asset, asset_decision in strategy_decision.asset_decisions.items()
            },
            asset_reasons={
                asset: asset_decision.reason
                for asset, asset_decision in strategy_decision.asset_decisions.items()
            },
        )
        intents = build_order_intents(
            portfolio=portfolio,
            decision=decision_snapshot,
            reference_prices=prices_by_asset,
            settings=self.config.backtest,
            equity_usd=equity_usd,
        )

        fills: list[FillEvent] = []
        failures = state.consecutive_order_failures
        try:
            for intent in intents:
                metadata = pair_metadata[intent.asset]
                adjusted_volume = self._normalized_volume(
                    intent.quantity,
                    metadata=metadata,
                    reference_price=prices_by_asset[intent.asset],
                )
                if adjusted_volume is None:
                    incidents.append(f"skipped_small_order:{intent.asset}")
                    continue

                submission = self.kraken_client.add_market_order(
                    pair=metadata.altname,
                    side=intent.side,
                    volume=adjusted_volume,
                    userref=latest_timestamp,
                )
                order_state = self._wait_for_terminal_order(submission.txid)
                if order_state is None:
                    self.kraken_client.cancel_order(submission.txid)
                    failures += 1
                    incidents.append(f"order_timeout:{submission.txid}")
                    continue

                if order_state.executed_volume > 0:
                    fills.append(
                        self._fill_from_order(latest_timestamp, intent.asset, order_state)
                    )
                if order_state.status not in {"closed", "canceled", "expired"}:
                    failures += 1
                    incidents.append(
                        f"order_not_terminal:{submission.txid}:{order_state.status}"
                    )

            account_after = self._sync_account_state(selected_assets)
        except KrakenClientError as exc:
            return self._freeze_summary(
                state=state,
                state_path=state_path,
                report_path=report_path,
                freeze_reason=f"order_management:{exc}",
                system_status=system_status,
                incidents=incidents + [f"order_management:{exc}"],
                dataset_id=dataset_id,
                timestamp=latest_timestamp,
                cash_usd=account.cash_usd,
                positions=account.positions,
                open_orders=account.open_orders,
                holdings=self._holdings(account.positions),
                model_id=model_id,
                regime_state=strategy_decision.regime_state,
                risk_state=strategy_decision.risk_state,
            )
        if self._balances_mismatch(account.positions, account_after.positions, fills):
            failures += 1
            incidents.append("account_reconciliation_failed")

        freeze_reason = None
        status = "executed" if fills else "ok"
        if failures >= self.config.runtime.live_max_order_failures:
            freeze_reason = "order_failures_exceeded"
            status = "frozen"

        equity_after = self._portfolio_equity(
            account_after.cash_usd,
            account_after.positions,
            prices_by_asset,
        )
        updated_state = LiveState(
            cash_usd=account_after.cash_usd,
            positions=account_after.positions,
            open_orders=account_after.open_orders,
            recent_fills=(fills + state.recent_fills)[:10],
            last_decision_timestamp=latest_timestamp,
            last_model_id=model_id,
            last_regime=strategy_decision.regime_state,
            last_risk_state=strategy_decision.risk_state,
            peak_equity_usd=max(state.peak_equity_usd or equity_after, equity_after),
            consecutive_order_failures=failures if freeze_reason is not None else 0,
            freeze_reason=freeze_reason,
            incidents=incidents[-10:],
            last_synced_at=self._now_iso(),
        )
        summary = LiveCycleSummary(
            dataset_id=dataset_id,
            timestamp=latest_timestamp,
            status=status,
            system_status=system_status,
            connectivity_state="online",
            regime_state=strategy_decision.regime_state,
            risk_state=strategy_decision.risk_state,
            equity_usd=equity_after,
            cash_usd=account_after.cash_usd,
            fill_count=len(fills),
            fills=fills,
            holdings=self._holdings(account_after.positions),
            open_order_count=len(account_after.open_orders),
            incidents=updated_state.incidents,
            state_file=str(state_path),
            freeze_reason=freeze_reason,
            model_id=model_id,
            decision_executed=True,
        )
        return self._persist_summary(
            summary=summary,
            state=updated_state,
            state_path=state_path,
            report_path=report_path,
        )

    def _pair_metadata_for_assets(self, assets: tuple[str, ...]) -> dict[str, PairMetadata]:
        pair_names = [self._kraken_rest_pair(asset) for asset in assets]
        metadata = self.kraken_client.get_asset_pairs(pair_names)
        result: dict[str, PairMetadata] = {}
        for asset in assets:
            pair_name = self._kraken_rest_pair(asset)
            if pair_name not in metadata:
                raise KrakenClientError(f"Missing Kraken pair metadata for {asset} ({pair_name})")
            result[asset] = metadata[pair_name]
        return result

    def _sync_account_state(self, assets: tuple[str, ...]) -> _AccountSnapshot:
        balances_raw = self.kraken_client.get_balances()
        open_orders = self.kraken_client.get_open_orders()
        positions: dict[str, PositionState] = {}
        for asset in assets:
            quantity = self._balance_for_asset(asset, balances_raw)
            if quantity <= 0:
                continue
            positions[asset] = PositionState(
                asset=asset,
                quantity=quantity,
                average_entry_price=0.0,
            )
        return _AccountSnapshot(
            cash_usd=self._usd_balance(balances_raw),
            positions=positions,
            open_orders={
                txid: order
                for txid, order in open_orders.items()
                if self._asset_from_pair(order.pair) in assets
            },
            balances_raw=balances_raw,
        )

    def _load_state(self, path: Path) -> LiveState:
        if not path.exists():
            return LiveState(cash_usd=0.0)
        payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        positions = {
            asset: PositionState(
                asset=str(position["asset"]),
                quantity=float(position["quantity"]),
                average_entry_price=float(position["average_entry_price"]),
            )
            for asset, position in payload.get("positions", {}).items()
        }
        open_orders = {
            txid: KrakenOrderState(
                txid=str(order["txid"]),
                pair=str(order["pair"]),
                side=cast("Any", order["side"]),
                order_type=str(order["order_type"]),
                status=str(order["status"]),
                requested_volume=float(order["requested_volume"]),
                executed_volume=float(order["executed_volume"]),
                remaining_volume=float(order["remaining_volume"]),
                average_price=self._optional_float(order, "average_price"),
                cost_usd=self._optional_float(order, "cost_usd"),
                fee_usd=self._optional_float(order, "fee_usd"),
                opened_at=self._optional_float(order, "opened_at"),
                closed_at=self._optional_float(order, "closed_at"),
                limit_price=self._optional_float(order, "limit_price"),
                userref=(None if order.get("userref") is None else int(order["userref"])),
            )
            for txid, order in payload.get("open_orders", {}).items()
        }
        fills = [
            FillEvent(
                timestamp=int(fill["timestamp"]),
                asset=str(fill["asset"]),
                side=cast("Any", fill["side"]),
                quantity=float(fill["quantity"]),
                fill_price=float(fill["fill_price"]),
                gross_notional_usd=float(fill["gross_notional_usd"]),
                fee_paid_usd=float(fill["fee_paid_usd"]),
                realized_pnl_usd=float(fill["realized_pnl_usd"]),
            )
            for fill in payload.get("recent_fills", [])
        ]
        return LiveState(
            cash_usd=float(payload.get("cash_usd", 0.0)),
            positions=positions,
            open_orders=open_orders,
            recent_fills=fills,
            last_decision_timestamp=payload.get("last_decision_timestamp"),
            last_model_id=payload.get("last_model_id"),
            last_regime=payload.get("last_regime"),
            last_risk_state=payload.get("last_risk_state"),
            peak_equity_usd=payload.get("peak_equity_usd"),
            consecutive_order_failures=int(payload.get("consecutive_order_failures", 0)),
            freeze_reason=payload.get("freeze_reason"),
            incidents=[str(item) for item in payload.get("incidents", [])],
            last_synced_at=payload.get("last_synced_at"),
        )

    def _persist_summary(
        self,
        *,
        summary: LiveCycleSummary,
        state: LiveState,
        state_path: Path,
        report_path: Path,
    ) -> LiveCycleSummary:
        write_json(state_path, state.to_dict())
        write_json(report_path, summary.to_dict() | {"state": state.to_dict()})
        return summary

    def _freeze_summary(
        self,
        *,
        state: LiveState,
        state_path: Path,
        report_path: Path,
        freeze_reason: str,
        system_status: str,
        incidents: list[str],
        dataset_id: str | None = None,
        timestamp: int | None = None,
        cash_usd: float | None = None,
        positions: dict[str, PositionState] | None = None,
        open_orders: dict[str, KrakenOrderState] | None = None,
        holdings: dict[str, float] | None = None,
        model_id: str | None = None,
        regime_state: str | None = None,
        risk_state: str | None = None,
    ) -> LiveCycleSummary:
        frozen_state = LiveState(
            cash_usd=state.cash_usd if cash_usd is None else cash_usd,
            positions=state.positions if positions is None else positions,
            open_orders=state.open_orders if open_orders is None else open_orders,
            recent_fills=state.recent_fills,
            last_decision_timestamp=state.last_decision_timestamp,
            last_model_id=state.last_model_id if model_id is None else model_id,
            last_regime=state.last_regime if regime_state is None else regime_state,
            last_risk_state=state.last_risk_state if risk_state is None else risk_state,
            peak_equity_usd=state.peak_equity_usd,
            consecutive_order_failures=state.consecutive_order_failures,
            freeze_reason=freeze_reason,
            incidents=incidents[-10:],
            last_synced_at=self._now_iso(),
        )
        summary = LiveCycleSummary(
            dataset_id=dataset_id,
            timestamp=timestamp,
            status="frozen",
            system_status=system_status,
            connectivity_state="degraded",
            regime_state=regime_state,
            risk_state=risk_state,
            equity_usd=state.cash_usd if cash_usd is None else cash_usd,
            cash_usd=state.cash_usd if cash_usd is None else cash_usd,
            fill_count=0,
            fills=[],
            holdings=holdings or self._holdings(frozen_state.positions),
            open_order_count=len(frozen_state.open_orders),
            incidents=frozen_state.incidents,
            state_file=str(state_path),
            freeze_reason=freeze_reason,
            model_id=model_id,
            decision_executed=False,
        )
        return self._persist_summary(
            summary=summary,
            state=frozen_state,
            state_path=state_path,
            report_path=report_path,
        )

    def _prices_by_asset(self, assets: tuple[str, ...]) -> dict[str, float]:
        pairs = [self._kraken_rest_pair(asset) for asset in assets]
        price_payload = self.kraken_client.get_ticker(pairs)
        return {
            asset: price_payload[self._kraken_rest_pair(asset)]
            for asset in assets
            if self._kraken_rest_pair(asset) in price_payload
        }

    def _wait_for_terminal_order(self, txid: str) -> KrakenOrderState | None:
        timeout_seconds = self.config.runtime.live_order_timeout_seconds
        poll_seconds = self.config.runtime.live_order_poll_seconds
        attempts = max(
            int(timeout_seconds / poll_seconds),
            1,
        )
        for attempt in range(attempts):
            order_state = self.kraken_client.query_orders([txid]).get(txid)
            if order_state is None:
                return None
            if order_state.status in {"closed", "canceled", "expired"}:
                return order_state
            if attempt < attempts - 1:
                self.sleep_fn(poll_seconds)
        return None

    def _normalized_volume(
        self,
        quantity: float,
        *,
        metadata: PairMetadata,
        reference_price: float,
    ) -> float | None:
        precision = metadata.lot_decimals
        factor = 10**precision
        rounded = int(quantity * factor) / factor
        if rounded <= 0:
            return None
        minimum_quantity = metadata.ordermin or 0.0
        if rounded < minimum_quantity:
            return None
        minimum_cost = max(metadata.costmin or 0.0, self.config.backtest.min_order_notional_usd)
        if rounded * reference_price < minimum_cost:
            return None
        return float(rounded)

    def _fill_from_order(
        self,
        timestamp: int,
        asset: str,
        order_state: KrakenOrderState,
    ) -> FillEvent:
        fill_price = order_state.average_price or 0.0
        gross_notional = order_state.cost_usd or (fill_price * order_state.executed_volume)
        return FillEvent(
            timestamp=timestamp,
            asset=asset,
            side=order_state.side,
            quantity=order_state.executed_volume,
            fill_price=fill_price,
            gross_notional_usd=gross_notional,
            fee_paid_usd=order_state.fee_usd or 0.0,
            realized_pnl_usd=0.0,
        )

    def _balances_mismatch(
        self,
        before_positions: dict[str, PositionState],
        after_positions: dict[str, PositionState],
        fills: list[FillEvent],
    ) -> bool:
        expected_delta: dict[str, float] = {}
        for fill in fills:
            delta = fill.quantity if fill.side == "buy" else -fill.quantity
            expected_delta[fill.asset] = expected_delta.get(fill.asset, 0.0) + delta

        tolerance = 1e-8
        for asset, delta in expected_delta.items():
            before_quantity = before_positions.get(asset, PositionState(asset, 0.0, 0.0)).quantity
            after_quantity = after_positions.get(asset, PositionState(asset, 0.0, 0.0)).quantity
            if abs((after_quantity - before_quantity) - delta) > tolerance:
                return True
        return False

    @staticmethod
    def _holdings(positions: dict[str, PositionState]) -> dict[str, float]:
        return {asset: position.quantity for asset, position in positions.items()}

    @staticmethod
    def _portfolio_equity(
        cash_usd: float,
        positions: dict[str, PositionState],
        prices_by_asset: dict[str, float],
    ) -> float:
        return cash_usd + sum(
            position.quantity * prices_by_asset.get(asset, 0.0)
            for asset, position in positions.items()
        )

    @staticmethod
    def _feature_float(row: dict[str, object], key: str) -> float:
        value = row.get(key, 0.0)
        if isinstance(value, int | float):
            return float(value)
        return float(str(value))

    @staticmethod
    def _optional_float(payload: dict[str, Any], key: str) -> float | None:
        value = payload.get(key)
        return None if value is None else float(value)

    @staticmethod
    def _select_assets(assets: tuple[str, ...] | None) -> tuple[str, ...]:
        selected_assets = assets or FIXED_UNIVERSE
        invalid_assets = [asset for asset in selected_assets if asset not in FIXED_UNIVERSE]
        if invalid_assets:
            joined = ", ".join(sorted(invalid_assets))
            raise ValueError(f"Assets outside the fixed V1 universe are not allowed: {joined}")
        if "BTC" not in selected_assets:
            raise ValueError("Live execution requires BTC for regime classification")
        return selected_assets

    @staticmethod
    def _asset_from_pair(pair: str) -> str | None:
        normalized = pair.replace("/", "").upper()
        for asset, symbol in ASSET_SYMBOLS.items():
            candidates = {
                symbol.kraken_raw_file.removesuffix(".csv").upper(),
                symbol.kraken_pair.replace("/", "").upper(),
            }
            if normalized in candidates:
                return asset
        return None

    @staticmethod
    def _kraken_rest_pair(asset: str) -> str:
        return ASSET_SYMBOLS[asset].kraken_raw_file.removesuffix(".csv")

    @staticmethod
    def _usd_balance(balances_raw: dict[str, float]) -> float:
        return sum(balances_raw.get(code, 0.0) for code in ("ZUSD", "USD"))

    @staticmethod
    def _balance_for_asset(asset: str, balances_raw: dict[str, float]) -> float:
        aliases: dict[str, tuple[str, ...]] = {
            "BTC": ("XXBT", "XBT", "BTC"),
            "ETH": ("XETH", "ETH"),
            "BNB": ("BNB",),
            "XRP": ("XXRP", "XRP"),
            "SOL": ("SOL",),
            "ADA": ("ADA",),
            "DOGE": ("XDG", "XXDG", "DOGE"),
            "TRX": ("TRX",),
            "AVAX": ("AVAX",),
            "LINK": ("LINK",),
        }
        return sum(balances_raw.get(alias, 0.0) for alias in aliases.get(asset, (asset,)))

    @staticmethod
    def _latest_closed_timestamp() -> int:
        step = 86_400
        return (int(datetime.now(tz=UTC).timestamp()) // step) * step - step

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(tz=UTC).isoformat()
