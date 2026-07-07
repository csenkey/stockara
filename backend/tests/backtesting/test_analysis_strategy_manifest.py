from pathlib import Path

import pytest

from backend.src.backtesting.analysis_strategy import (
    AnalysisStrategyManifest,
    load_analysis_strategy_manifest,
)


def test_loads_current_yaml_strategy_manifest():
    manifest = load_analysis_strategy_manifest(
        Path("configs/analysis-strategies/analysis_strategy_current.yaml")
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
