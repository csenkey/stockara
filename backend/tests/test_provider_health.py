"""Tests for collection health classification."""

from src.models.schemas import CollectionTickerHealth
from src.services.provider_health import classify_collection_health


def test_classifies_healthy_state():
    assert classify_collection_health(None) == CollectionTickerHealth.HEALTHY
    assert classify_collection_health("recovered") == CollectionTickerHealth.HEALTHY


def test_classifies_rate_limit_state():
    assert (
        classify_collection_health("HTTP 429 rate limit exceeded")
        == CollectionTickerHealth.RATE_LIMITED
    )
    assert (
        classify_collection_health("daily quota reached")
        == CollectionTickerHealth.RATE_LIMITED
    )


def test_classifies_symbol_mapping_state():
    assert (
        classify_collection_health("provider symbol mapping missing")
        == CollectionTickerHealth.SYMBOL_MAPPING_NEEDED
    )


def test_classifies_unsupported_and_inactive_states():
    assert (
        classify_collection_health("no_data")
        == CollectionTickerHealth.PROVIDER_UNSUPPORTED
    )
    assert (
        classify_collection_health("ticker appears delisted")
        == CollectionTickerHealth.INACTIVE_OR_DELISTED
    )


def test_unknown_failure_is_transient():
    assert (
        classify_collection_health("all_providers_failed")
        == CollectionTickerHealth.TRANSIENT_FAILURE
    )
