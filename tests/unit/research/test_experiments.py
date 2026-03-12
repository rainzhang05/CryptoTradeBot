"""Unit tests for staged research sweep planning and ranking."""

from __future__ import annotations

import json
from pathlib import Path

from tradebot.config import load_config
from tradebot.research.experiments import ResearchSweepService


def _write_config(root: Path) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "settings.yaml"
    config_path.write_text(
        """
app: {}
runtime: {}
exchange: {}
strategy:
  fixed_universe: [BTC, ETH, BNB, XRP, SOL, ADA, DOGE, TRX, AVAX, LINK]
research: {}
model: {}
backtest: {}
alerts: {}
paths: {}
""",
        encoding="utf-8",
    )
    return config_path


def _dataset_result(
    *,
    dataset_track: str,
    cagr: float,
    calmar_ratio: float,
    total_return: float,
) -> dict[str, object]:
    return {
        "experiment_id": f"{dataset_track}-baseline",
        "status": "completed",
        "stage": 1,
        "stage_name": "stage_1_dataset_comparison",
        "dataset_track": dataset_track,
        "track_type": "research",
        "mode": "rule_only",
        "cagr": cagr,
        "calmar_ratio": calmar_ratio,
        "total_return": total_return,
        "fee_to_gross_pnl_ratio": 0.2,
        "rule_switch_key": "regime=1,entry=1,vol=1,reduce=1",
        "head_key": "none",
        "aggression_preset": "current",
        "spec_json": json.dumps(
            {
                "stage": 1,
                "stage_name": "stage_1_dataset_comparison",
                "track_type": "research",
                "mode": "rule_only",
                "dataset_track": dataset_track,
                "assets": ["BTC", "ETH"],
                "aggression_preset": "current",
                "label_preset": "current",
                "stress_multiplier": 1.0,
                "research_profile": {
                    "regime_layer_enabled": True,
                    "entry_filter_layer_enabled": True,
                    "volatility_layer_enabled": True,
                    "gradual_reduction_layer_enabled": True,
                    "expected_return_head_enabled": False,
                    "downside_risk_head_enabled": False,
                    "sell_risk_head_enabled": False,
                },
            },
            sort_keys=True,
        ),
    }


def _leaderboard_result(
    *,
    experiment_id: str,
    mode: str,
    cagr: float,
    total_return: float,
    max_drawdown: float,
    decision_count: int,
    paired_key: str,
    parent_experiment_id: str = "",
    stage_name: str = "stage_4_hybrid_tuning",
) -> dict[str, object]:
    return {
        "experiment_id": experiment_id,
        "status": "completed",
        "stage": 4,
        "stage_name": stage_name,
        "track_type": "research",
        "mode": mode,
        "dataset_track": "dynamic_universe_kraken_only",
        "aggression_preset": "current",
        "rule_switch_key": "regime=1,entry=1,vol=1,reduce=1",
        "head_key": "expected_return+downside_risk+sell_risk" if mode == "hybrid" else "none",
        "label_preset": "current",
        "model_family": "ridge_logistic" if mode == "hybrid" else "",
        "total_return": total_return,
        "cagr": cagr,
        "calmar_ratio": 1.2,
        "max_drawdown": max_drawdown,
        "fee_to_gross_pnl_ratio": 0.15,
        "decision_count": decision_count,
        "holdout_year": None,
        "stress_multiplier": 1.0,
        "base_pairing_key": paired_key,
        "parent_experiment_id": parent_experiment_id,
        "report_file": "/tmp/report.json",
        "disqualification_reasons_json": "[]",
        "spec_json": json.dumps(
            {
                "stage": 4,
                "stage_name": stage_name,
                "track_type": "research",
                "mode": mode,
                "dataset_track": "dynamic_universe_kraken_only",
                "assets": ["BTC", "ETH"],
                "aggression_preset": "current",
                "label_preset": "current",
                "model_family": "ridge_logistic" if mode == "hybrid" else "",
                "model_hyperparameters": {},
                "hybrid_overrides": {},
                "holdout_year": None,
                "start_timestamp": None,
                "end_timestamp": None,
                "stress_multiplier": 1.0,
                "parent_experiment_id": parent_experiment_id or None,
                "research_profile": {
                    "regime_layer_enabled": True,
                    "entry_filter_layer_enabled": True,
                    "volatility_layer_enabled": True,
                    "gradual_reduction_layer_enabled": True,
                    "expected_return_head_enabled": mode == "hybrid",
                    "downside_risk_head_enabled": mode == "hybrid",
                    "sell_risk_head_enabled": mode == "hybrid",
                },
            },
            sort_keys=True,
        ),
    }


def test_stage2_rule_switch_ablation_expands_top_three_dataset_tracks(tmp_path: Path) -> None:
    service = ResearchSweepService(
        load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    )
    stage1_results = [
        _dataset_result(
            dataset_track="official_fixed_10",
            cagr=0.01,
            calmar_ratio=0.2,
            total_return=0.02,
        ),
        _dataset_result(
            dataset_track="subset_9_no_bnb",
            cagr=0.10,
            calmar_ratio=1.1,
            total_return=0.18,
        ),
        _dataset_result(
            dataset_track="subset_7_pre_2021",
            cagr=0.08,
            calmar_ratio=0.9,
            total_return=0.16,
        ),
        _dataset_result(
            dataset_track="dynamic_universe_kraken_only",
            cagr=0.12,
            calmar_ratio=1.0,
            total_return=0.17,
        ),
    ]

    specs = service._stage2_rule_switch_ablation(stage1_results)

    assert len(specs) == 48
    assert {spec.dataset_track for spec in specs} == {
        "subset_9_no_bnb",
        "subset_7_pre_2021",
        "dynamic_universe_kraken_only",
    }
    assert all(spec.mode == "rule_only" for spec in specs)


def test_leaderboard_shortlist_requires_hybrid_uplift_and_positive_holdouts(
    tmp_path: Path,
) -> None:
    service = ResearchSweepService(
        load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    )
    paired_key = "pair-1"
    results = [
        _leaderboard_result(
            experiment_id="rule-win",
            mode="rule_only",
            cagr=0.18,
            total_return=0.55,
            max_drawdown=-0.20,
            decision_count=220,
            paired_key=paired_key,
            stage_name="stage_2_aggression_rerun",
        ),
        _leaderboard_result(
            experiment_id="hybrid-weak",
            mode="hybrid",
            cagr=0.12,
            total_return=0.40,
            max_drawdown=-0.22,
            decision_count=220,
            paired_key=paired_key,
        ),
        _leaderboard_result(
            experiment_id="hybrid-strong",
            mode="hybrid",
            cagr=0.24,
            total_return=0.70,
            max_drawdown=-0.23,
            decision_count=220,
            paired_key="pair-2",
        ),
        _leaderboard_result(
            experiment_id="rule-pair-2",
            mode="rule_only",
            cagr=0.15,
            total_return=0.45,
            max_drawdown=-0.20,
            decision_count=220,
            paired_key="pair-2",
            stage_name="stage_2_aggression_rerun",
        ),
        {
            **_leaderboard_result(
                experiment_id="hybrid-strong-holdout-2024",
                mode="hybrid",
                cagr=0.10,
                total_return=0.12,
                max_drawdown=-0.10,
                decision_count=160,
                paired_key="pair-2",
                parent_experiment_id="hybrid-strong",
                stage_name="stage_5_holdout_confirmation",
            ),
            "holdout_year": 2024,
        },
        {
            **_leaderboard_result(
                experiment_id="hybrid-strong-holdout-2025",
                mode="hybrid",
                cagr=0.10,
                total_return=0.08,
                max_drawdown=-0.10,
                decision_count=160,
                paired_key="pair-2",
                parent_experiment_id="hybrid-strong",
                stage_name="stage_5_holdout_confirmation",
            ),
            "holdout_year": 2025,
        },
    ]

    leaderboard = service._leaderboard(results)

    assert leaderboard["rule_only"][0]["experiment_id"] == "rule-win"
    assert leaderboard["hybrid"][0]["experiment_id"] == "hybrid-strong"
    assert leaderboard["shortlist"][0]["experiment_id"] == "hybrid-strong"


def test_stage4_hybrid_tuning_generates_seeded_parameter_search_specs(
    tmp_path: Path,
) -> None:
    service = ResearchSweepService(
        load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    )
    stage3_result = _leaderboard_result(
        experiment_id="hybrid-stage3",
        mode="hybrid",
        cagr=0.20,
        total_return=0.50,
        max_drawdown=-0.20,
        decision_count=220,
        paired_key="pair-3",
        stage_name="stage_3_ml_effectiveness",
    )
    stage3_result["stage"] = 3

    specs = service._stage4_hybrid_tuning([stage3_result])

    assert len(specs) == service.random_search_count
    assert all(spec.stage_name == "stage_4_hybrid_tuning" for spec in specs)
    assert all(spec.parent_experiment_id == "hybrid-stage3" for spec in specs)
    assert all(spec.model_family == "ridge_logistic" for spec in specs)
    assert any(spec.hybrid_overrides for spec in specs)


def test_stage5_generates_holdout_and_stress_specs_from_top_candidates(
    tmp_path: Path,
) -> None:
    service = ResearchSweepService(
        load_config(config_path=_write_config(tmp_path), env_path=tmp_path / ".env")
    )
    candidate = _leaderboard_result(
        experiment_id="candidate-stage4",
        mode="hybrid",
        cagr=0.22,
        total_return=0.60,
        max_drawdown=-0.18,
        decision_count=240,
        paired_key="pair-4",
        stage_name="stage_4_hybrid_tuning",
    )
    candidate["stage"] = 4
    service._holdout_windows = lambda spec: [(2024, 1_704_067_200, 1_705_622_400)]  # type: ignore[method-assign]

    holdout_specs = service._stage5_holdout_confirmation([candidate])
    stress_results = [
        {
            **candidate,
            "experiment_id": "candidate-stage4-holdout",
            "stage": 5,
            "stage_name": "stage_5_holdout_confirmation",
            "holdout_year": 2024,
            "start_timestamp": 1_704_067_200,
            "end_timestamp": 1_705_622_400,
            "parent_experiment_id": "candidate-stage4",
            "spec_json": json.dumps(
                holdout_specs[0].to_dict(),
                sort_keys=True,
            ),
        }
    ]
    stress_specs = service._stage5_stress_confirmation(stress_results)

    assert len(holdout_specs) == 1
    assert holdout_specs[0].holdout_year == 2024
    assert holdout_specs[0].parent_experiment_id == "candidate-stage4"
    assert len(stress_specs) == 1
    assert stress_specs[0].stage_name == "stage_5_stress_confirmation"
    assert stress_specs[0].stress_multiplier == 2.0
