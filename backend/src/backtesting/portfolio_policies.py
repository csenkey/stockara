"""Portfolio policy identifiers and small rule helpers."""

from backend.src.backtesting.models import RecommendationRisk, ReplayRecommendation


def accepts_buy(policy_id: str, recommendation: ReplayRecommendation, *, company_size: str) -> bool:
    """Return whether a policy accepts a BUY recommendation.

    This is intentionally narrow until the full policy engine is implemented.
    """
    if recommendation.action != "BUY":
        return False
    normalized_policy = policy_id.lower()
    normalized_size = company_size.lower()
    if normalized_policy == "conservative":
        return (
            recommendation.risk == RecommendationRisk.LOW
            and normalized_size == "blue_chip"
            and recommendation.ai_review_status == "passed"
        )
    if normalized_policy == "balanced":
        return recommendation.risk in {RecommendationRisk.LOW, RecommendationRisk.MEDIUM} and (
            normalized_size in {"blue_chip", "mid_cap"}
        )
    if normalized_policy == "aggressive":
        return True
    raise ValueError(f"Unknown portfolio policy: {policy_id}")

