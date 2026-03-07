"""Unit tests for the research feature pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from spotbot.config import load_config
from spotbot.data.models import Candle
from spotbot.data.storage import write_candles
from spotbot.research.features import build_feature_rows
from spotbot.research.service import ResearchService


def _write_config(root: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime: {}
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
alerts: {}
paths:
  data_dir: data
  artifacts_dir: artifacts
  features_dir: artifacts/features
  experiments_dir: artifacts/experiments
  logs_dir: runtime/logs
  state_dir: runtime/state
""",
        encoding="utf-8",
    )
    return config_path


def _write_canonical_series(root: Path, asset: str, candles: list[Candle]) -> None:
    path = root / "data" / "canonical" / "kraken" / asset / "candles_1d.csv"
    write_candles(path, candles)


def _daily_candles(
    closes: list[float],
    lows: list[float],
    *,
    sources: list[str] | None = None,
) -> list[Candle]:
    candles: list[Candle] = []
    source_values = sources or ["kraken_api"] * len(closes)
    start_timestamp = 1_704_067_200
    for index, close in enumerate(closes):
        candles.append(
            Candle(
                timestamp=start_timestamp + index * 86_400,
                open=close - 1,
                high=close + 1,
                low=lows[index],
                close=close,
                volume=1000 + index * 10,
                trade_count=100 + index,
                source=source_values[index],
            )
        )
    return candles


def test_build_feature_rows_generates_expected_labels_and_regime(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    rows, _ = build_feature_rows(
        {
            "BTC": _daily_candles(
                [100, 105, 110, 112, 115, 118, 120],
                [99, 104, 109, 111, 114, 117, 119],
            ),
            "ETH": _daily_candles(
                [50, 52, 54, 53, 55, 57, 56],
                [49, 51, 53, 52, 48, 56, 55],
                sources=[
                    "kraken_api",
                    "kraken_api",
                    "binance",
                    "kraken_api",
                    "kraken_api",
                    "kraken_api",
                    "kraken_api",
                ],
            ),
        },
        config.research,
    )

    assert len(rows) == 2

    eth_row = next(row for row in rows if row["asset"] == "ETH")
    assert eth_row["regime_state"] == "constructive"
    assert eth_row["regime_constructive"] == 1
    assert eth_row["momentum_2d"] == 53 / 52 - 1
    assert eth_row["label_forward_return_1d"] == 55 / 53 - 1
    assert eth_row["label_downside_risk_flag_2d"] == 1
    assert eth_row["label_sell_risk_flag_3d"] == 0
    assert eth_row["binance_source_ratio_2d"] == 0.5
    assert eth_row["fallback_source_ratio_2d"] == 0.5


def test_build_feature_store_writes_manifest_and_uses_cache(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    _write_canonical_series(
        tmp_path,
        "BTC",
        _daily_candles(
            [100, 105, 110, 112, 115, 118, 120],
            [99, 104, 109, 111, 114, 117, 119],
        ),
    )
    _write_canonical_series(
        tmp_path,
        "ETH",
        _daily_candles(
            [50, 52, 54, 53, 55, 57, 56],
            [49, 51, 53, 52, 48, 56, 55],
        ),
    )

    service = ResearchService(config)
    first = service.build_feature_store(assets=("BTC", "ETH"))
    second = service.build_feature_store(assets=("BTC", "ETH"))

    assert first.cached is False
    assert second.cached is True
    assert first.dataset_id == second.dataset_id
    assert Path(first.dataset_file).exists()
    assert Path(first.manifest_file).exists()
    assert Path(first.experiment_root).is_dir()

    manifest = json.loads(Path(first.manifest_file).read_text(encoding="utf-8"))
    assert manifest["row_count"] == 2
    assert manifest["selected_assets"] == ["BTC", "ETH"]
    assert manifest["experiment_layout"]["dataset_reference_field"] == "dataset_id"