from pathlib import Path

import pytest

from backend.src.backtesting.analysis_strategy import (
    AnalysisStrategyManifest,
    load_analysis_strategy_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EARNINGS_STRATEGY_PATH = (
    REPO_ROOT
    / "configs/analysis-strategies/analysis_strategy_2026_09_05_earnings_event_v1.yaml"
)


def _manifest_payload(**overrides) -> dict:
    payload = {
        "analysis_strategy": {
            "id": "analysis_strategy_fixture",
            "status": "candidate",
            "git_commit": "abc123",
            "created_at": "2026-09-05",
            "description": "fixture",
        },
        "preselection": {"flow_version": "v1"},
        "evidence": {
            "required": ["ohlcv_30d"],
            "missing_evidence_behavior": "suppress",
        },
        "recommendation_ai": {"enabled": False},
        "review_ai": {"enabled": False},
    }
    payload.update(overrides)
    return payload


def _earnings_prediction_payload(**overrides) -> dict:
    payload = {
        "feature_schema_version": "1.0",
        "shadow_mode": True,
        "influences_production": False,
        "scope": {
            "universe": "active_watchlist",
            "horizon_days": 7,
            "minimum_date_confidence": "medium",
        },
        "targets": {
            "surprise_targets": ["eps_surprise_direction"],
            "reaction_windows": ["[0,+1]"],
            "reaction_basis": "broad_market_adjusted",
        },
        "evaluation": {
            "protocol": "walk_forward",
            "min_training_events": 500,
            "min_evaluation_events": 200,
            "required_metrics": ["brier_score"],
        },
        "costs": {
            "commission_percent": 1.0,
            "spread_percent": 0.05,
            "slippage_percent": 0.05,
        },
        "promotion_gates": {"status": "proposed", "min_scored_events": 200},
    }
    payload.update(overrides)
    return payload


def test_loads_current_yaml_strategy_manifest():
    manifest = load_analysis_strategy_manifest(
        REPO_ROOT / "configs/analysis-strategies/analysis_strategy_current.yaml"
    )

    assert manifest.strategy_id == "analysis_strategy_current"
    assert manifest.analysis_strategy.status == "baseline"
    assert "ohlcv_30d" in manifest.evidence.required
    assert manifest.recommendation_ai.enabled is True


def test_strategy_id_must_be_stable():
    with pytest.raises(ValueError, match="analysis_strategy_"):
        AnalysisStrategyManifest.model_validate(
            {
                "analysis_strategy": {
                    "id": "current",
                    "status": "baseline",
                    "git_commit": "abc123",
                    "created_at": "2026-07-07",
                    "description": "bad",
                },
                "preselection": {"flow_version": "v1"},
                "evidence": {"required": ["ohlcv_30d"], "missing_evidence_behavior": "suppress"},
                "recommendation_ai": {"model": "fixture", "prompt_template": "fixture"},
                "review_ai": {"model": "fixture", "prompt_template": "fixture"},
            }
        )


def test_loads_candidate_earnings_event_strategy():
    manifest = load_analysis_strategy_manifest(EARNINGS_STRATEGY_PATH)
    prediction = manifest.earnings_event_prediction

    assert manifest.strategy_id == "analysis_strategy_2026_09_05_earnings_event_v1"
    assert manifest.analysis_strategy.status == "candidate"
    assert manifest.recommendation_ai.enabled is False
    assert manifest.review_ai.enabled is False
    assert prediction is not None
    assert prediction.shadow_mode is True
    assert prediction.influences_production is False
    assert prediction.feature_schema_version == "1.0"
    assert prediction.scope.horizon_days == 7
    assert prediction.promotion_gates.status == "proposed"


def test_manifest_thresholds_load_as_numbers_not_text():
    manifest = load_analysis_strategy_manifest(EARNINGS_STRATEGY_PATH)
    gates = manifest.earnings_event_prediction.promotion_gates

    assert gates.max_brier_score == 0.24
    assert gates.min_directional_precision == 0.55
    assert manifest.earnings_event_prediction.costs.spread_percent == 0.05


def test_disabled_ai_stage_may_omit_its_model():
    manifest = AnalysisStrategyManifest.model_validate(_manifest_payload())

    assert manifest.recommendation_ai.model is None


def test_enabled_ai_stage_requires_a_model():
    with pytest.raises(ValueError, match="requires a model identifier"):
        AnalysisStrategyManifest.model_validate(
            _manifest_payload(recommendation_ai={"enabled": True})
        )


def test_shadow_strategy_cannot_declare_production_influence():
    with pytest.raises(ValueError, match="must not influence production"):
        AnalysisStrategyManifest.model_validate(
            _manifest_payload(
                earnings_event_prediction=_earnings_prediction_payload(
                    shadow_mode=True, influences_production=True
                )
            )
        )


def test_only_a_promoted_strategy_may_influence_production():
    payload = _manifest_payload(
        earnings_event_prediction=_earnings_prediction_payload(
            shadow_mode=False, influences_production=True
        )
    )

    with pytest.raises(ValueError, match="only a promoted analysis strategy"):
        AnalysisStrategyManifest.model_validate(payload)

    payload["analysis_strategy"]["status"] = "promoted"
    manifest = AnalysisStrategyManifest.model_validate(payload)

    assert manifest.earnings_event_prediction.influences_production is True


def test_strategy_must_require_ohlcv_30d():
    with pytest.raises(ValueError, match="ohlcv_30d"):
        AnalysisStrategyManifest.model_validate(
            {
                "analysis_strategy": {
                    "id": "analysis_strategy_missing_prices",
                    "status": "candidate",
                    "git_commit": "abc123",
                    "created_at": "2026-07-07",
                    "description": "bad",
                },
                "preselection": {"flow_version": "v1"},
                "evidence": {"required": ["news_7d"], "missing_evidence_behavior": "suppress"},
                "recommendation_ai": {"model": "fixture", "prompt_template": "fixture"},
                "review_ai": {"model": "fixture", "prompt_template": "fixture"},
            }
        )
