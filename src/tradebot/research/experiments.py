"""Research-only staged experiment sweeps and reporting."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from numbers import Real
from pathlib import Path
from typing import Any, cast

from tradebot.backtest.service import BacktestService
from tradebot.backtest.storage import write_csv_rows
from tradebot.config import AppConfig
from tradebot.constants import FIXED_UNIVERSE
from tradebot.data.storage import write_json
from tradebot.logging_config import get_logger
from tradebot.model.service import ModelService
from tradebot.research.service import ResearchService
from tradebot.strategy.models import ResearchStrategyProfile

DATASET_TRACKS: dict[str, dict[str, object]] = {
    "official_fixed_10": {
        "assets": FIXED_UNIVERSE,
        "track_type": "official",
        "description": "Strict fixed-universe aligned-history dataset.",
    },
    "subset_9_no_bnb": {
        "assets": tuple(asset for asset in FIXED_UNIVERSE if asset != "BNB"),
        "track_type": "research",
        "description": "Fixed-universe subset without BNB.",
    },
    "subset_7_pre_2021": {
        "assets": ("BTC", "ETH", "XRP", "ADA", "DOGE", "TRX", "LINK"),
        "track_type": "research",
        "description": "Longer-history subset focused on earlier Kraken listings.",
    },
    "dynamic_universe_kraken_only": {
        "assets": FIXED_UNIVERSE,
        "track_type": "research",
        "description": "Dynamic Kraken-only universe with per-asset activation dates.",
    },
}

AGGRESSION_PRESETS = ("current", "higher_exposure", "concentrated", "tighter_trend")
LABEL_PRESETS = ("short", "current", "medium")
MODEL_FAMILIES = (
    "ridge_logistic",
    "elastic_net_logistic",
    "random_forest",
    "hist_gradient_boosting",
)
RESULTS_FIELDNAMES = [
    "experiment_id",
    "stage",
    "stage_name",
    "status",
    "track_type",
    "mode",
    "dataset_track",
    "assets_json",
    "aggression_preset",
    "rule_switch_key",
    "head_key",
    "label_preset",
    "model_family",
    "holdout_year",
    "stress_multiplier",
    "base_pairing_key",
    "parent_experiment_id",
    "dataset_id",
    "model_id",
    "validation_promotion_eligible",
    "validation_expected_return_correlation",
    "validation_downside_brier_score",
    "validation_sell_brier_score",
    "backtest_run_id",
    "report_file",
    "decision_count",
    "fill_count",
    "final_equity_usd",
    "total_return",
    "max_drawdown",
    "total_fees_usd",
    "start_timestamp",
    "end_timestamp",
    "cagr",
    "calmar_ratio",
    "annualized_volatility",
    "daily_sharpe",
    "turnover",
    "fee_to_gross_pnl_ratio",
    "days_invested",
    "trades_per_year",
    "benchmark_cash_total_return",
    "benchmark_btc_total_return",
    "benchmark_equal_weight_total_return",
    "yearly_returns_json",
    "benchmarks_json",
    "disqualified",
    "disqualification_reasons_json",
    "error",
    "spec_json",
]


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Real):
        return int(float(value))
    return int(str(value))


def _coerce_float(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, Real):
        return float(value)
    return float(str(value))


@dataclass(frozen=True)
class ExperimentSpec:
    """One staged research experiment definition."""

    stage: int
    stage_name: str
    track_type: str
    mode: str
    dataset_track: str
    assets: tuple[str, ...]
    aggression_preset: str = "current"
    research_profile: ResearchStrategyProfile = field(
        default_factory=ResearchStrategyProfile
    )
    label_preset: str = "current"
    model_family: str | None = None
    model_hyperparameters: dict[str, object] = field(default_factory=dict)
    hybrid_overrides: dict[str, object] = field(default_factory=dict)
    holdout_year: int | None = None
    start_timestamp: int | None = None
    end_timestamp: int | None = None
    stress_multiplier: float = 1.0
    parent_experiment_id: str | None = None

    def experiment_id(self) -> str:
        payload = json.dumps(self.identity_payload(), sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()[:12]
        prefix = "hyb" if self.mode == "hybrid" else "rul"
        return f"{prefix}_{digest}"

    def base_pairing_key(self) -> str:
        payload = {
            "dataset_track": self.dataset_track,
            "assets": list(self.assets),
            "aggression_preset": self.aggression_preset,
            "rule_switch_key": self.rule_switch_key(),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[
            :16
        ]

    def head_key(self) -> str:
        enabled: list[str] = []
        if self.research_profile.expected_return_head_enabled:
            enabled.append("expected_return")
        if self.research_profile.downside_risk_head_enabled:
            enabled.append("downside_risk")
        if self.research_profile.sell_risk_head_enabled:
            enabled.append("sell_risk")
        return "none" if not enabled else "+".join(enabled)

    def rule_switch_key(self) -> str:
        return ",".join(
            [
                f"regime={int(self.research_profile.regime_layer_enabled)}",
                f"entry={int(self.research_profile.entry_filter_layer_enabled)}",
                f"vol={int(self.research_profile.volatility_layer_enabled)}",
                f"reduce={int(self.research_profile.gradual_reduction_layer_enabled)}",
            ]
        )

    def identity_payload(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "dataset_track": self.dataset_track,
            "assets": list(self.assets),
            "aggression_preset": self.aggression_preset,
            "research_profile": self.research_profile.to_dict(),
            "label_preset": self.label_preset,
            "model_family": self.model_family,
            "model_hyperparameters": self.model_hyperparameters,
            "hybrid_overrides": self.hybrid_overrides,
            "holdout_year": self.holdout_year,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "stress_multiplier": self.stress_multiplier,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id(),
            "stage": self.stage,
            "stage_name": self.stage_name,
            "track_type": self.track_type,
            "mode": self.mode,
            "dataset_track": self.dataset_track,
            "assets": list(self.assets),
            "aggression_preset": self.aggression_preset,
            "rule_switch_key": self.rule_switch_key(),
            "head_key": self.head_key(),
            "label_preset": self.label_preset,
            "model_family": self.model_family,
            "model_hyperparameters": self.model_hyperparameters,
            "hybrid_overrides": self.hybrid_overrides,
            "holdout_year": self.holdout_year,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "stress_multiplier": self.stress_multiplier,
            "base_pairing_key": self.base_pairing_key(),
            "parent_experiment_id": self.parent_experiment_id,
            "research_profile": self.research_profile.to_dict(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> ExperimentSpec:
        research_profile_payload = cast(
            dict[str, object],
            payload.get("research_profile", {}),
        )
        return cls(
            stage=_coerce_int(payload["stage"]),
            stage_name=str(payload["stage_name"]),
            track_type=str(payload["track_type"]),
            mode=str(payload["mode"]),
            dataset_track=str(payload["dataset_track"]),
            assets=tuple(str(asset) for asset in cast(list[object], payload["assets"])),
            aggression_preset=str(payload.get("aggression_preset", "current")),
            research_profile=ResearchStrategyProfile(
                regime_layer_enabled=bool(
                    research_profile_payload.get("regime_layer_enabled", True)
                ),
                entry_filter_layer_enabled=bool(
                    research_profile_payload.get("entry_filter_layer_enabled", True)
                ),
                volatility_layer_enabled=bool(
                    research_profile_payload.get("volatility_layer_enabled", True)
                ),
                gradual_reduction_layer_enabled=bool(
                    research_profile_payload.get("gradual_reduction_layer_enabled", True)
                ),
                expected_return_head_enabled=bool(
                    research_profile_payload.get("expected_return_head_enabled", True)
                ),
                downside_risk_head_enabled=bool(
                    research_profile_payload.get("downside_risk_head_enabled", True)
                ),
                sell_risk_head_enabled=bool(
                    research_profile_payload.get("sell_risk_head_enabled", True)
                ),
            ),
            label_preset=str(payload.get("label_preset", "current")),
            model_family=(
                None
                if payload.get("model_family") in {None, ""}
                else str(payload["model_family"])
            ),
            model_hyperparameters=cast(
                dict[str, object],
                payload.get("model_hyperparameters", {}),
            ),
            hybrid_overrides=cast(dict[str, object], payload.get("hybrid_overrides", {})),
            holdout_year=(
                None
                if payload.get("holdout_year") in {None, ""}
                else _coerce_int(payload["holdout_year"])
            ),
            start_timestamp=(
                None
                if payload.get("start_timestamp") in {None, ""}
                else _coerce_int(payload["start_timestamp"])
            ),
            end_timestamp=(
                None
                if payload.get("end_timestamp") in {None, ""}
                else _coerce_int(payload["end_timestamp"])
            ),
            stress_multiplier=_coerce_float(payload.get("stress_multiplier", 1.0)),
            parent_experiment_id=(
                None
                if payload.get("parent_experiment_id") in {None, ""}
                else str(payload["parent_experiment_id"])
            ),
        )


class ResearchSweepService:
    """Run and summarize staged research sweeps."""

    random_seed = 1729
    random_search_count = 120
    minimum_shortlist_decisions = 150
    maximum_shortlist_drawdown = -0.45
    maximum_hybrid_drawdown_gap = 0.05
    sweep_version = "research_sweep_v1"

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.paths = config.resolved_paths()
        self.logger = get_logger("tradebot.research.sweep")
        self._holdout_cache: dict[str, list[tuple[int, int, int]]] = {}
        self.maximum_hybrid_drawdown_gap = config.model.promotion_max_drawdown_gap

    def run_sweep(
        self,
        *,
        preset: str = "broad_staged",
        resume: bool = False,
        max_workers: int = 1,
        limit: int | None = None,
    ) -> dict[str, object]:
        if preset != "broad_staged":
            raise ValueError(f"Unsupported research preset: {preset}")

        sweep_id = self._sweep_id(preset=preset)
        sweep_dir = self.paths.experiments_dir / sweep_id
        manifest_path = sweep_dir / "manifest.json"
        results_path = sweep_dir / "results.csv"
        leaderboard_path = sweep_dir / "leaderboard.json"

        results = self._load_results(results_path) if resume else []
        manifest = self._initial_manifest(
            sweep_id=sweep_id,
            preset=preset,
            resume=resume,
            max_workers=max_workers,
            limit=limit,
            existing_results=results,
        )
        executed_count = 0
        skipped_existing = 0
        limit_remaining = None if limit is None else max(limit - len(results), 0)
        result_ids = {str(result["experiment_id"]) for result in results}

        for stage, stage_name, builder in self._phase_plans():
            if limit_remaining == 0:
                break
            planned_specs = builder(results)
            stage_specs = [
                spec for spec in planned_specs if spec.experiment_id() not in result_ids
            ]
            skipped_existing += max(0, len(planned_specs) - len(stage_specs))
            self._merge_manifest_specs(manifest, stage_specs)
            self._update_manifest_run_state(
                manifest,
                status="running",
                stage=stage_name,
                completed_count=len(results),
                limit_remaining=limit_remaining,
            )
            write_json(manifest_path, manifest)
            for spec in stage_specs:
                if limit_remaining == 0:
                    break
                result = self._execute_experiment(
                    sweep_id=sweep_id,
                    stage=stage,
                    spec=spec,
                )
                results.append(result)
                result_ids.add(str(result["experiment_id"]))
                executed_count += 1
                self._write_results(results_path, results)
                if limit_remaining is not None:
                    limit_remaining = max(limit_remaining - 1, 0)
            self._persist_progress(
                sweep_id=sweep_id,
                sweep_dir=sweep_dir,
                manifest=manifest,
                results=results,
                manifest_path=manifest_path,
                leaderboard_path=leaderboard_path,
            )

        self._update_manifest_run_state(
            manifest,
            status="partial" if limit_remaining == 0 else "completed",
            stage=None,
            completed_count=len(results),
            limit_remaining=limit_remaining,
        )
        write_json(manifest_path, manifest)
        report = self._persist_progress(
            sweep_id=sweep_id,
            sweep_dir=sweep_dir,
            manifest=manifest,
            results=results,
            manifest_path=manifest_path,
            leaderboard_path=leaderboard_path,
        )
        leaderboard_payload = cast(dict[str, list[dict[str, object]]], report["leaderboard"])
        rule_only_entries = leaderboard_payload["rule_only"]
        hybrid_entries = leaderboard_payload["hybrid"]
        run_metadata = cast(dict[str, object], manifest["run_metadata"])
        return {
            "sweep_id": sweep_id,
            "preset": preset,
            "manifest_file": str(manifest_path),
            "results_file": str(results_path),
            "leaderboard_file": str(leaderboard_path),
            "report_file": report["report_file"],
            "completed_experiments": len(results),
            "executed_experiments": executed_count,
            "skipped_existing": skipped_existing,
            "limit_reached": limit_remaining == 0,
            "status": run_metadata["status"],
            "top_rule_only_experiment_id": (
                rule_only_entries[0]["experiment_id"] if rule_only_entries else None
            ),
            "top_hybrid_experiment_id": (
                hybrid_entries[0]["experiment_id"] if hybrid_entries else None
            ),
        }

    def load_report(self, sweep_id: str | None = None) -> dict[str, object]:
        if sweep_id is None:
            pointer_path = self.paths.artifacts_dir / "reports" / "research" / "latest_sweep.json"
            if not pointer_path.exists():
                raise FileNotFoundError("No research sweep report exists yet")
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            sweep_id = str(pointer["sweep_id"])

        sweep_dir = self.paths.experiments_dir / sweep_id
        manifest_path = sweep_dir / "manifest.json"
        results_path = sweep_dir / "results.csv"
        leaderboard_path = sweep_dir / "leaderboard.json"
        if not manifest_path.exists() or not results_path.exists():
            raise FileNotFoundError(f"Research sweep does not exist: {sweep_id}")

        manifest = cast(dict[str, object], json.loads(manifest_path.read_text(encoding="utf-8")))
        results = self._load_results(results_path)
        return self._build_report(
            sweep_id=sweep_id,
            manifest=manifest,
            results=results,
            leaderboard_path=leaderboard_path,
        )

    def _phase_plans(self) -> list[tuple[int, str, Any]]:
        return [
            (0, "stage_0_baselines", self._stage0_baselines),
            (1, "stage_1_dataset_comparison", self._stage1_dataset_comparison),
            (2, "stage_2_rule_switch_ablation", self._stage2_rule_switch_ablation),
            (2, "stage_2_aggression_rerun", self._stage2_aggression_rerun),
            (3, "stage_3_ml_effectiveness", self._stage3_ml_effectiveness),
            (4, "stage_4_hybrid_tuning", self._stage4_hybrid_tuning),
            (5, "stage_5_holdout_confirmation", self._stage5_holdout_confirmation),
            (5, "stage_5_stress_confirmation", self._stage5_stress_confirmation),
        ]

    def _stage0_baselines(self, results: list[dict[str, object]]) -> list[ExperimentSpec]:
        del results
        return [
            self._rule_only_spec(
                stage=0,
                stage_name="stage_0_baselines",
                dataset_track="dynamic_universe_kraken_only",
            ),
            self._hybrid_spec(
                stage=0,
                stage_name="stage_0_baselines",
                dataset_track="dynamic_universe_kraken_only",
            ),
            self._rule_only_spec(
                stage=0,
                stage_name="stage_0_baselines",
                dataset_track="subset_9_no_bnb",
            ),
            self._hybrid_spec(
                stage=0,
                stage_name="stage_0_baselines",
                dataset_track="subset_9_no_bnb",
            ),
            self._rule_only_spec(
                stage=0,
                stage_name="stage_0_baselines",
                dataset_track="official_fixed_10",
            ),
            self._hybrid_spec(
                stage=0,
                stage_name="stage_0_baselines",
                dataset_track="official_fixed_10",
            ),
        ]

    def _stage1_dataset_comparison(self, results: list[dict[str, object]]) -> list[ExperimentSpec]:
        del results
        specs: list[ExperimentSpec] = []
        for dataset_track in DATASET_TRACKS:
            specs.append(
                self._rule_only_spec(
                    stage=1,
                    stage_name="stage_1_dataset_comparison",
                    dataset_track=dataset_track,
                )
            )
            specs.append(
                self._hybrid_spec(
                    stage=1,
                    stage_name="stage_1_dataset_comparison",
                    dataset_track=dataset_track,
                )
            )
        return specs

    def _stage2_rule_switch_ablation(
        self,
        results: list[dict[str, object]],
    ) -> list[ExperimentSpec]:
        dataset_tracks = self._top_dataset_tracks(results, top_n=3)
        profiles = self._rule_switch_profiles(heads_enabled=False)
        specs: list[ExperimentSpec] = []
        for dataset_track in dataset_tracks:
            for profile in profiles:
                specs.append(
                    self._rule_only_spec(
                        stage=2,
                        stage_name="stage_2_rule_switch_ablation",
                        dataset_track=dataset_track,
                        research_profile=profile,
                    )
                )
        return specs

    def _stage2_aggression_rerun(
        self,
        results: list[dict[str, object]],
    ) -> list[ExperimentSpec]:
        seeds = self._top_rule_switches_per_track(results)
        specs: list[ExperimentSpec] = []
        for result in seeds:
            spec = self._spec_from_result(result)
            for aggression_preset in AGGRESSION_PRESETS:
                specs.append(
                    self._rule_only_spec(
                        stage=2,
                        stage_name="stage_2_aggression_rerun",
                        dataset_track=spec.dataset_track,
                        research_profile=spec.research_profile,
                        aggression_preset=aggression_preset,
                    )
                )
        return specs

    def _stage3_ml_effectiveness(
        self,
        results: list[dict[str, object]],
    ) -> list[ExperimentSpec]:
        top_rule_configs = self._top_results(
            [
                result
                for result in results
                if result.get("status") == "completed"
                and result.get("mode") == "rule_only"
                and self._optional_int(result.get("stage")) == 2
            ],
            top_n=5,
        )
        specs: list[ExperimentSpec] = []
        for result in top_rule_configs:
            base_spec = self._spec_from_result(result)
            for head_profile in self._head_ablation_profiles(base_spec.research_profile):
                if self._profile_head_key(head_profile) == "none":
                    specs.append(
                        self._rule_only_spec(
                            stage=3,
                            stage_name="stage_3_ml_effectiveness",
                            dataset_track=base_spec.dataset_track,
                            research_profile=self._disable_heads(base_spec.research_profile),
                            aggression_preset=base_spec.aggression_preset,
                        )
                    )
                    continue
                for label_preset in LABEL_PRESETS:
                    for family in MODEL_FAMILIES:
                        specs.append(
                            self._hybrid_spec(
                                stage=3,
                                stage_name="stage_3_ml_effectiveness",
                                dataset_track=base_spec.dataset_track,
                                research_profile=head_profile,
                                aggression_preset=base_spec.aggression_preset,
                                label_preset=label_preset,
                                model_family=family,
                            )
                        )
        return specs

    def _stage4_hybrid_tuning(
        self,
        results: list[dict[str, object]],
    ) -> list[ExperimentSpec]:
        top_hybrids = self._top_results(
            [
                result
                for result in results
                if result.get("status") == "completed"
                and result.get("mode") == "hybrid"
                and self._optional_int(result.get("stage")) == 3
            ],
            top_n=20,
        )
        specs: list[ExperimentSpec] = []
        for result in top_hybrids:
            base_spec = self._spec_from_result(result)
            for overrides, hyperparameters in self._random_hybrid_parameter_sets(base_spec):
                specs.append(
                    self._hybrid_spec(
                        stage=4,
                        stage_name="stage_4_hybrid_tuning",
                        dataset_track=base_spec.dataset_track,
                        research_profile=base_spec.research_profile,
                        aggression_preset=base_spec.aggression_preset,
                        label_preset=base_spec.label_preset,
                        model_family=base_spec.model_family,
                        model_hyperparameters=hyperparameters,
                        hybrid_overrides=overrides,
                        parent_experiment_id=str(result["experiment_id"]),
                    )
                )
        return specs

    def _stage5_holdout_confirmation(
        self,
        results: list[dict[str, object]],
    ) -> list[ExperimentSpec]:
        top_candidates = self._top_results(
            [
                result
                for result in results
                if result.get("status") == "completed"
                and result.get("holdout_year") is None
                and self._optional_float(result.get("stress_multiplier"), default=1.0) == 1.0
                and self._optional_int(result.get("stage")) in {2, 3, 4}
            ],
            top_n=10,
        )
        specs: list[ExperimentSpec] = []
        for result in top_candidates:
            base_spec = self._spec_from_result(result)
            for year, start_timestamp, end_timestamp in self._holdout_windows(base_spec):
                specs.append(
                    self._derived_holdout_spec(
                        stage_name="stage_5_holdout_confirmation",
                        base_spec=base_spec,
                        stage=5,
                        year=year,
                        start_timestamp=start_timestamp,
                        end_timestamp=end_timestamp,
                        stress_multiplier=1.0,
                        parent_experiment_id=str(result["experiment_id"]),
                    )
                )
        return specs

    def _stage5_stress_confirmation(
        self,
        results: list[dict[str, object]],
    ) -> list[ExperimentSpec]:
        holdout_results = [
            result
            for result in results
            if result.get("status") == "completed"
            and str(result.get("stage_name")) == "stage_5_holdout_confirmation"
        ]
        specs: list[ExperimentSpec] = []
        for result in holdout_results:
            base_spec = self._spec_from_result(result)
            specs.append(
                self._derived_holdout_spec(
                    stage_name="stage_5_stress_confirmation",
                    base_spec=base_spec,
                    stage=5,
                    year=self._optional_int(result["holdout_year"]) or 0,
                    start_timestamp=self._optional_int(result["start_timestamp"]) or 0,
                    end_timestamp=self._optional_int(result["end_timestamp"]) or 0,
                    stress_multiplier=2.0,
                    parent_experiment_id=str(
                        result.get("parent_experiment_id") or result["experiment_id"]
                    ),
                )
            )
        return specs

    def _rule_only_spec(
        self,
        *,
        stage: int,
        stage_name: str,
        dataset_track: str,
        research_profile: ResearchStrategyProfile | None = None,
        aggression_preset: str = "current",
    ) -> ExperimentSpec:
        track = DATASET_TRACKS[dataset_track]
        return ExperimentSpec(
            stage=stage,
            stage_name=stage_name,
            track_type=str(track["track_type"]),
            mode="rule_only",
            dataset_track=dataset_track,
            assets=cast(tuple[str, ...], track["assets"]),
            aggression_preset=aggression_preset,
            research_profile=research_profile or self._default_rule_profile(),
        )

    def _hybrid_spec(
        self,
        *,
        stage: int,
        stage_name: str,
        dataset_track: str,
        research_profile: ResearchStrategyProfile | None = None,
        aggression_preset: str = "current",
        label_preset: str = "current",
        model_family: str | None = "ridge_logistic",
        model_hyperparameters: dict[str, object] | None = None,
        hybrid_overrides: dict[str, object] | None = None,
        parent_experiment_id: str | None = None,
    ) -> ExperimentSpec:
        track = DATASET_TRACKS[dataset_track]
        return ExperimentSpec(
            stage=stage,
            stage_name=stage_name,
            track_type=str(track["track_type"]),
            mode="hybrid",
            dataset_track=dataset_track,
            assets=cast(tuple[str, ...], track["assets"]),
            aggression_preset=aggression_preset,
            research_profile=research_profile or ResearchStrategyProfile(),
            label_preset=label_preset,
            model_family=model_family,
            model_hyperparameters=model_hyperparameters or {},
            hybrid_overrides=hybrid_overrides or {},
            parent_experiment_id=parent_experiment_id,
        )

    def _derived_holdout_spec(
        self,
        *,
        stage_name: str,
        base_spec: ExperimentSpec,
        stage: int,
        year: int,
        start_timestamp: int,
        end_timestamp: int,
        stress_multiplier: float,
        parent_experiment_id: str,
    ) -> ExperimentSpec:
        return ExperimentSpec(
            stage=stage,
            stage_name=stage_name,
            track_type=base_spec.track_type,
            mode=base_spec.mode,
            dataset_track=base_spec.dataset_track,
            assets=base_spec.assets,
            aggression_preset=base_spec.aggression_preset,
            research_profile=base_spec.research_profile,
            label_preset=base_spec.label_preset,
            model_family=base_spec.model_family,
            model_hyperparameters=base_spec.model_hyperparameters,
            hybrid_overrides=base_spec.hybrid_overrides,
            holdout_year=year,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            stress_multiplier=stress_multiplier,
            parent_experiment_id=parent_experiment_id,
        )

    def _execute_experiment(
        self,
        *,
        sweep_id: str,
        stage: int,
        spec: ExperimentSpec,
    ) -> dict[str, object]:
        del stage
        experiment_id = spec.experiment_id()
        result: dict[str, object] = {
            "experiment_id": experiment_id,
            "stage": spec.stage,
            "stage_name": spec.stage_name,
            "status": "failed",
            "track_type": spec.track_type,
            "mode": spec.mode,
            "dataset_track": spec.dataset_track,
            "assets_json": json.dumps(list(spec.assets)),
            "aggression_preset": spec.aggression_preset,
            "rule_switch_key": spec.rule_switch_key(),
            "head_key": spec.head_key(),
            "label_preset": spec.label_preset,
            "model_family": spec.model_family or "",
            "holdout_year": spec.holdout_year,
            "stress_multiplier": spec.stress_multiplier,
            "base_pairing_key": spec.base_pairing_key(),
            "parent_experiment_id": spec.parent_experiment_id or "",
            "dataset_id": "",
            "model_id": "",
            "validation_promotion_eligible": "",
            "validation_expected_return_correlation": "",
            "validation_downside_brier_score": "",
            "validation_sell_brier_score": "",
            "backtest_run_id": "",
            "report_file": "",
            "decision_count": "",
            "fill_count": "",
            "final_equity_usd": "",
            "total_return": "",
            "max_drawdown": "",
            "total_fees_usd": "",
            "start_timestamp": spec.start_timestamp or "",
            "end_timestamp": spec.end_timestamp or "",
            "cagr": "",
            "calmar_ratio": "",
            "annualized_volatility": "",
            "daily_sharpe": "",
            "turnover": "",
            "fee_to_gross_pnl_ratio": "",
            "days_invested": "",
            "trades_per_year": "",
            "benchmark_cash_total_return": "",
            "benchmark_btc_total_return": "",
            "benchmark_equal_weight_total_return": "",
            "yearly_returns_json": "",
            "benchmarks_json": "",
            "disqualified": "",
            "disqualification_reasons_json": "[]",
            "error": "",
            "spec_json": json.dumps(spec.to_dict(), sort_keys=True),
        }
        try:
            experiment_config = self._experiment_config(sweep_id=sweep_id, spec=spec)
            model_id: str | None = None
            validation_payload: dict[str, object] | None = None
            if spec.mode == "hybrid":
                family = spec.model_family or "ridge_logistic"
                model_service = ModelService(experiment_config)
                training = model_service.train_model(
                    assets=spec.assets,
                    dataset_track=spec.dataset_track,
                    family=family,
                    hyperparameters=spec.model_hyperparameters,
                )
                model_id = training.model_id
                validation_payload = model_service.validate_model(model_id=model_id).to_dict()

            backtest_service = BacktestService(experiment_config)
            summary = backtest_service.run_backtest(
                assets=spec.assets,
                force_features=False,
                model_id=model_id,
                use_active_model=False,
                dataset_track=spec.dataset_track,
                research_profile=spec.research_profile,
                start_timestamp=spec.start_timestamp,
                end_timestamp=spec.end_timestamp,
            )
            report = backtest_service.load_backtest_report(summary.run_id)
            benchmarks = cast(dict[str, dict[str, object]], report.get("benchmarks", {}))
            result |= {
                "status": "completed",
                "dataset_id": summary.dataset_id,
                "model_id": model_id or "",
                "backtest_run_id": summary.run_id,
                "report_file": summary.report_file,
                "decision_count": summary.decision_count,
                "fill_count": summary.fill_count,
                "final_equity_usd": summary.final_equity_usd,
                "total_return": summary.total_return,
                "max_drawdown": summary.max_drawdown,
                "total_fees_usd": summary.total_fees_usd,
                "start_timestamp": summary.start_timestamp or "",
                "end_timestamp": summary.end_timestamp or "",
                "cagr": summary.cagr if summary.cagr is not None else "",
                "calmar_ratio": summary.calmar_ratio if summary.calmar_ratio is not None else "",
                "annualized_volatility": (
                    summary.annualized_volatility
                    if summary.annualized_volatility is not None
                    else ""
                ),
                "daily_sharpe": summary.daily_sharpe if summary.daily_sharpe is not None else "",
                "turnover": summary.turnover if summary.turnover is not None else "",
                "fee_to_gross_pnl_ratio": (
                    summary.fee_to_gross_pnl_ratio
                    if summary.fee_to_gross_pnl_ratio is not None
                    else ""
                ),
                "days_invested": summary.days_invested if summary.days_invested is not None else "",
                "trades_per_year": (
                    summary.trades_per_year if summary.trades_per_year is not None else ""
                ),
                "benchmark_cash_total_return": self._nested_float(
                    benchmarks,
                    "cash",
                    "total_return",
                ),
                "benchmark_btc_total_return": self._nested_float(
                    benchmarks,
                    "btc_buy_and_hold",
                    "total_return",
                ),
                "benchmark_equal_weight_total_return": self._nested_float(
                    benchmarks,
                    "equal_weight_active_universe_buy_and_hold",
                    "total_return",
                ),
                "yearly_returns_json": json.dumps(report.get("yearly_returns", {}), sort_keys=True),
                "benchmarks_json": json.dumps(benchmarks, sort_keys=True),
            }
            if validation_payload is not None:
                result |= {
                    "validation_promotion_eligible": str(
                        bool(validation_payload["promotion_eligible"])
                    ).lower(),
                    "validation_expected_return_correlation": validation_payload[
                        "expected_return_correlation"
                    ],
                    "validation_downside_brier_score": validation_payload[
                        "downside_brier_score"
                    ],
                    "validation_sell_brier_score": validation_payload["sell_brier_score"],
                }
            disqualification_reasons = self._disqualification_reasons(result)
            result["disqualified"] = str(bool(disqualification_reasons)).lower()
            result["disqualification_reasons_json"] = json.dumps(
                disqualification_reasons,
                sort_keys=True,
            )
            return result
        except Exception as exc:
            self.logger.exception(
                "research experiment failed",
                extra={"experiment_id": experiment_id, "dataset_track": spec.dataset_track},
            )
            result["error"] = str(exc)
            return result

    def _experiment_config(self, *, sweep_id: str, spec: ExperimentSpec) -> AppConfig:
        config = self.config.model_copy(deep=True)
        scratch_root = self.paths.experiments_dir / sweep_id / "scratch"
        config.paths.artifacts_dir = scratch_root / "artifacts"
        config.paths.models_dir = scratch_root / "models"
        config.paths.model_reports_dir = scratch_root / "reports" / "models"
        config.paths.logs_dir = scratch_root / "logs"
        config.paths.state_dir = scratch_root / "state"
        self._apply_aggression_preset(config, spec.aggression_preset)
        self._apply_label_preset(config, spec.label_preset)
        self._apply_hybrid_overrides(config, spec.hybrid_overrides)
        if spec.stress_multiplier != 1.0:
            config.backtest.fee_rate_bps *= spec.stress_multiplier
            config.backtest.slippage_bps *= spec.stress_multiplier
        return config

    def _apply_aggression_preset(self, config: AppConfig, aggression_preset: str) -> None:
        if aggression_preset == "current":
            return
        if aggression_preset == "higher_exposure":
            config.backtest.neutral_exposure = 0.75
            config.backtest.defensive_exposure = 0.50
            config.strategy.elevated_caution_exposure_multiplier = 1.00
            config.strategy.reduced_aggressiveness_exposure_multiplier = 0.80
            config.strategy.catastrophe_exposure_multiplier = 0.50
            return
        if aggression_preset == "concentrated":
            config.backtest.max_positions = 3
            config.backtest.max_asset_weight = 0.35
            config.backtest.rebalance_threshold = 0.05
            config.strategy.reduction_target_fraction = 0.25
            config.strategy.held_asset_score_bonus = 0.03
            return
        if aggression_preset == "tighter_trend":
            config.strategy.entry_momentum_floor = 0.02
            config.strategy.entry_trend_gap_floor = 0.01
            config.strategy.hold_momentum_floor = -0.01
            config.strategy.hold_trend_gap_floor = -0.01
            config.strategy.max_realized_volatility = 0.18
            config.strategy.reduction_volatility_threshold = 0.10
            return
        raise ValueError(f"Unsupported aggression preset: {aggression_preset}")

    def _apply_label_preset(self, config: AppConfig, label_preset: str) -> None:
        if label_preset == "current":
            return
        if label_preset == "short":
            config.research.forward_return_days = 3
            config.research.downside_lookahead_days = 5
            config.research.downside_threshold = 0.06
            config.research.sell_lookahead_days = 10
            config.research.sell_drawdown_threshold = 0.08
            config.research.sell_return_threshold = -0.01
            return
        if label_preset == "medium":
            config.research.forward_return_days = 10
            config.research.downside_lookahead_days = 15
            config.research.downside_threshold = 0.10
            config.research.sell_lookahead_days = 30
            config.research.sell_drawdown_threshold = 0.15
            config.research.sell_return_threshold = -0.03
            return
        raise ValueError(f"Unsupported label preset: {label_preset}")

    def _apply_hybrid_overrides(
        self,
        config: AppConfig,
        hybrid_overrides: dict[str, object],
    ) -> None:
        for field_name, value in hybrid_overrides.items():
            setattr(config.model, field_name, value)

    def _default_rule_profile(self) -> ResearchStrategyProfile:
        return ResearchStrategyProfile(
            expected_return_head_enabled=False,
            downside_risk_head_enabled=False,
            sell_risk_head_enabled=False,
        )

    def _disable_heads(
        self,
        profile: ResearchStrategyProfile,
    ) -> ResearchStrategyProfile:
        return ResearchStrategyProfile(
            regime_layer_enabled=profile.regime_layer_enabled,
            entry_filter_layer_enabled=profile.entry_filter_layer_enabled,
            volatility_layer_enabled=profile.volatility_layer_enabled,
            gradual_reduction_layer_enabled=profile.gradual_reduction_layer_enabled,
            expected_return_head_enabled=False,
            downside_risk_head_enabled=False,
            sell_risk_head_enabled=False,
        )

    def _rule_switch_profiles(
        self,
        *,
        heads_enabled: bool,
    ) -> list[ResearchStrategyProfile]:
        profiles: list[ResearchStrategyProfile] = []
        for regime_enabled in (False, True):
            for entry_enabled in (False, True):
                for volatility_enabled in (False, True):
                    for gradual_enabled in (False, True):
                        profiles.append(
                            ResearchStrategyProfile(
                                regime_layer_enabled=regime_enabled,
                                entry_filter_layer_enabled=entry_enabled,
                                volatility_layer_enabled=volatility_enabled,
                                gradual_reduction_layer_enabled=gradual_enabled,
                                expected_return_head_enabled=heads_enabled,
                                downside_risk_head_enabled=heads_enabled,
                                sell_risk_head_enabled=heads_enabled,
                            )
                        )
        return profiles

    def _head_ablation_profiles(
        self,
        rule_profile: ResearchStrategyProfile,
    ) -> list[ResearchStrategyProfile]:
        profiles: list[ResearchStrategyProfile] = []
        for expected_return_enabled in (False, True):
            for downside_enabled in (False, True):
                for sell_enabled in (False, True):
                    profiles.append(
                        ResearchStrategyProfile(
                            regime_layer_enabled=rule_profile.regime_layer_enabled,
                            entry_filter_layer_enabled=rule_profile.entry_filter_layer_enabled,
                            volatility_layer_enabled=rule_profile.volatility_layer_enabled,
                            gradual_reduction_layer_enabled=(
                                rule_profile.gradual_reduction_layer_enabled
                            ),
                            expected_return_head_enabled=expected_return_enabled,
                            downside_risk_head_enabled=downside_enabled,
                            sell_risk_head_enabled=sell_enabled,
                        )
                    )
        return profiles

    def _profile_head_key(self, profile: ResearchStrategyProfile) -> str:
        enabled: list[str] = []
        if profile.expected_return_head_enabled:
            enabled.append("expected_return")
        if profile.downside_risk_head_enabled:
            enabled.append("downside_risk")
        if profile.sell_risk_head_enabled:
            enabled.append("sell_risk")
        return "none" if not enabled else "+".join(enabled)

    def _top_dataset_tracks(
        self,
        results: list[dict[str, object]],
        *,
        top_n: int,
    ) -> list[str]:
        candidates = [
            result
            for result in results
            if result.get("status") == "completed"
            and str(result.get("stage_name")) == "stage_1_dataset_comparison"
        ]
        best_by_track: dict[str, dict[str, object]] = {}
        for result in candidates:
            dataset_track = str(result["dataset_track"])
            current_best = best_by_track.get(dataset_track)
            if current_best is None or self._result_sort_key(result) > self._result_sort_key(
                current_best
            ):
                best_by_track[dataset_track] = result
        ranked = sorted(best_by_track.values(), key=self._result_sort_key, reverse=True)
        return [str(result["dataset_track"]) for result in ranked[:top_n]]

    def _top_rule_switches_per_track(
        self,
        results: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        initial_results = [
            result
            for result in results
            if result.get("status") == "completed"
            and str(result.get("stage_name")) == "stage_2_rule_switch_ablation"
            and result.get("mode") == "rule_only"
        ]
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for result in initial_results:
            grouped[str(result["dataset_track"])].append(result)
        selected: list[dict[str, object]] = []
        for dataset_track in sorted(grouped):
            ranked = self._top_results(grouped[dataset_track], top_n=4)
            selected.extend(ranked)
        return selected

    def _random_hybrid_parameter_sets(
        self,
        spec: ExperimentSpec,
    ) -> list[tuple[dict[str, object], dict[str, object]]]:
        seed_material = json.dumps(spec.identity_payload(), sort_keys=True).encode("utf-8")
        seed = int(hashlib.sha256(seed_material).hexdigest()[:8], 16) ^ self.random_seed
        rng = random.Random(seed)
        seen: set[str] = set()
        parameter_sets: list[tuple[dict[str, object], dict[str, object]]] = []
        while len(parameter_sets) < self.random_search_count:
            overrides: dict[str, object] = {
                "expected_return_weight": rng.choice([0.15, 0.30, 0.45, 0.60]),
                "downside_penalty_weight": rng.choice([0.10, 0.20, 0.30, 0.40]),
                "sell_risk_penalty_weight": rng.choice([0.05, 0.15, 0.25, 0.35]),
                "entry_downside_threshold": rng.choice([0.45, 0.50, 0.55, 0.60, 0.65]),
                "reduce_sell_risk_threshold": rng.choice([0.40, 0.50, 0.55, 0.60]),
                "exit_sell_risk_threshold": rng.choice([0.60, 0.70, 0.80, 0.85]),
                "exit_downside_threshold": rng.choice([0.65, 0.75, 0.85, 0.90]),
            }
            reduce_threshold = _coerce_float(overrides["reduce_sell_risk_threshold"])
            exit_sell_threshold = _coerce_float(overrides["exit_sell_risk_threshold"])
            if reduce_threshold >= exit_sell_threshold:
                continue
            entry_downside_threshold = _coerce_float(overrides["entry_downside_threshold"])
            exit_downside_threshold = _coerce_float(overrides["exit_downside_threshold"])
            if entry_downside_threshold >= exit_downside_threshold:
                continue
            hyperparameters: dict[str, object] = {}
            if spec.model_family == "elastic_net_logistic":
                hyperparameters = {
                    "elastic_net_alpha": rng.choice([1e-4, 1e-3, 1e-2]),
                    "elastic_net_l1_ratio": rng.choice([0.2, 0.5, 0.8]),
                }
            elif spec.model_family == "random_forest":
                hyperparameters = {
                    "rf_n_estimators": rng.choice([200, 500]),
                    "rf_max_depth": rng.choice([3, 5, None]),
                    "rf_min_samples_leaf": rng.choice([1, 5, 10]),
                }
            elif spec.model_family == "hist_gradient_boosting":
                hyperparameters = {
                    "hgb_learning_rate": rng.choice([0.03, 0.10]),
                    "hgb_max_depth": rng.choice([3, 5, None]),
                    "hgb_max_leaf_nodes": rng.choice([31, 63]),
                    "hgb_min_samples_leaf": rng.choice([20, 50]),
                }
            signature = json.dumps(
                {"overrides": overrides, "hyperparameters": hyperparameters},
                sort_keys=True,
            )
            if signature in seen:
                continue
            seen.add(signature)
            parameter_sets.append((overrides, hyperparameters))
        return parameter_sets

    def _holdout_windows(
        self,
        spec: ExperimentSpec,
    ) -> list[tuple[int, int, int]]:
        cache_key = json.dumps(
            {
                "dataset_track": spec.dataset_track,
                "assets": list(spec.assets),
                "label_preset": spec.label_preset,
            },
            sort_keys=True,
        )
        cached = self._holdout_cache.get(cache_key)
        if cached is not None:
            return cached

        config = self.config.model_copy(deep=True)
        self._apply_label_preset(config, spec.label_preset)
        service = ResearchService(config)
        feature_store = service.build_feature_store(
            assets=spec.assets,
            dataset_track=spec.dataset_track,
        )
        timestamps: dict[int, list[int]] = defaultdict(list)
        with Path(feature_store.dataset_file).open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                timestamp = int(str(row["timestamp"]))
                year = datetime.fromtimestamp(timestamp, tz=UTC).year
                timestamps[year].append(timestamp)

        windows = [
            (year, min(values), max(values))
            for year, values in sorted(timestamps.items())
            if len(set(values)) >= self.minimum_shortlist_decisions
        ]
        self._holdout_cache[cache_key] = windows
        return windows

    def _disqualification_reasons(self, result: dict[str, object]) -> list[str]:
        reasons: list[str] = []
        decision_count = self._optional_int(result.get("decision_count"))
        max_drawdown = self._optional_float(result.get("max_drawdown"))
        if decision_count is not None and decision_count < self.minimum_shortlist_decisions:
            reasons.append(
                f"decision_count<{self.minimum_shortlist_decisions}"
            )
        if max_drawdown is not None and max_drawdown < self.maximum_shortlist_drawdown:
            reasons.append(
                f"max_drawdown<{self.maximum_shortlist_drawdown:.2f}"
            )
        return reasons

    def _top_results(
        self,
        results: list[dict[str, object]],
        *,
        top_n: int,
    ) -> list[dict[str, object]]:
        completed = [result for result in results if result.get("status") == "completed"]
        return sorted(completed, key=self._result_sort_key, reverse=True)[:top_n]

    def _result_sort_key(self, result: dict[str, object]) -> tuple[float, float, float, float]:
        cagr = self._optional_float(result.get("cagr"))
        calmar = self._optional_float(result.get("calmar_ratio"))
        total_return = self._optional_float(result.get("total_return"))
        fee_ratio = self._optional_float(result.get("fee_to_gross_pnl_ratio"))
        return (
            -math.inf if cagr is None else cagr,
            -math.inf if calmar is None else calmar,
            -math.inf if total_return is None else total_return,
            -math.inf if fee_ratio is None else -fee_ratio,
        )

    def _build_report(
        self,
        *,
        sweep_id: str,
        manifest: dict[str, object],
        results: list[dict[str, object]],
        leaderboard_path: Path,
    ) -> dict[str, object]:
        leaderboard = self._leaderboard(results)
        write_json(leaderboard_path, leaderboard)
        comparisons_dir = self.paths.experiments_dir / sweep_id / "comparisons"
        comparisons = self._write_comparisons(comparisons_dir, results, leaderboard)
        report_path = self.paths.experiments_dir / sweep_id / "report.json"
        payload = {
            "sweep_id": sweep_id,
            "preset": manifest["preset"],
            "manifest_file": str(self.paths.experiments_dir / sweep_id / "manifest.json"),
            "results_file": str(self.paths.experiments_dir / sweep_id / "results.csv"),
            "leaderboard_file": str(leaderboard_path),
            "comparison_dir": str(comparisons_dir),
            "completed_experiments": len(results),
            "failed_experiments": sum(1 for result in results if result.get("status") == "failed"),
            "run_metadata": manifest["run_metadata"],
            "leaderboard": leaderboard,
            "comparisons": comparisons,
        }
        write_json(report_path, payload)
        payload["report_file"] = str(report_path)
        return payload

    def _persist_progress(
        self,
        *,
        sweep_id: str,
        sweep_dir: Path,
        manifest: dict[str, object],
        results: list[dict[str, object]],
        manifest_path: Path,
        leaderboard_path: Path,
    ) -> dict[str, object]:
        report = self._build_report(
            sweep_id=sweep_id,
            manifest=manifest,
            results=results,
            leaderboard_path=leaderboard_path,
        )
        latest_pointer = self.paths.artifacts_dir / "reports" / "research" / "latest_sweep.json"
        write_json(
            latest_pointer,
            {
                "sweep_id": sweep_id,
                "report_file": str(sweep_dir / "report.json"),
                "leaderboard_file": str(leaderboard_path),
                "manifest_file": str(manifest_path),
                "results_file": str(sweep_dir / "results.csv"),
            },
        )
        return report

    def _leaderboard(self, results: list[dict[str, object]]) -> dict[str, object]:
        baseline_results = [
            result
            for result in results
            if result.get("status") == "completed"
            and result.get("holdout_year") in {None, ""}
            and self._optional_float(result.get("stress_multiplier"), default=1.0) == 1.0
        ]
        rule_only = self._top_results(
            [result for result in baseline_results if result.get("mode") == "rule_only"],
            top_n=10,
        )
        hybrid = self._top_results(
            [result for result in baseline_results if result.get("mode") == "hybrid"],
            top_n=10,
        )

        results_by_pairing = {
            str(result["base_pairing_key"]): result
            for result in baseline_results
            if result.get("mode") == "rule_only"
        }
        holdouts_by_parent: dict[str, list[dict[str, object]]] = defaultdict(list)
        for result in results:
            if str(result.get("stage_name")) != "stage_5_holdout_confirmation":
                continue
            parent_id = str(result.get("parent_experiment_id") or "")
            if parent_id:
                holdouts_by_parent[parent_id].append(result)

        shortlist: list[dict[str, object]] = []
        for result in self._top_results(baseline_results, top_n=20):
            reasons = json.loads(str(result.get("disqualification_reasons_json", "[]")))
            if result.get("mode") == "hybrid":
                paired_rule = results_by_pairing.get(str(result["base_pairing_key"]))
                if paired_rule is None:
                    reasons.append("missing_paired_rule_only_baseline")
                else:
                    hybrid_cagr = self._optional_float(result.get("cagr"))
                    rule_cagr = self._optional_float(paired_rule.get("cagr"))
                    if (
                        hybrid_cagr is None
                        or rule_cagr is None
                        or hybrid_cagr <= rule_cagr
                    ):
                        reasons.append("hybrid_cagr_not_above_rule_only")
                    hybrid_total_return = self._optional_float(result.get("total_return"))
                    rule_total_return = self._optional_float(paired_rule.get("total_return"))
                    if (
                        hybrid_total_return is None
                        or rule_total_return is None
                        or hybrid_total_return <= rule_total_return
                    ):
                        reasons.append("hybrid_total_return_not_above_rule_only")
                    hybrid_drawdown = self._optional_float(result.get("max_drawdown"))
                    rule_drawdown = self._optional_float(paired_rule.get("max_drawdown"))
                    if (
                        hybrid_drawdown is not None
                        and rule_drawdown is not None
                        and hybrid_drawdown < (rule_drawdown - self.maximum_hybrid_drawdown_gap)
                    ):
                        reasons.append("hybrid_drawdown_too_much_worse")
                holdout_results = holdouts_by_parent.get(str(result["experiment_id"]), [])
                if holdout_results:
                    positive_count = 0
                    for holdout in holdout_results:
                        total_return = self._optional_float(
                            holdout.get("total_return"),
                            default=-1.0,
                        )
                        if total_return is not None and total_return > 0:
                            positive_count += 1
                    if positive_count <= len(holdout_results) / 2:
                        reasons.append("holdout_years_not_majority_positive")
                else:
                    reasons.append("missing_holdout_confirmation")

            if reasons:
                continue
            shortlist.append(result)

        return {
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "ranking": {
                "primary": "cagr",
                "secondary": ["calmar_ratio", "total_return", "fee_to_gross_pnl_ratio"],
            },
            "rule_only": [self._leaderboard_entry(result) for result in rule_only],
            "hybrid": [self._leaderboard_entry(result) for result in hybrid],
            "shortlist": [self._leaderboard_entry(result) for result in shortlist[:10]],
        }

    def _leaderboard_entry(self, result: dict[str, object]) -> dict[str, object]:
        return {
            "experiment_id": result["experiment_id"],
            "stage_name": result["stage_name"],
            "mode": result["mode"],
            "track_type": result["track_type"],
            "dataset_track": result["dataset_track"],
            "aggression_preset": result["aggression_preset"],
            "rule_switch_key": result["rule_switch_key"],
            "head_key": result["head_key"],
            "label_preset": result["label_preset"],
            "model_family": result["model_family"],
            "total_return": self._optional_float(result.get("total_return")),
            "cagr": self._optional_float(result.get("cagr")),
            "calmar_ratio": self._optional_float(result.get("calmar_ratio")),
            "max_drawdown": self._optional_float(result.get("max_drawdown")),
            "fee_to_gross_pnl_ratio": self._optional_float(
                result.get("fee_to_gross_pnl_ratio")
            ),
            "decision_count": self._optional_int(result.get("decision_count")),
            "report_file": result.get("report_file"),
            "disqualified": json.loads(str(result.get("disqualification_reasons_json", "[]"))),
        }

    def _write_comparisons(
        self,
        comparisons_dir: Path,
        results: list[dict[str, object]],
        leaderboard: dict[str, object],
    ) -> list[str]:
        comparisons_dir.mkdir(parents=True, exist_ok=True)
        index = {str(result["experiment_id"]): result for result in results}
        paths: list[str] = []
        comparison_ids = {
            str(entry["experiment_id"])
            for key in ("rule_only", "hybrid", "shortlist")
            for entry in cast(list[dict[str, object]], leaderboard[key])
        }
        holdouts_by_parent: dict[str, list[dict[str, object]]] = defaultdict(list)
        for result in results:
            parent_id = str(result.get("parent_experiment_id") or "")
            if parent_id:
                holdouts_by_parent[parent_id].append(result)

        paired_rule_index = {
            str(result["base_pairing_key"]): result
            for result in results
            if result.get("mode") == "rule_only"
            and result.get("holdout_year") in {None, ""}
            and self._optional_float(result.get("stress_multiplier"), default=1.0) == 1.0
        }
        for experiment_id in sorted(comparison_ids):
            candidate = index.get(experiment_id)
            if candidate is None:
                continue
            payload = {
                "candidate": candidate,
                "paired_rule_only": paired_rule_index.get(
                    str(candidate["base_pairing_key"])
                ),
                "holdout_results": holdouts_by_parent.get(experiment_id, []),
            }
            path = comparisons_dir / f"{experiment_id}.json"
            write_json(path, payload)
            paths.append(str(path))
        return paths

    def _merge_manifest_specs(
        self,
        manifest: dict[str, object],
        specs: list[ExperimentSpec],
    ) -> None:
        matrix = cast(list[dict[str, object]], manifest["experiment_matrix"])
        existing_ids = {str(entry["experiment_id"]) for entry in matrix}
        for spec in specs:
            payload = spec.to_dict()
            if str(payload["experiment_id"]) in existing_ids:
                continue
            matrix.append(payload)
            existing_ids.add(str(payload["experiment_id"]))

    def _initial_manifest(
        self,
        *,
        sweep_id: str,
        preset: str,
        resume: bool,
        max_workers: int,
        limit: int | None,
        existing_results: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "sweep_id": sweep_id,
            "preset": preset,
            "objective": "Maximize after-fee CAGR within Kraken spot, long-only, USD constraints.",
            "risk_profile": {
                "target": "aspirational_50_percent_cagr",
                "shortlist_constraints": {
                    "max_drawdown_floor": self.maximum_shortlist_drawdown,
                    "minimum_decision_timestamps": self.minimum_shortlist_decisions,
                    "hybrid_drawdown_gap_limit": self.maximum_hybrid_drawdown_gap,
                },
            },
            "random_seed": self.random_seed,
            "dataset_tracks": DATASET_TRACKS,
            "experiment_matrix": [],
            "run_metadata": {
                "version": self.sweep_version,
                "created_at": datetime.now(tz=UTC).isoformat(),
                "resume": resume,
                "requested_max_workers": max_workers,
                "effective_max_workers": 1,
                "limit": limit,
                "status": "pending",
                "completed_experiments": len(existing_results),
                "active_stage": None,
                "last_updated_at": datetime.now(tz=UTC).isoformat(),
            },
        }

    def _update_manifest_run_state(
        self,
        manifest: dict[str, object],
        *,
        status: str,
        stage: str | None,
        completed_count: int,
        limit_remaining: int | None,
    ) -> None:
        run_metadata = cast(dict[str, object], manifest["run_metadata"])
        run_metadata["status"] = status
        run_metadata["active_stage"] = stage
        run_metadata["completed_experiments"] = completed_count
        run_metadata["limit_remaining"] = limit_remaining
        run_metadata["last_updated_at"] = datetime.now(tz=UTC).isoformat()

    def _sweep_id(self, *, preset: str) -> str:
        payload = {
            "preset": preset,
            "sweep_version": self.sweep_version,
            "strategy": self.config.strategy.model_dump(mode="json"),
            "research": self.config.research.model_dump(mode="json"),
            "model": self.config.model.model_dump(mode="json"),
            "backtest": self.config.backtest.model_dump(mode="json"),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[
            :12
        ]
        return f"{preset}_{digest}"

    def _load_results(self, path: Path) -> list[dict[str, object]]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            return [self._deserialize_result_row(row) for row in reader]

    def _write_results(self, path: Path, results: list[dict[str, object]]) -> None:
        rows = [self._serialize_result_row(result) for result in results]
        write_csv_rows(path, fieldnames=RESULTS_FIELDNAMES, rows=rows)

    def _serialize_result_row(self, result: dict[str, object]) -> dict[str, object]:
        row: dict[str, object] = {}
        for field_name in RESULTS_FIELDNAMES:
            row[field_name] = result.get(field_name, "")
        return row

    def _deserialize_result_row(self, row: dict[str, str]) -> dict[str, object]:
        result: dict[str, object] = dict(row)
        for field_name in (
            "stage",
            "holdout_year",
            "decision_count",
            "fill_count",
            "days_invested",
            "start_timestamp",
            "end_timestamp",
        ):
            result[field_name] = (
                None if row.get(field_name, "") == "" else _coerce_int(row[field_name])
            )
        for field_name in (
            "stress_multiplier",
            "validation_expected_return_correlation",
            "validation_downside_brier_score",
            "validation_sell_brier_score",
            "final_equity_usd",
            "total_return",
            "max_drawdown",
            "total_fees_usd",
            "cagr",
            "calmar_ratio",
            "annualized_volatility",
            "daily_sharpe",
            "turnover",
            "fee_to_gross_pnl_ratio",
            "trades_per_year",
            "benchmark_cash_total_return",
            "benchmark_btc_total_return",
            "benchmark_equal_weight_total_return",
        ):
            result[field_name] = (
                None if row.get(field_name, "") == "" else _coerce_float(row[field_name])
            )
        if row.get("validation_promotion_eligible", ""):
            result["validation_promotion_eligible"] = row[
                "validation_promotion_eligible"
            ].lower() == "true"
        else:
            result["validation_promotion_eligible"] = None
        return result

    def _spec_from_result(self, result: dict[str, object]) -> ExperimentSpec:
        return ExperimentSpec.from_payload(json.loads(str(result["spec_json"])))

    @staticmethod
    def _nested_float(
        payload: dict[str, dict[str, object]],
        group: str,
        field_name: str,
    ) -> float | str:
        entry = payload.get(group)
        if entry is None or entry.get(field_name) is None:
            return ""
        return float(entry[field_name])  # type: ignore[arg-type]

    @staticmethod
    def _optional_float(value: object, *, default: float | None = None) -> float | None:
        if value in {None, ""}:
            return default
        return _coerce_float(value)

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if value in {None, ""}:
            return None
        return _coerce_int(value)
