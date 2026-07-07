from datetime import date
from decimal import Decimal

import pytest

from backend.src.backtesting.models import (
    RecommendationAction,
    RecommendationRisk,
    ReplayRecommendation,
)
from backend.src.backtesting.recommendation_replay import (
    LiveAIReplayDisabledError,
    StoredRecommendationReplay,
    assert_live_ai_replay_disabled,
)


def test_stored_recommendation_replay_is_point_in_time_keyed():
    recommendation = ReplayRecommendation(
        recommendation_id="rec_1",
        analysis_strategy_id="analysis_strategy_current",
        recommendation_date=date(2022, 3, 4),
        ticker="aapl",
        action=RecommendationAction.BUY,
        risk=RecommendationRisk.LOW,
        confidence=Decimal("0.85"),
        model_id="fixture-model",
        evidence_hash="hash",
    )
    replay = StoredRecommendationReplay([recommendation])

    assert (
        replay.get(
            analysis_strategy_id="analysis_strategy_current",
            recommendation_date=date(2022, 3, 4),
            ticker="AAPL",
        )
        == recommendation
    )
    assert (
        replay.get(
            analysis_strategy_id="analysis_strategy_current",
            recommendation_date=date(2022, 3, 5),
            ticker="AAPL",
        )
        is None
    )


def test_live_ai_replay_is_disabled():
    with pytest.raises(LiveAIReplayDisabledError, match="disabled"):
        assert_live_ai_replay_disabled()
