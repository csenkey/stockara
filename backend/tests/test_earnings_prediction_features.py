"""Tests for the cutoff-safe earnings-event feature snapshot contract.

Requirement EARN-5.1 (immutable candidate strategy plus feature schema) and the
earnings design rule that report content published after the prediction cutoff
is excluded by construction.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.models.schemas import (
    EarningsConsensusFeatures,
    EarningsDateConfidence,
    EarningsEstimateRevision,
    EarningsEventFeatureSnapshot,
    EarningsFeatureProvenance,
    EarningsGuidanceEvidence,
    EarningsHistoryFeatures,
    EarningsMarketContextFeatures,
    EarningsReactionHistorySummary,
)

EVENT_DATE = date(2026, 9, 9)
CUTOFF = datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)
STRATEGY_ID = "analysis_strategy_2026_09_05_earnings_event_v1"


def _consensus(**overrides) -> EarningsConsensusFeatures:
    values = {
        "availability": "complete",
        "eps_consensus": Decimal("1.42"),
        "eps_estimate_count": 28,
        "eps_estimate_dispersion": Decimal("0.04"),
        "eps_revision_count": 5,
        "eps_revision_net_direction": "up",
        "revision_lookback_days": 30,
        "provenance": [
            EarningsFeatureProvenance(
                source="alpha_vantage",
                observed_at=CUTOFF - timedelta(days=1),
            )
        ],
    }
    values.update(overrides)
    return EarningsConsensusFeatures(**values)


def _history(**overrides) -> EarningsHistoryFeatures:
    values = {
        "availability": "complete",
        "quarters_available": 8,
        "eps_surprise_sample_count": 8,
        "eps_beat_count": 6,
        "mean_eps_surprise_percent": Decimal("3.10"),
        "reaction_summaries": [
            EarningsReactionHistorySummary(
                window="[0,+1]",
                basis="broad_market_adjusted",
                sample_count=8,
                mean_return_percent=Decimal("1.20"),
                positive_event_percent=Decimal("62.50"),
            )
        ],
    }
    values.update(overrides)
    return EarningsHistoryFeatures(**values)


def _market_context(**overrides) -> EarningsMarketContextFeatures:
    values = {
        "availability": "complete",
        "price_as_of_session": date(2026, 9, 8),
        "adjusted_close_price": Decimal("231.50"),
        "trailing_return_20d_percent": Decimal("4.10"),
        "realized_volatility_20d_percent": Decimal("22.30"),
        "average_dollar_volume_20d": Decimal("9500000000"),
    }
    values.update(overrides)
    return EarningsMarketContextFeatures(**values)


def _snapshot(**overrides) -> EarningsEventFeatureSnapshot:
    values = {
        "snapshot_id": "AAPL-2026-09-09-v1",
        "strategy_id": STRATEGY_ID,
        "ticker": "AAPL",
        "canonical_event_id": "aapl-2026q3",
        "event_date": EVENT_DATE,
        "date_confidence": EarningsDateConfidence.HIGH,
        "reported_timing": "after_market",
        "prediction_cutoff": CUTOFF,
        "provider_snapshot_hash": "a1b2c3d4e5f6a7b8",
        "consensus": _consensus(),
        "history": _history(),
        "market_context": _market_context(),
        "evidence_quality": "high",
        "created_at": CUTOFF,
    }
    values.update(overrides)
    return EarningsEventFeatureSnapshot(**values)


def test_snapshot_accepts_cutoff_safe_evidence():
    snapshot = _snapshot()

    assert snapshot.schema_version == "1.0"
    assert snapshot.ticker == "AAPL"
    assert snapshot.history.reaction_summaries[0].positive_event_percent == Decimal(
        "62.50"
    )


def test_snapshot_is_immutable():
    snapshot = _snapshot()

    with pytest.raises(ValueError):
        snapshot.evidence_quality = "low"


def test_snapshot_rejects_revision_published_after_cutoff():
    leaked = _consensus(
        estimate_revisions=[
            EarningsEstimateRevision(
                metric="eps",
                current_value=Decimal("1.50"),
                source_url="https://example.com/revision",
                observed_at=CUTOFF + timedelta(hours=1),
            )
        ]
    )

    with pytest.raises(ValueError, match="after the prediction cutoff"):
        _snapshot(consensus=leaked)


def test_snapshot_rejects_guidance_published_after_cutoff():
    leaked = _consensus(
        guidance_evidence=[
            EarningsGuidanceEvidence(
                metric="revenue",
                direction="raised",
                summary="Management raised full-year revenue guidance.",
                source_url="https://example.com/guidance",
                published_at=CUTOFF + timedelta(minutes=5),
            )
        ]
    )

    with pytest.raises(ValueError, match="after the prediction cutoff"):
        _snapshot(consensus=leaked)


def test_snapshot_rejects_evidence_without_timezone():
    unprovable = _consensus(
        estimate_revisions=[
            EarningsEstimateRevision(
                metric="eps",
                current_value=Decimal("1.50"),
                source_url="https://example.com/revision",
                observed_at=datetime(2026, 9, 7, 12, 0),
            )
        ]
    )

    with pytest.raises(ValueError, match="needs a timezone"):
        _snapshot(consensus=unprovable)


def test_before_open_report_requires_cutoff_on_an_earlier_date():
    with pytest.raises(ValueError, match="before its report date"):
        _snapshot(
            reported_timing="before_market",
            prediction_cutoff=datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc),
        )


def test_unknown_timing_uses_the_conservative_before_open_rule():
    with pytest.raises(ValueError, match="before its report date"):
        _snapshot(
            reported_timing="unknown",
            prediction_cutoff=datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc),
        )


def test_after_close_report_may_be_predicted_earlier_the_same_day():
    snapshot = _snapshot(
        prediction_cutoff=datetime(2026, 9, 9, 19, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 9, 9, 19, 0, tzinfo=timezone.utc),
    )

    assert snapshot.prediction_cutoff.date() == EVENT_DATE


def test_after_close_report_cannot_be_predicted_once_it_is_published():
    with pytest.raises(ValueError, match="cannot be predicted after"):
        _snapshot(
            prediction_cutoff=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
        )


def test_snapshot_timestamps_require_a_timezone():
    with pytest.raises(ValueError, match="must include a timezone"):
        _snapshot(prediction_cutoff=datetime(2026, 9, 8, 20, 0))


def test_snapshot_must_name_an_analysis_strategy():
    with pytest.raises(ValueError, match="analysis_strategy_"):
        _snapshot(strategy_id="earnings_event_v1")


def test_snapshot_without_any_feature_family_is_insufficient():
    empty = {
        "consensus": EarningsConsensusFeatures(availability="missing"),
        "history": EarningsHistoryFeatures(availability="missing"),
        "market_context": EarningsMarketContextFeatures(availability="missing"),
    }

    with pytest.raises(ValueError, match="insufficient evidence"):
        _snapshot(evidence_quality="high", **empty)

    assert _snapshot(evidence_quality="insufficient", **empty).evidence_quality == (
        "insufficient"
    )


def test_reaction_summary_without_samples_reports_no_statistics():
    with pytest.raises(ValueError, match="without samples"):
        EarningsReactionHistorySummary(
            window="[0,+1]",
            basis="raw",
            sample_count=0,
            mean_return_percent=Decimal("1.20"),
        )


def test_history_beat_count_cannot_exceed_its_sample_count():
    with pytest.raises(ValueError, match="cannot exceed"):
        EarningsHistoryFeatures(
            availability="partial",
            quarters_available=4,
            eps_surprise_sample_count=4,
            eps_beat_count=5,
        )


def test_history_statistics_require_at_least_one_sample():
    with pytest.raises(ValueError, match="at least one historical surprise sample"):
        EarningsHistoryFeatures(
            availability="partial",
            eps_surprise_sample_count=0,
            mean_eps_surprise_percent=Decimal("2.00"),
        )


def test_implied_move_requires_its_licensed_source():
    with pytest.raises(ValueError, match="licensed options data source"):
        EarningsMarketContextFeatures(
            availability="partial",
            implied_move_percent=Decimal("6.50"),
        )


def test_feature_provenance_requires_a_timezone():
    with pytest.raises(ValueError, match="must include a timezone"):
        EarningsFeatureProvenance(
            source="alpha_vantage",
            observed_at=datetime(2026, 9, 7, 12, 0),
        )
