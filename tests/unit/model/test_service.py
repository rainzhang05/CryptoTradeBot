"""Unit tests for the Phase 6 model service."""

from __future__ import annotations

import json
from pathlib import Path

from tradebot.config import load_config
from tradebot.data.models import Candle
from tradebot.data.storage import write_candles
from tradebot.model.service import ModelService


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
model:
    initial_train_timestamps: 2
    minimum_validation_rows: 1
    minimum_walk_forward_splits: 1
    retrain_cadence_days: 14
    promotion_min_expected_return_correlation: -1.0
    promotion_max_downside_brier: 1.0
    promotion_max_sell_brier: 1.0
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


def test_train_validate_and_promote_model(tmp_path: Path) -> None:
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

    service = ModelService(config)
    training = service.train_model(assets=("BTC", "ETH"))
    validation = service.validate_model(training.model_id)
    promotion = service.promote_model(training.model_id)

    assert Path(training.manifest_file).exists()
    assert Path(training.metrics_file).exists()
    assert Path(training.predictions_file).exists()
    assert Path(training.bundle_file).exists()
    assert validation.model_id == training.model_id
    assert validation.promotion_eligible is True
    assert Path(promotion.pointer_file).exists()
    promotion_summary = (
        tmp_path / "artifacts" / "reports" / "models" / "latest_promotion_summary.json"
    )
    assert promotion_summary.exists()
    assert service.load_active_reference(dataset_id=training.dataset_id) is not None


def test_enrich_rows_with_active_predictions_adds_model_scores(tmp_path: Path) -> None:
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

    service = ModelService(config)
    training = service.train_model(assets=("BTC", "ETH"))
    service.promote_model(training.model_id)

    feature_store = service.research_service.build_feature_store(assets=("BTC", "ETH"))
    rows = service._load_dataset_rows(Path(feature_store.dataset_file))
    latest_timestamp = max(int(row["timestamp"]) for row in rows)
    rows_for_timestamp = {
        str(row["asset"]): row for row in rows if int(row["timestamp"]) == latest_timestamp
    }

    enriched, model_id = service.enrich_rows_with_active_predictions(
        dataset_id=feature_store.dataset_id,
        rows_by_asset=rows_for_timestamp,
        timestamp=latest_timestamp,
    )

    assert model_id == training.model_id
    assert "expected_return_score" in enriched["BTC"]
    assert "downside_risk_score" in enriched["BTC"]
    assert "sell_risk_score" in enriched["BTC"]


def test_train_model_manifest_excludes_forward_label_columns(tmp_path: Path) -> None:
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

    service = ModelService(config)
    training = service.train_model(assets=("BTC", "ETH"))
    manifest = json.loads(Path(training.manifest_file).read_text(encoding="utf-8"))

    assert all(not str(column).startswith("label_") for column in manifest["feature_columns"])


def test_infer_rows_with_active_model_scores_live_signal_rows(tmp_path: Path) -> None:
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

    service = ModelService(config)
    training = service.train_model(assets=("BTC", "ETH"))
    service.promote_model(training.model_id)
    _, _, rows_by_asset = service.research_service.build_live_signal_rows(assets=("BTC", "ETH"))

    enriched, model_id = service.infer_rows_with_active_model(rows_by_asset)

    assert model_id == training.model_id
    assert "expected_return_score" in enriched["BTC"]
    assert "downside_risk_score" in enriched["BTC"]
    assert "sell_risk_score" in enriched["BTC"]


def test_positive_class_probabilities_handle_single_class_outputs(tmp_path: Path) -> None:
    config = load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    service = ModelService(config)
    features = [{"asset": "BTC", "momentum_2d": 0.1}, {"asset": "ETH", "momentum_2d": -0.1}]

    all_negative = service._fit_classifier(features, [0, 0])
    all_positive = service._fit_classifier(features, [1, 1])

    assert service._positive_class_probabilities(all_negative, features) == [0.0, 0.0]
    assert service._positive_class_probabilities(all_positive, features) == [1.0, 1.0]


def test_load_active_reference_ignores_missing_artifact_files(tmp_path: Path) -> None:
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

    service = ModelService(config)
    training = service.train_model(assets=("BTC", "ETH"))
    service.promote_model(training.model_id)
    Path(training.predictions_file).unlink()

    assert service.load_active_reference(dataset_id=training.dataset_id) is None
