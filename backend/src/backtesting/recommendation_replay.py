"""Offline recommendation replay adapters.

Backtests must not call OpenAI or other live AI providers until a future task
adds explicit, budgeted execution. This module only supports stored responses.
"""

from datetime import date

from backend.src.backtesting.models import ReplayRecommendation


class LiveAIReplayDisabledError(RuntimeError):
    """Raised when a caller attempts live AI replay from the backtester."""


class StoredRecommendationReplay:
    """Point-in-time replay using precomputed recommendation fixtures."""

    def __init__(self, recommendations: list[ReplayRecommendation]) -> None:
        self._by_key: dict[tuple[str, date, str], ReplayRecommendation] = {}
        for recommendation in recommendations:
            key = (
                recommendation.analysis_strategy_id,
                recommendation.recommendation_date,
                recommendation.ticker.upper(),
            )
            self._by_key[key] = recommendation

    def get(
        self, *, analysis_strategy_id: str, recommendation_date: date, ticker: str
    ) -> ReplayRecommendation | None:
        return self._by_key.get((analysis_strategy_id, recommendation_date, ticker.upper()))


def assert_live_ai_replay_disabled() -> None:
    raise LiveAIReplayDisabledError(
        "Live historical AI replay is disabled for backtesting. "
        "Use fixture_only or s3_cache_only recommendation replay."
    )

