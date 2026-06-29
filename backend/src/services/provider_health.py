"""Normalize provider and ticker collection failure health states."""

from src.models.schemas import CollectionTickerHealth


def classify_collection_health(reason: str | None) -> CollectionTickerHealth:
    """Map provider/ticker failure text into the shared collection health taxonomy."""
    text = str(reason or "").strip().lower()
    if not text or text in {"success", "succeeded", "recovered", "healthy"}:
        return CollectionTickerHealth.HEALTHY
    if "429" in text or "quota" in text or ("rate" in text and "limit" in text):
        return CollectionTickerHealth.RATE_LIMITED
    if "symbol" in text or "mapping" in text:
        return CollectionTickerHealth.SYMBOL_MAPPING_NEEDED
    if "delisted" in text or "inactive" in text:
        return CollectionTickerHealth.INACTIVE_OR_DELISTED
    if "unsupported" in text or "no_data" in text or "no data" in text:
        return CollectionTickerHealth.PROVIDER_UNSUPPORTED
    return CollectionTickerHealth.TRANSIENT_FAILURE
