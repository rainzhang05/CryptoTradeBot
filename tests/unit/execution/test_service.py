"""Unit tests for the Phase 7 live execution service."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tradebot.config import load_config
from tradebot.data.models import Candle
from tradebot.data.storage import write_candles
from tradebot.execution.kraken import KrakenClientError
from tradebot.execution.models import KrakenOrderState, OrderSubmission, PairMetadata
from tradebot.execution.service import LiveExecutionService
from tradebot.model.service import ModelService
from tradebot.research.service import ResearchService
from tradebot.strategy.service import StrategyEngine

LATEST_TEST_TIMESTAMP = 1_704_067_200 + 11 * 86_400


@pytest.fixture(autouse=True)
def _stub_promotion_backtest_comparison(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ModelService,
        "_promotion_backtest_comparison",
        lambda self, *, model_id, assets, dataset_track: {
            "hybrid": SimpleNamespace(run_id="hybrid-run", total_return=0.02),
            "rule_only": SimpleNamespace(run_id="rule-only-run", total_return=0.01),
            "incremental_total_return": 0.01,
            "hybrid_cagr": 0.03,
            "rule_only_cagr": 0.02,
            "yearly_win_rate": 1.0,
            "max_drawdown_gap": 0.01,
        },
    )


def _write_config(root: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app:
  log_format: console
runtime:
  default_mode: simulate
  max_cycles: 1
  live_order_poll_seconds: 0.01
  live_order_timeout_seconds: 0.03
  live_dead_man_switch_seconds: 60
  live_max_order_failures: 2
exchange: {}
data:
  canonical_dir: data/canonical
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
research:
  primary_interval: 1d
  momentum_windows_days: [2]
  trend_windows_days: [2, 4]
  volatility_windows_days: [2]
  relative_strength_window_days: 2
  breadth_window_days: 2
  dollar_volume_window_days: 2
  source_window_days: 2
  forward_return_days: 1
  downside_lookahead_days: 2
  downside_threshold: 0.05
  sell_lookahead_days: 3
  sell_drawdown_threshold: 0.08
  sell_return_threshold: -0.01
model:
  initial_train_timestamps: 2
  minimum_validation_rows: 1
  minimum_walk_forward_splits: 1
  promotion_min_expected_return_correlation: -1.0
  promotion_max_downside_brier: 1.0
  promotion_max_sell_brier: 1.0
backtest:
  initial_cash_usd: 1000.0
  fee_rate_bps: 0.0
  slippage_bps: 0.0
  max_positions: 2
  max_asset_weight: 0.35
  min_order_notional_usd: 10.0
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    return config_path


def _write_daily_series(root: Path, asset: str, closes: list[float], lows: list[float]) -> None:
    path = root / "data" / "canonical" / "kraken" / asset / "candles_1d.csv"
    candles = [
        Candle(
            timestamp=1_704_067_200 + index * 86_400,
            open=close - 0.5,
            high=close + 1.0,
            low=lows[index],
            close=close,
            volume=1_000.0 + index * 20,
            trade_count=100 + index,
            source="kraken_api",
        )
        for index, close in enumerate(closes)
    ]
    write_candles(path, candles)


class FakeDataService:
    def complete_canonical(
        self,
        assets: tuple[str, ...],
        allow_synthetic: bool,
    ) -> dict[str, object]:
        return {"assets": list(assets), "allow_synthetic": allow_synthetic}


class FakeKrakenClient:
    def __init__(self) -> None:
        self.usd_balance = 1_000.0
        self.asset_balances = {"BTC": 0.0, "ETH": 0.0}
        self.submissions: list[tuple[str, str, float]] = []
        self.cancelled: list[str] = []
        self.open_orders: dict[str, KrakenOrderState] = {}
        self.dead_man_switch_calls = 0

    def get_system_status(self) -> dict[str, str | None]:
        return {"status": "online", "timestamp": "2026-03-08T00:00:00Z", "message": None}

    def cancel_all_orders_after(self, timeout_seconds: int) -> dict[str, str | None]:
        self.dead_man_switch_calls += 1
        return {"current_time": "2026-03-08T00:00:00Z", "trigger_time": "2026-03-08T00:01:00Z"}

    def get_asset_pairs(self, pairs: list[str]) -> dict[str, PairMetadata]:
        return {
            pair: PairMetadata(
                pair=pair,
                altname=pair,
                wsname=f"{pair[:-3]}/USD",
                status="online",
                lot_decimals=8,
                ordermin=0.0001,
                costmin=10.0,
            )
            for pair in pairs
        }

    def get_balances(self) -> dict[str, float]:
        return {
            "ZUSD": self.usd_balance,
            "XXBT": self.asset_balances["BTC"],
            "XETH": self.asset_balances["ETH"],
        }

    def get_open_orders(self) -> dict[str, KrakenOrderState]:
        return dict(self.open_orders)

    def get_ticker(self, pairs: list[str]) -> dict[str, float]:
        return {"XBTUSD": 100.0, "ETHUSD": 50.0}

    def add_market_order(
        self,
        *,
        pair: str,
        side: str,
        volume: float,
        validate: bool = False,
        userref: int | None = None,
    ) -> OrderSubmission:
        del validate, userref
        txid = f"OID{len(self.submissions) + 1}"
        self.submissions.append((pair, side, volume))
        return OrderSubmission(txid=txid, description=f"{side} {volume} {pair} @ market")

    def query_orders(self, txids: list[str]) -> dict[str, KrakenOrderState]:
        results: dict[str, KrakenOrderState] = {}
        for txid in txids:
            pair, side, volume = self.submissions[int(txid.removeprefix("OID")) - 1]
            price = 100.0 if pair == "XBTUSD" else 50.0
            cost = volume * price
            if side == "buy":
                self.usd_balance -= cost
                if pair == "XBTUSD":
                    self.asset_balances["BTC"] += volume
                else:
                    self.asset_balances["ETH"] += volume
            results[txid] = KrakenOrderState(
                txid=txid,
                pair=pair,
                side=side,
                order_type="market",
                status="closed",
                requested_volume=volume,
                executed_volume=volume,
                remaining_volume=0.0,
                average_price=price,
                cost_usd=cost,
                fee_usd=0.0,
                opened_at=1_700_000_000.0,
                closed_at=1_700_000_001.0,
            )
        return results

    def cancel_order(self, txid: str) -> int:
        self.cancelled.append(txid)
        self.open_orders.pop(txid, None)
        return 1


def _prepare_promoted_model(tmp_path: Path) -> tuple[object, ResearchService, ModelService]:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    _write_daily_series(
        tmp_path,
        "BTC",
        [100, 101, 103, 106, 108, 111, 114, 118, 121, 125, 128, 132],
        [99, 100, 102, 105, 107, 110, 113, 117, 120, 124, 127, 131],
    )
    _write_daily_series(
        tmp_path,
        "ETH",
        [50, 51, 52, 53, 55, 58, 60, 63, 65, 68, 70, 73],
        [49, 50, 51, 52, 54, 57, 59, 62, 64, 67, 69, 72],
    )
    research_service = ResearchService(config)
    model_service = ModelService(config)
    training = model_service.train_model(assets=("BTC", "ETH"))
    model_service.promote_model(training.model_id)
    return config, research_service, model_service


def test_live_service_places_orders_and_persists_state(tmp_path: Path, monkeypatch) -> None:
    config, research_service, model_service = _prepare_promoted_model(tmp_path)
    fake_client = FakeKrakenClient()
    service = LiveExecutionService(
        config,
        kraken_client=fake_client,
        data_service=FakeDataService(),
        research_service=research_service,
        model_service=model_service,
        strategy_engine=StrategyEngine(config),
        sleep_fn=lambda _: None,
    )
    monkeypatch.setattr(service, "_latest_closed_timestamp", lambda: LATEST_TEST_TIMESTAMP)

    summary = service.run_cycle(assets=("BTC", "ETH"))

    assert summary.status == "executed"
    assert summary.fill_count >= 1
    assert fake_client.submissions
    assert Path(summary.state_file).exists()


def test_live_service_skips_repeated_decision_timestamp(tmp_path: Path, monkeypatch) -> None:
    config, research_service, model_service = _prepare_promoted_model(tmp_path)
    fake_client = FakeKrakenClient()
    service = LiveExecutionService(
        config,
        kraken_client=fake_client,
        data_service=FakeDataService(),
        research_service=research_service,
        model_service=model_service,
        strategy_engine=StrategyEngine(config),
        sleep_fn=lambda _: None,
    )
    monkeypatch.setattr(service, "_latest_closed_timestamp", lambda: LATEST_TEST_TIMESTAMP)

    first = service.run_cycle(assets=("BTC", "ETH"))
    second = service.run_cycle(assets=("BTC", "ETH"))

    assert first.status == "executed"
    assert second.status == "monitoring"
    assert len(fake_client.submissions) == first.fill_count


def test_live_service_freezes_without_active_model(tmp_path: Path, monkeypatch) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    _write_daily_series(
        tmp_path,
        "BTC",
        [100, 101, 103, 106, 108, 111, 114, 118, 121, 125, 128, 132],
        [99, 100, 102, 105, 107, 110, 113, 117, 120, 124, 127, 131],
    )
    _write_daily_series(
        tmp_path,
        "ETH",
        [50, 51, 52, 53, 55, 58, 60, 63, 65, 68, 70, 73],
        [49, 50, 51, 52, 54, 57, 59, 62, 64, 67, 69, 72],
    )
    research_service = ResearchService(config)
    model_service = ModelService(config)
    model_service.train_model(assets=("BTC", "ETH"))

    service = LiveExecutionService(
        config,
        kraken_client=FakeKrakenClient(),
        data_service=FakeDataService(),
        research_service=research_service,
        model_service=model_service,
        strategy_engine=StrategyEngine(config),
        sleep_fn=lambda _: None,
    )
    monkeypatch.setattr(service, "_latest_closed_timestamp", lambda: LATEST_TEST_TIMESTAMP)

    summary = service.run_cycle(assets=("BTC", "ETH"))

    assert summary.status == "frozen"
    assert summary.freeze_reason == "missing_active_model"


def test_live_service_cancels_stale_open_orders_before_new_decision(
    tmp_path: Path, monkeypatch
) -> None:
    config, research_service, model_service = _prepare_promoted_model(tmp_path)
    fake_client = FakeKrakenClient()
    fake_client.open_orders["OLD1"] = KrakenOrderState(
        txid="OLD1",
        pair="XBTUSD",
        side="buy",
        order_type="market",
        status="open",
        requested_volume=0.1,
        executed_volume=0.0,
        remaining_volume=0.1,
        average_price=None,
        cost_usd=None,
        fee_usd=None,
        opened_at=1_700_000_000.0,
        closed_at=None,
    )
    state_path = tmp_path / "runtime" / "state" / "live_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        """
{
  "cash_usd": 1000.0,
  "positions": {},
  "open_orders": {},
  "recent_fills": [],
  "last_decision_timestamp": 1704067200,
  "peak_equity_usd": 1000.0,
  "consecutive_order_failures": 0,
  "incidents": []
}
""".strip(),
        encoding="utf-8",
    )
    service = LiveExecutionService(
        config,
        kraken_client=fake_client,
        data_service=FakeDataService(),
        research_service=research_service,
        model_service=model_service,
        strategy_engine=StrategyEngine(config),
        sleep_fn=lambda _: None,
    )
    monkeypatch.setattr(service, "_latest_closed_timestamp", lambda: LATEST_TEST_TIMESTAMP)

    summary = service.run_cycle(assets=("BTC", "ETH"))

    assert summary.status == "executed"
    assert fake_client.cancelled == ["OLD1"]


def test_live_service_freezes_on_order_management_failures(
    tmp_path: Path, monkeypatch
) -> None:
    config, research_service, model_service = _prepare_promoted_model(tmp_path)

    class FailingOrderKrakenClient(FakeKrakenClient):
        def add_market_order(
            self,
            *,
            pair: str,
            side: str,
            volume: float,
            validate: bool = False,
            userref: int | None = None,
        ) -> OrderSubmission:
            del pair, side, volume, validate, userref
            raise KrakenClientError("submit_failed")

    service = LiveExecutionService(
        config,
        kraken_client=FailingOrderKrakenClient(),
        data_service=FakeDataService(),
        research_service=research_service,
        model_service=model_service,
        strategy_engine=StrategyEngine(config),
        sleep_fn=lambda _: None,
    )
    monkeypatch.setattr(service, "_latest_closed_timestamp", lambda: LATEST_TEST_TIMESTAMP)

    summary = service.run_cycle(assets=("BTC", "ETH"))

    assert summary.status == "frozen"
    assert summary.freeze_reason == "order_management:submit_failed"
