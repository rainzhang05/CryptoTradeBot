"""Phase 6 ML training, validation, promotion, and prediction integration."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import pickle
from collections.abc import Callable
from datetime import UTC, datetime
from numbers import Real
from pathlib import Path
from typing import Any, cast

from sklearn.dummy import DummyClassifier, DummyRegressor  # type: ignore[import-untyped]
from sklearn.ensemble import (  # type: ignore[import-untyped]  # type: ignore[import-untyped]
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.feature_extraction import DictVectorizer  # type: ignore[import-untyped]
from sklearn.linear_model import (  # type: ignore[import-untyped]
    ElasticNet,
    LogisticRegression,
    Ridge,
)
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from tradebot.cancellation import CancellationToken
from tradebot.config import AppConfig
from tradebot.data.storage import write_json
from tradebot.logging_config import get_logger
from tradebot.model.models import (
    ActiveModelReference,
    ModelPromotionSummary,
    ModelTrainingSummary,
    ModelValidationSummary,
)
from tradebot.model.storage import (
    active_model_pointer_file,
    latest_promotion_summary_file,
    latest_training_summary_file,
    latest_validation_summary_file,
    model_bundle_file,
    model_manifest_file,
    model_metrics_file,
    model_predictions_file,
    write_prediction_rows,
)
from tradebot.research.service import ResearchService

type ModelRow = dict[str, object]
type PredictionRow = dict[str, object]
type MetricPayload = dict[str, object]
type PromotionBacktestComparison = dict[str, object]


class ModelService:
    """Train and serve Phase 6 model artifacts."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.paths = config.resolved_paths()
        self.logger = get_logger("tradebot.model")
        self.research_service = ResearchService(config)

    def train_model(
        self,
        assets: tuple[str, ...] | None = None,
        force_features: bool = False,
        cancellation_token: CancellationToken | None = None,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
        *,
        dataset_track: str | None = None,
        family: str = "ridge_logistic",
        hyperparameters: dict[str, object] | None = None,
    ) -> ModelTrainingSummary:
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        if not self.config.model.enabled:
            raise ValueError("Model subsystem is disabled in configuration")
        self.logger.info(
            "model training started",
            extra={"assets": list(assets or ()), "force_features": force_features},
        )

        feature_store = self.research_service.build_feature_store(
            assets=assets,
            force=force_features,
            dataset_track=dataset_track,
            cancellation_token=cancellation_token,
        )
        rows = self._load_dataset_rows(Path(feature_store.dataset_file))
        timestamps = self._unique_timestamps(rows)
        if len(timestamps) <= self.config.model.initial_train_timestamps:
            raise ValueError(
                self._insufficient_training_data_message(
                    feature_store=feature_store,
                    timestamp_count=len(timestamps),
                )
            )

        predictions: list[PredictionRow] = []
        split_count = 0
        training_profile = {
            "family": family,
            "hyperparameters": hyperparameters or {},
        }
        for timestamp in timestamps[self.config.model.initial_train_timestamps :]:
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            train_rows = [row for row in rows if self._coerce_int(row["timestamp"]) < timestamp]
            validation_rows = [
                row for row in rows if self._coerce_int(row["timestamp"]) == timestamp
            ]
            bundle = self._fit_bundle(
                train_rows,
                family=family,
                hyperparameters=hyperparameters,
            )
            predictions.extend(self._predict_rows(bundle, validation_rows))
            split_count += 1
            if progress_callback is not None:
                progress_callback(
                    {
                        "split_count": split_count,
                        "total_splits": len(
                            timestamps[self.config.model.initial_train_timestamps :]
                        ),
                        "timestamp": timestamp,
                    }
                )

        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()
        final_bundle = self._fit_bundle(
            rows,
            family=family,
            hyperparameters=hyperparameters,
        )
        profile_id = hashlib.sha256(
            json.dumps(training_profile, sort_keys=True).encode("utf-8")
        ).hexdigest()[:8]
        model_id = (
            f"{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
            f"_{feature_store.dataset_id}_{profile_id}"
        )
        manifest_path = model_manifest_file(self.paths.models_dir, model_id)
        metrics_path = model_metrics_file(self.paths.models_dir, model_id)
        predictions_path = model_predictions_file(self.paths.models_dir, model_id)
        bundle_path = model_bundle_file(self.paths.models_dir, model_id)
        metrics_payload = self._metrics_payload(predictions, split_count)
        manifest_payload = {
            "model_id": model_id,
            "dataset_id": feature_store.dataset_id,
            "created_at": datetime.now(tz=UTC).isoformat(),
            "selected_assets": list(feature_store.selected_assets),
            "dataset_track": feature_store.dataset_track,
            "model_settings": self.config.model.model_dump(mode="json"),
            "research_settings": self.config.research.model_dump(mode="json"),
            "training_profile": training_profile,
            "feature_dataset_file": feature_store.dataset_file,
            "feature_manifest_file": feature_store.manifest_file,
            "feature_columns": self._feature_columns(rows),
            "label_columns": self._label_columns(),
            "split_count": split_count,
            "validation_row_count": len(predictions),
            "retrain_cadence_days": self.config.model.retrain_cadence_days,
        }
        write_json(manifest_path, manifest_payload)
        write_json(metrics_path, metrics_payload)
        write_prediction_rows(predictions_path, predictions)
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_path.write_bytes(pickle.dumps(final_bundle))

        summary = ModelTrainingSummary(
            model_id=model_id,
            dataset_id=feature_store.dataset_id,
            artifact_dir=str(bundle_path.parent),
            manifest_file=str(manifest_path),
            metrics_file=str(metrics_path),
            predictions_file=str(predictions_path),
            bundle_file=str(bundle_path),
            split_count=split_count,
            validation_row_count=len(predictions),
            selected_assets=feature_store.selected_assets,
        )
        write_json(
            latest_training_summary_file(self.paths.model_reports_dir),
            summary.to_dict() | {"metrics": metrics_payload},
        )
        self.logger.info(
            "model training completed",
            extra={
                "model_id": model_id,
                "dataset_id": feature_store.dataset_id,
                "split_count": split_count,
                "validation_row_count": len(predictions),
            },
        )
        return summary

    def validate_model(self, model_id: str | None = None) -> ModelValidationSummary:
        selected_model_id = model_id or self._latest_trained_model_id()
        manifest_path = model_manifest_file(self.paths.models_dir, selected_model_id)
        metrics_path = model_metrics_file(self.paths.models_dir, selected_model_id)
        if not manifest_path.exists() or not metrics_path.exists():
            raise FileNotFoundError(f"Model artifact does not exist: {selected_model_id}")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        promotion_eligible = self._promotion_eligible(metrics)
        summary = ModelValidationSummary(
            model_id=selected_model_id,
            dataset_id=str(manifest["dataset_id"]),
            manifest_file=str(manifest_path),
            metrics_file=str(metrics_path),
            split_count=int(metrics["split_count"]),
            validation_row_count=int(metrics["validation_row_count"]),
            expected_return_mae=float(metrics["expected_return_mae"]),
            expected_return_correlation=float(metrics["expected_return_correlation"]),
            expected_return_directional_accuracy=float(
                metrics["expected_return_directional_accuracy"]
            ),
            downside_brier_score=float(metrics["downside_brier_score"]),
            sell_brier_score=float(metrics["sell_brier_score"]),
            promotion_eligible=promotion_eligible,
            selected_assets=tuple(str(asset) for asset in manifest["selected_assets"]),
        )
        write_json(
            latest_validation_summary_file(self.paths.model_reports_dir),
            summary.to_dict(),
        )
        self.logger.info(
            "model validation completed",
            extra={
                "model_id": summary.model_id,
                "dataset_id": summary.dataset_id,
                "promotion_eligible": summary.promotion_eligible,
            },
        )
        return summary

    def promote_model(self, model_id: str | None = None) -> ModelPromotionSummary:
        validation = self.validate_model(model_id=model_id)
        if not validation.promotion_eligible:
            raise ValueError(f"Model {validation.model_id} does not satisfy promotion rules")
        comparison = self._promotion_backtest_comparison(
            model_id=validation.model_id,
            assets=validation.selected_assets,
        )
        hybrid = cast(Any, comparison["hybrid"])
        rule_only = cast(Any, comparison["rule_only"])
        incremental_total_return = self._coerce_float(comparison["incremental_total_return"])
        if incremental_total_return <= 0:
            raise ValueError(
                "Model "
                f"{validation.model_id} does not improve on the rule-only baseline "
                f"(hybrid_total_return={hybrid.total_return:.6f}, "
                f"rule_only_total_return={rule_only.total_return:.6f})"
            )

        pointer_path = active_model_pointer_file(self.paths.models_dir)
        previous_model_id: str | None = None
        if pointer_path.exists():
            previous_pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            previous_model_id = previous_pointer.get("model_id")

        manifest_path = model_manifest_file(self.paths.models_dir, validation.model_id)
        payload = {
            "model_id": validation.model_id,
            "dataset_id": validation.dataset_id,
            "manifest_file": str(manifest_path),
            "metrics_file": str(model_metrics_file(self.paths.models_dir, validation.model_id)),
            "predictions_file": str(
                model_predictions_file(self.paths.models_dir, validation.model_id)
            ),
            "bundle_file": str(model_bundle_file(self.paths.models_dir, validation.model_id)),
            "promoted_at": datetime.now(tz=UTC).isoformat(),
        }
        write_json(pointer_path, payload)
        summary = ModelPromotionSummary(
            model_id=validation.model_id,
            dataset_id=validation.dataset_id,
            pointer_file=str(pointer_path),
            manifest_file=str(manifest_path),
            previous_model_id=previous_model_id,
            promoted_at=str(payload["promoted_at"]),
            hybrid_backtest_run_id=hybrid.run_id,
            hybrid_total_return=hybrid.total_return,
            rule_only_backtest_run_id=rule_only.run_id,
            rule_only_total_return=rule_only.total_return,
            incremental_total_return=incremental_total_return,
        )
        write_json(
            latest_promotion_summary_file(self.paths.model_reports_dir),
            summary.to_dict(),
        )
        self.logger.info(
            "model promoted",
            extra={
                "model_id": summary.model_id,
                "dataset_id": summary.dataset_id,
                "previous_model_id": previous_model_id,
            },
        )
        return summary

    def load_active_reference(self, *, dataset_id: str) -> ActiveModelReference | None:
        return self._load_active_reference(dataset_id=dataset_id)

    def load_latest_active_reference(self) -> ActiveModelReference | None:
        return self._load_active_reference(dataset_id=None)

    def load_model_reference(self, model_id: str) -> ActiveModelReference:
        manifest_path = model_manifest_file(self.paths.models_dir, model_id)
        metrics_path = model_metrics_file(self.paths.models_dir, model_id)
        predictions_path = model_predictions_file(self.paths.models_dir, model_id)
        bundle_path = model_bundle_file(self.paths.models_dir, model_id)
        required_paths = (manifest_path, metrics_path, predictions_path, bundle_path)
        if any(not path.exists() for path in required_paths):
            raise FileNotFoundError(f"Model artifact does not exist: {model_id}")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return ActiveModelReference(
            model_id=str(manifest["model_id"]),
            dataset_id=str(manifest["dataset_id"]),
            manifest_file=str(manifest_path),
            metrics_file=str(metrics_path),
            predictions_file=str(predictions_path),
            bundle_file=str(bundle_path),
            selected_assets=tuple(str(asset) for asset in manifest["selected_assets"]),
        )

    def _load_active_reference(self, *, dataset_id: str | None) -> ActiveModelReference | None:
        pointer_path = active_model_pointer_file(self.paths.models_dir)
        if not pointer_path.exists():
            return None

        payload = json.loads(pointer_path.read_text(encoding="utf-8"))
        if dataset_id is not None and str(payload.get("dataset_id")) != dataset_id:
            return None
        manifest_path = Path(str(payload["manifest_file"]))
        required_paths = (
            manifest_path,
            Path(str(payload["metrics_file"])),
            Path(str(payload["predictions_file"])),
            Path(str(payload["bundle_file"])),
        )
        if any(not path.exists() for path in required_paths):
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return ActiveModelReference(
            model_id=str(payload["model_id"]),
            dataset_id=str(payload["dataset_id"]),
            manifest_file=str(payload["manifest_file"]),
            metrics_file=str(payload["metrics_file"]),
            predictions_file=str(payload["predictions_file"]),
            bundle_file=str(payload["bundle_file"]),
            selected_assets=tuple(str(asset) for asset in manifest["selected_assets"]),
        )

    def enrich_rows_with_active_predictions(
        self,
        *,
        dataset_id: str,
        rows_by_asset: dict[str, dict[str, object]],
        timestamp: int,
    ) -> tuple[dict[str, dict[str, object]], str | None]:
        reference = self.load_active_reference(dataset_id=dataset_id)
        if reference is None:
            return rows_by_asset, None

        prediction_index = self._load_prediction_index(Path(reference.predictions_file))
        enriched: dict[str, dict[str, object]] = {}
        for asset, row in rows_by_asset.items():
            enriched_row = dict(row)
            prediction_row = prediction_index.get((timestamp, asset))
            if prediction_row is not None:
                enriched_row["expected_return_score"] = prediction_row["expected_return_score"]
                enriched_row["downside_risk_score"] = prediction_row["downside_risk_score"]
                enriched_row["sell_risk_score"] = prediction_row["sell_risk_score"]
            enriched[asset] = enriched_row
        return enriched, reference.model_id

    def enrich_rows_with_model_predictions(
        self,
        *,
        model_id: str,
        dataset_id: str,
        rows_by_asset: dict[str, dict[str, object]],
        timestamp: int,
    ) -> tuple[dict[str, dict[str, object]], str]:
        reference = self.load_model_reference(model_id)
        if reference.dataset_id != dataset_id:
            raise ValueError(
                f"Model {model_id} was trained on dataset {reference.dataset_id}, "
                f"but the active backtest dataset is {dataset_id}"
            )

        prediction_index = self._load_prediction_index(Path(reference.predictions_file))
        enriched: dict[str, dict[str, object]] = {}
        for asset, row in rows_by_asset.items():
            enriched_row = dict(row)
            prediction_row = prediction_index.get((timestamp, asset))
            if prediction_row is not None:
                enriched_row["expected_return_score"] = prediction_row["expected_return_score"]
                enriched_row["downside_risk_score"] = prediction_row["downside_risk_score"]
                enriched_row["sell_risk_score"] = prediction_row["sell_risk_score"]
            enriched[asset] = enriched_row
        return enriched, reference.model_id

    def infer_rows_with_active_model(
        self,
        rows_by_asset: dict[str, dict[str, object]],
    ) -> tuple[dict[str, dict[str, object]], str | None]:
        """Run the promoted model bundle on point-in-time rows for live inference."""
        reference = self.load_latest_active_reference()
        if reference is None:
            return rows_by_asset, None

        manifest = json.loads(Path(reference.manifest_file).read_text(encoding="utf-8"))
        feature_columns = [str(column) for column in manifest.get("feature_columns", [])]
        if any(
            any(column not in row for column in feature_columns)
            for row in rows_by_asset.values()
        ):
            return rows_by_asset, None

        bundle = pickle.loads(Path(reference.bundle_file).read_bytes())
        ordered_assets = sorted(rows_by_asset)
        ordered_rows = [rows_by_asset[asset] for asset in ordered_assets]
        features = [self._feature_payload(row) for row in ordered_rows]
        expected_values = list(bundle["expected_return"].predict(features))
        downside_values = self._positive_class_probabilities(bundle["downside_risk"], features)
        sell_values = self._positive_class_probabilities(bundle["sell_risk"], features)

        enriched: dict[str, dict[str, object]] = {}
        for asset, row, expected, downside, sell in zip(
            ordered_assets,
            ordered_rows,
            expected_values,
            downside_values,
            sell_values,
            strict=True,
        ):
            enriched[asset] = dict(row) | {
                "expected_return_score": self._coerce_float(expected),
                "downside_risk_score": self._coerce_float(downside),
                "sell_risk_score": self._coerce_float(sell),
            }
        return enriched, reference.model_id

    def _latest_trained_model_id(self) -> str:
        summary_path = latest_training_summary_file(self.paths.model_reports_dir)
        if not summary_path.exists():
            raise FileNotFoundError("No model training summary exists yet")
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        return str(payload["model_id"])

    def _promotion_backtest_comparison(
        self,
        *,
        model_id: str,
        assets: tuple[str, ...],
    ) -> PromotionBacktestComparison:
        from tradebot.backtest.service import BacktestService

        backtest_service = BacktestService(self.config)
        hybrid = backtest_service.run_backtest(
            assets=assets,
            force_features=False,
            model_id=model_id,
            use_active_model=False,
        )
        rule_only = backtest_service.run_backtest(
            assets=assets,
            force_features=False,
            use_active_model=False,
        )
        return {
            "hybrid": hybrid,
            "rule_only": rule_only,
            "incremental_total_return": hybrid.total_return - rule_only.total_return,
        }

    def _insufficient_training_data_message(
        self,
        *,
        feature_store: Any,
        timestamp_count: int,
    ) -> str:
        minimum_required = self.config.model.initial_train_timestamps + 1
        limiting_asset = min(
            feature_store.asset_stats,
            key=lambda entry: entry.kraken_rows + entry.fallback_rows,
        )
        limiting_history = limiting_asset.kraken_rows + limiting_asset.fallback_rows
        return (
            "Not enough usable aligned feature timestamps to train the ML model. "
            f"Available={timestamp_count}, required>{self.config.model.initial_train_timestamps} "
            f"(minimum {minimum_required}). "
            "This does not mean your candles have gaps; it usually means the selected assets only "
            "share a shorter common history after the feature lookbacks and forward-label windows "
            f"are applied. The shortest-history selected asset is {limiting_asset.asset} with "
            f"{limiting_history} daily candles. "
            "Options: lower model.initial_train_timestamps, reduce the longest research windows, "
            "or train on a subset of assets with deeper shared history."
        )

    def _load_dataset_rows(self, path: Path) -> list[ModelRow]:
        rows: list[ModelRow] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                parsed: ModelRow = {}
                for key, value in row.items():
                    if key is None or value is None:
                        continue
                    if key in {"asset", "regime_state"}:
                        parsed[key] = value
                    elif key == "timestamp":
                        parsed[key] = int(value)
                    else:
                        parsed[key] = float(value)
                rows.append(parsed)
        return rows

    def _unique_timestamps(self, rows: list[ModelRow]) -> list[int]:
        return sorted({self._coerce_int(row["timestamp"]) for row in rows})

    def _fit_bundle(
        self,
        rows: list[ModelRow],
        *,
        family: str = "ridge_logistic",
        hyperparameters: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        features = [self._feature_payload(row) for row in rows]
        label_columns = self._label_columns()
        expected_targets = [
            self._coerce_float(row[label_columns["expected_return"]]) for row in rows
        ]
        downside_targets = [
            int(self._coerce_float(row[label_columns["downside_risk"]])) for row in rows
        ]
        sell_targets = [
            int(self._coerce_float(row[label_columns["sell_risk"]])) for row in rows
        ]
        return {
            "expected_return": self._fit_regressor(
                features,
                expected_targets,
                family=family,
                hyperparameters=hyperparameters,
            ),
            "downside_risk": self._fit_classifier(
                features,
                downside_targets,
                family=family,
                hyperparameters=hyperparameters,
            ),
            "sell_risk": self._fit_classifier(
                features,
                sell_targets,
                family=family,
                hyperparameters=hyperparameters,
            ),
        }

    def _predict_rows(
        self,
        bundle: dict[str, Any],
        rows: list[ModelRow],
    ) -> list[PredictionRow]:
        features = [self._feature_payload(row) for row in rows]
        expected_values = list(bundle["expected_return"].predict(features))
        downside_values = self._positive_class_probabilities(bundle["downside_risk"], features)
        sell_values = self._positive_class_probabilities(bundle["sell_risk"], features)
        label_columns = self._label_columns()
        predictions: list[PredictionRow] = []
        for row, expected, downside, sell in zip(
            rows,
            expected_values,
            downside_values,
            sell_values,
            strict=True,
        ):
            predictions.append(
                {
                    "timestamp": self._coerce_int(row["timestamp"]),
                    "asset": str(row["asset"]),
                    "expected_return_score": self._coerce_float(expected),
                    "downside_risk_score": self._coerce_float(downside),
                    "sell_risk_score": self._coerce_float(sell),
                    "actual_forward_return": self._coerce_float(
                        row[label_columns["expected_return"]]
                    ),
                    "actual_downside_risk_flag": int(
                        self._coerce_float(row[label_columns["downside_risk"]])
                    ),
                    "actual_sell_risk_flag": int(
                        self._coerce_float(row[label_columns["sell_risk"]])
                    ),
                }
            )
        return predictions

    def _fit_regressor(
        self,
        features: list[dict[str, object]],
        targets: list[float],
        *,
        family: str = "ridge_logistic",
        hyperparameters: dict[str, object] | None = None,
    ) -> Pipeline:
        if len(targets) <= 1:
            model = DummyRegressor(strategy="mean")
        elif family == "ridge_logistic":
            model = Ridge(alpha=1.0)
        elif family == "elastic_net_logistic":
            params = hyperparameters or {}
            model = ElasticNet(
                alpha=self._coerce_float(params.get("elastic_net_alpha", 1e-3)),
                l1_ratio=self._coerce_float(params.get("elastic_net_l1_ratio", 0.5)),
                random_state=0,
                max_iter=10_000,
            )
        elif family == "random_forest":
            params = hyperparameters or {}
            model = RandomForestRegressor(
                n_estimators=self._coerce_int(params.get("rf_n_estimators", 200)),
                max_depth=self._optional_int(params.get("rf_max_depth")),
                min_samples_leaf=self._coerce_int(params.get("rf_min_samples_leaf", 1)),
                random_state=0,
                n_jobs=1,
            )
        elif family == "hist_gradient_boosting":
            params = hyperparameters or {}
            model = HistGradientBoostingRegressor(
                learning_rate=self._coerce_float(params.get("hgb_learning_rate", 0.1)),
                max_depth=self._optional_int(params.get("hgb_max_depth")),
                max_leaf_nodes=self._optional_int(params.get("hgb_max_leaf_nodes", 31)),
                min_samples_leaf=self._coerce_int(params.get("hgb_min_samples_leaf", 20)),
                random_state=0,
            )
        else:
            raise ValueError(f"Unsupported model family: {family}")
        pipeline = Pipeline(self._pipeline_steps(family=family, model=model))
        pipeline.fit(features, targets)
        return pipeline

    def _fit_classifier(
        self,
        features: list[dict[str, object]],
        targets: list[int],
        *,
        family: str = "ridge_logistic",
        hyperparameters: dict[str, object] | None = None,
    ) -> Pipeline:
        if len(set(targets)) < 2:
            model = DummyClassifier(strategy="constant", constant=targets[0])
        elif family == "ridge_logistic":
            model = LogisticRegression(
                class_weight="balanced",
                max_iter=1_000,
                random_state=0,
            )
        elif family == "elastic_net_logistic":
            params = hyperparameters or {}
            alpha = self._coerce_float(params.get("elastic_net_alpha", 1e-3))
            model = LogisticRegression(
                class_weight="balanced",
                penalty="elasticnet",
                solver="saga",
                l1_ratio=self._coerce_float(params.get("elastic_net_l1_ratio", 0.5)),
                C=max(1.0, 1.0 / alpha),
                max_iter=2_000,
                random_state=0,
            )
        elif family == "random_forest":
            params = hyperparameters or {}
            model = RandomForestClassifier(
                n_estimators=self._coerce_int(params.get("rf_n_estimators", 200)),
                max_depth=self._optional_int(params.get("rf_max_depth")),
                min_samples_leaf=self._coerce_int(params.get("rf_min_samples_leaf", 1)),
                class_weight="balanced",
                random_state=0,
                n_jobs=1,
            )
        elif family == "hist_gradient_boosting":
            params = hyperparameters or {}
            model = HistGradientBoostingClassifier(
                learning_rate=self._coerce_float(params.get("hgb_learning_rate", 0.1)),
                max_depth=self._optional_int(params.get("hgb_max_depth")),
                max_leaf_nodes=self._optional_int(params.get("hgb_max_leaf_nodes", 31)),
                min_samples_leaf=self._coerce_int(params.get("hgb_min_samples_leaf", 20)),
                random_state=0,
            )
        else:
            raise ValueError(f"Unsupported model family: {family}")
        pipeline = Pipeline(self._pipeline_steps(family=family, model=model))
        pipeline.fit(features, targets)
        return pipeline

    def _pipeline_steps(self, *, family: str, model: Any) -> list[tuple[str, Any]]:
        if family == "ridge_logistic":
            return [
                ("vectorizer", DictVectorizer(sparse=True)),
                ("scaler", StandardScaler(with_mean=False)),
                ("model", model),
            ]
        if family == "elastic_net_logistic":
            return [
                ("vectorizer", DictVectorizer(sparse=False)),
                ("scaler", StandardScaler()),
                ("model", model),
            ]
        if family in {"random_forest", "hist_gradient_boosting"}:
            return [
                ("vectorizer", DictVectorizer(sparse=False)),
                ("model", model),
            ]
        raise ValueError(f"Unsupported model family: {family}")

    def _feature_payload(self, row: ModelRow) -> dict[str, object]:
        return {
            key: value
            for key, value in row.items()
            if key != "timestamp" and not key.startswith("label_")
        }

    def _feature_columns(self, rows: list[ModelRow]) -> list[str]:
        columns = sorted(self._feature_payload(rows[0]).keys())
        return columns

    def _label_columns(self) -> dict[str, str]:
        return {
            "expected_return": (
                f"label_forward_return_{self.config.research.forward_return_days}d"
            ),
            "downside_risk": (
                f"label_downside_risk_flag_{self.config.research.downside_lookahead_days}d"
            ),
            "sell_risk": f"label_sell_risk_flag_{self.config.research.sell_lookahead_days}d",
        }

    def _metrics_payload(
        self,
        predictions: list[PredictionRow],
        split_count: int,
    ) -> MetricPayload:
        if not predictions:
            raise ValueError("Walk-forward validation did not produce any prediction rows")

        expected_pairs = [
            (
                self._coerce_float(row["expected_return_score"]),
                self._coerce_float(row["actual_forward_return"]),
            )
            for row in predictions
        ]
        downside_pairs = [
            (
                self._coerce_float(row["downside_risk_score"]),
                self._coerce_float(row["actual_downside_risk_flag"]),
            )
            for row in predictions
        ]
        sell_pairs = [
            (
                self._coerce_float(row["sell_risk_score"]),
                self._coerce_float(row["actual_sell_risk_flag"]),
            )
            for row in predictions
        ]
        return {
            "split_count": split_count,
            "validation_row_count": len(predictions),
            "expected_return_mae": self._mean_absolute_error(expected_pairs),
            "expected_return_correlation": self._correlation(expected_pairs),
            "expected_return_directional_accuracy": self._directional_accuracy(expected_pairs),
            "downside_brier_score": self._brier_score(downside_pairs),
            "sell_brier_score": self._brier_score(sell_pairs),
        }

    def _promotion_eligible(self, metrics: MetricPayload) -> bool:
        return (
            self._coerce_int(metrics["validation_row_count"])
            >= self.config.model.minimum_validation_rows
            and self._coerce_int(metrics["split_count"])
            >= self.config.model.minimum_walk_forward_splits
            and self._coerce_float(metrics["expected_return_correlation"])
            >= self.config.model.promotion_min_expected_return_correlation
            and self._coerce_float(metrics["downside_brier_score"])
            <= self.config.model.promotion_max_downside_brier
            and self._coerce_float(metrics["sell_brier_score"])
            <= self.config.model.promotion_max_sell_brier
        )

    def _load_prediction_index(
        self,
        path: Path,
    ) -> dict[tuple[int, str], dict[str, float]]:
        index: dict[tuple[int, str], dict[str, float]] = {}
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                timestamp = int(str(row["timestamp"]))
                asset = str(row["asset"])
                index[(timestamp, asset)] = {
                    "expected_return_score": float(str(row["expected_return_score"])),
                    "downside_risk_score": float(str(row["downside_risk_score"])),
                    "sell_risk_score": float(str(row["sell_risk_score"])),
                }
        return index

    def _positive_class_probabilities(
        self,
        model: Pipeline,
        features: list[dict[str, object]],
    ) -> list[float]:
        matrix = [list(row) for row in model.predict_proba(features)]
        if not matrix:
            return []

        classifier = model.named_steps["model"]
        classes = [self._coerce_int(value) for value in getattr(classifier, "classes_", [])]
        if 1 not in classes:
            return [0.0 for _ in matrix]

        positive_index = classes.index(1)
        return [self._coerce_float(row[positive_index]) for row in matrix]

    def _mean_absolute_error(self, pairs: list[tuple[float, float]]) -> float:
        return sum(abs(predicted - actual) for predicted, actual in pairs) / len(pairs)

    def _brier_score(self, pairs: list[tuple[float, float]]) -> float:
        return sum((predicted - actual) ** 2 for predicted, actual in pairs) / len(pairs)

    def _directional_accuracy(self, pairs: list[tuple[float, float]]) -> float:
        hits = 0
        for predicted, actual in pairs:
            if predicted == 0 and actual == 0:
                hits += 1
            elif predicted == 0 or actual == 0:
                continue
            elif predicted > 0 and actual > 0:
                hits += 1
            elif predicted < 0 and actual < 0:
                hits += 1
        return hits / len(pairs)

    def _correlation(self, pairs: list[tuple[float, float]]) -> float:
        if len(pairs) < 2:
            return 0.0
        predicted_mean = sum(predicted for predicted, _ in pairs) / len(pairs)
        actual_mean = sum(actual for _, actual in pairs) / len(pairs)
        numerator = sum(
            (predicted - predicted_mean) * (actual - actual_mean)
            for predicted, actual in pairs
        )
        predicted_denom = sum((predicted - predicted_mean) ** 2 for predicted, _ in pairs)
        actual_denom = sum((actual - actual_mean) ** 2 for _, actual in pairs)
        if predicted_denom <= 0 or actual_denom <= 0:
            return 0.0
        return numerator / math.sqrt(predicted_denom * actual_denom)

    def _coerce_int(self, value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, Real):
            return int(float(value))
        if isinstance(value, str):
            return int(value)
        raise TypeError(f"Expected int-compatible value, got {type(value).__name__}")

    def _coerce_float(self, value: object) -> float:
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, Real):
            return float(value)
        if isinstance(value, str):
            return float(value)
        raise TypeError(f"Expected float-compatible value, got {type(value).__name__}")

    def _optional_int(self, value: object) -> int | None:
        if value in {None, "", "None"}:
            return None
        return self._coerce_int(value)
