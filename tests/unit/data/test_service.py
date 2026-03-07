"""Unit tests for data service import behavior."""

import shutil
from pathlib import Path

import httpx

from tradebot.config import load_config
from tradebot.data.clients import BinancePublicClient, CoinbasePublicClient, KrakenPublicClient
from tradebot.data.models import Candle
from tradebot.data.service import DataService
from tradebot.data.storage import write_candles


def test_import_kraken_raw_creates_canonical_files(tmp_path: Path) -> None:
    raw_dir = tmp_path / "data" / "kraken_data"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fixture_dir = Path(__file__).parents[2] / "fixtures" / "raw" / "kraken"
    shutil.copy(fixture_dir / "XBTUSD.csv", raw_dir / "XBTUSD.csv")
    shutil.copy(fixture_dir / "ETHUSD.csv", raw_dir / "ETHUSD.csv")

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime: {}
exchange: {}
data:
  raw_kraken_dir: data/kraken_data
  canonical_dir: data/canonical
  reports_dir: artifacts/reports/data
  intervals: [1h, 1d]
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    config = load_config(config_path=config_path, env_path=tmp_path / ".env")

    summary = DataService(config).import_kraken_raw(assets=("BTC", "ETH"))

    assert len(summary.assets) == 2
    assert (tmp_path / "data" / "canonical" / "kraken" / "BTC" / "candles_1h.csv").exists()
    assert (tmp_path / "data" / "canonical" / "kraken" / "ETH" / "manifest.json").exists()
    assert Path(summary.report_file).exists()


def test_sync_canonical_uses_fallback_when_kraken_window_is_short(tmp_path: Path) -> None:
    raw_dir = tmp_path / "data" / "kraken_data"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fixture_dir = Path(__file__).parents[2] / "fixtures" / "raw" / "kraken"
    shutil.copy(fixture_dir / "XBTUSD.csv", raw_dir / "XBTUSD.csv")

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime: {}
exchange: {}
data:
  raw_kraken_dir: data/kraken_data
  canonical_dir: data/canonical
  reports_dir: artifacts/reports/data
  intervals: [1h]
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    config = load_config(config_path=config_path, env_path=tmp_path / ".env")
    service = DataService(config)
    service.import_kraken_raw(assets=("BTC",))

    canonical_file = tmp_path / "data" / "canonical" / "kraken" / "BTC" / "candles_1h.csv"
    original_lines = canonical_file.read_text(encoding="utf-8").splitlines()
    original_last_timestamp = int(original_lines[-1].split(",")[0])

    def kraken_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "error": [],
                "result": {
                    "XXBTZUSD": [
                        [
                            original_last_timestamp + 7200,
                            "46000",
                            "46500",
                            "45500",
                            "46200",
                            "0",
                            "12",
                            2,
                        ],
                        [
                            original_last_timestamp + 10800,
                            "46200",
                            "47000",
                            "46000",
                            "46800",
                            "0",
                            "11",
                            3,
                        ],
                    ],
                    "last": original_last_timestamp + 14400,
                },
            },
        )

    def binance_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                [
                    (original_last_timestamp + 3600) * 1000,
                    "45500",
                    "45800",
                    "45400",
                    "45700",
                    "9",
                    (original_last_timestamp + 7199) * 1000,
                    "0",
                    4,
                    "0",
                    "0",
                    "0",
                ]
            ],
        )

    config = load_config(config_path=config_path, env_path=tmp_path / ".env")
    sync_service = DataService(
        config,
        kraken_client=KrakenPublicClient(
            client=httpx.Client(
                transport=httpx.MockTransport(kraken_handler),
                base_url="https://api.kraken.com",
            )
        ),
        binance_client=BinancePublicClient(
            client=httpx.Client(
                transport=httpx.MockTransport(binance_handler),
                base_url="https://api.binance.com",
            )
        ),
        coinbase_client=CoinbasePublicClient(
            client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
                base_url="https://api.exchange.coinbase.com",
            )
        ),
    )

    summary = sync_service.sync_canonical(assets=("BTC",))
    lines = canonical_file.read_text(encoding="utf-8").splitlines()

    assert summary["assets"][0]["intervals"][0]["fallback_source"] == "binance"
    assert len(lines) == len(original_lines) + 2


def test_prune_raw_kraken_keeps_only_fixed_universe_files(tmp_path: Path) -> None:
        raw_dir = tmp_path / "data" / "kraken_data"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "XBTUSD.csv").write_text("1704067200,1,1\n", encoding="utf-8")
        (raw_dir / "ETHUSD.csv").write_text("1704067200,1,1\n", encoding="utf-8")
        (raw_dir / "ALGOUSD.csv").write_text("1704067200,1,1\n", encoding="utf-8")

        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "settings.yaml"
        config_path.write_text(
                """
app: {}
runtime: {}
exchange: {}
data:
    raw_kraken_dir: data/kraken_data
    canonical_dir: data/canonical
    reports_dir: artifacts/reports/data
    intervals: [1h, 1d]
strategy:
    fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
                encoding="utf-8",
        )
        config = load_config(config_path=config_path, env_path=tmp_path / ".env")

        summary = DataService(config).prune_raw_kraken()

        assert summary["deleted_count"] == 1
        assert not (raw_dir / "ALGOUSD.csv").exists()
        assert (raw_dir / "XBTUSD.csv").exists()


def test_complete_canonical_fills_historical_and_tail_gaps(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime: {}
exchange: {}
data:
  raw_kraken_dir: data/kraken_data
  canonical_dir: data/canonical
  reports_dir: artifacts/reports/data
  intervals: [1h]
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    config = load_config(config_path=config_path, env_path=tmp_path / ".env")

    candle_path = tmp_path / "data" / "canonical" / "kraken" / "BTC" / "candles_1h.csv"
    write_candles(
        candle_path,
        [
            Candle(1704067200, 100, 101, 99, 100, 10, 5, "kraken_raw"),
            Candle(1704074400, 102, 103, 101, 102, 12, 6, "kraken_raw"),
        ],
    )

    class FakeKrakenClient:
        def fetch_ohlc_range(
            self,
            pair: str,
            interval: str,
            start_ts: int,
            end_ts: int,
        ) -> list[Candle]:
            rows = {
                1704070800: Candle(1704070800, 101, 102, 100, 101, 11, 4, "kraken_api"),
            }
            return [candle for ts, candle in rows.items() if start_ts <= ts <= end_ts]

    class FakeBinanceClient:
        def fetch_klines(
            self,
            symbol: str,
            interval: str,
            start_ts: int,
            end_ts: int,
        ) -> list[Candle]:
            rows = {
                1704078000: Candle(1704078000, 103, 104, 102, 103, 13, 7, "binance_fallback"),
            }
            return [candle for ts, candle in rows.items() if start_ts <= ts <= end_ts]

    class FakeCoinbaseClient:
        def fetch_candles(
            self,
            product_id: str,
            interval: str,
            start_ts: int,
            end_ts: int,
        ) -> list[Candle]:
            return []

    service = DataService(
        config,
        kraken_client=FakeKrakenClient(),
        binance_client=FakeBinanceClient(),
        coinbase_client=FakeCoinbaseClient(),
    )
    monkeypatch.setattr(service, "_latest_closed_timestamp", lambda interval: 1704078000)

    summary = service.complete_canonical(assets=("BTC",), allow_synthetic=False)
    completed = candle_path.read_text(encoding="utf-8").splitlines()

    assert summary["assets"][0]["intervals"][0]["missing_intervals_before"] == 1
    assert summary["assets"][0]["intervals"][0]["missing_intervals_after"] == 0
    assert summary["assets"][0]["intervals"][0]["kraken_api_added"] == 1
    assert summary["assets"][0]["intervals"][0]["binance_added"] == 1
    assert len(completed) == 5


def test_complete_canonical_can_use_synthetic_last_resort(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime: {}
exchange: {}
data:
  raw_kraken_dir: data/kraken_data
  canonical_dir: data/canonical
  reports_dir: artifacts/reports/data
  intervals: [1h]
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    config = load_config(config_path=config_path, env_path=tmp_path / ".env")

    candle_path = tmp_path / "data" / "canonical" / "kraken" / "BTC" / "candles_1h.csv"
    write_candles(
        candle_path,
        [
            Candle(1704067200, 100, 101, 99, 100, 10, 5, "kraken_raw"),
            Candle(1704074400, 102, 103, 101, 102, 12, 6, "kraken_raw"),
        ],
    )

    class EmptyKrakenClient:
        def fetch_ohlc_range(
            self,
            pair: str,
            interval: str,
            start_ts: int,
            end_ts: int,
        ) -> list[Candle]:
            return []

    class EmptyFallbackClient:
        def fetch_klines(
            self,
            symbol: str,
            interval: str,
            start_ts: int,
            end_ts: int,
        ) -> list[Candle]:
            return []

        def fetch_candles(
            self,
            product_id: str,
            interval: str,
            start_ts: int,
            end_ts: int,
        ) -> list[Candle]:
            return []

    service = DataService(
        config,
        kraken_client=EmptyKrakenClient(),
        binance_client=EmptyFallbackClient(),
        coinbase_client=EmptyFallbackClient(),
    )
    monkeypatch.setattr(service, "_latest_closed_timestamp", lambda interval: 1704078000)

    summary = service.complete_canonical(assets=("BTC",), allow_synthetic=True)
    rows = candle_path.read_text(encoding="utf-8")

    assert summary["assets"][0]["intervals"][0]["synthetic_added"] == 2
    assert summary["assets"][0]["intervals"][0]["missing_intervals_after"] == 0
    assert "synthetic_gap_fill" in rows