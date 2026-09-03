"""Tests for earnings reaction artifact contracts and publication safety."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.services.earnings_event_study import build_earnings_event_reaction
from src.services.earnings_reaction_artifacts import (
    build_earnings_reaction_artifacts,
    publish_earnings_reaction_artifacts,
)


def _rows(multiplier: Decimal = Decimal(1)) -> list[dict]:
    return [
        {
            "trading_date": date(2026, 1, day),
            "adjusted_close_price": Decimal(100 + day) * multiplier,
            "volume": 1000 if day != 25 else 1500,
        }
        for day in range(1, 32)
    ]


def _reaction(report_day: int):
    return build_earnings_event_reaction(
        ticker="NVDA",
        report_date=date(2026, 1, report_day),
        time_of_day="before_market",
        stock_rows=_rows(),
        broad_market_rows=_rows(Decimal(2)),
        sector_rows=_rows(Decimal(3)),
        sector_benchmark_ticker="XLK",
    )


def test_artifacts_include_per_event_payload_and_ticker_window_summary():
    artifacts = build_earnings_reaction_artifacts(
        [_reaction(10), _reaction(11)],
        as_of=date(2026, 2, 1),
        generated_at=datetime(2026, 2, 1, 12, tzinfo=timezone.utc),
    )

    assert artifacts["index"]["event_count"] == 2
    assert artifacts["index"]["ticker_count"] == 1
    assert len(artifacts["events"]) == 2
    ticker = artifacts["tickers"]["NVDA"]
    assert ticker["summary"]["event_count"] == 2
    assert len(ticker["summary"]["window_statistics"]) == 5
    assert [item["window"] for item in ticker["summary"]["window_statistics"]] == [
        "[-5,-1]",
        "[-1,+1]",
        "[0,+1]",
        "[+1,+5]",
        "[+1,+20]",
    ]
    assert ticker["summary"]["window_statistics"][0]["raw"]["sample_count"] >= 0
    assert len(ticker["reactions"]) == 2


@patch("src.services.earnings_reaction_artifacts.safe_publish_json_artifact")
def test_scoped_publication_never_replaces_global_latest(mock_publish):
    artifacts = build_earnings_reaction_artifacts(
        [_reaction(10)],
        as_of=date(2026, 2, 1),
    )

    publish_earnings_reaction_artifacts(
        bucket="bucket",
        artifacts=artifacts,
        artifact_scope="manual batch 1",
        publish_latest=False,
    )

    keys = [call.args[1] for call in mock_publish.call_args_list]
    assert keys
    assert all("task_id=manual-batch-1" in key for key in keys)
    assert "earnings/reactions/latest.json" not in keys


@patch("src.services.earnings_reaction_artifacts.safe_publish_json_artifact")
def test_full_publication_writes_latest_and_by_ticker(mock_publish):
    artifacts = build_earnings_reaction_artifacts(
        [_reaction(10)],
        as_of=date(2026, 2, 1),
    )

    publish_earnings_reaction_artifacts(
        bucket="bucket",
        artifacts=artifacts,
        publish_latest=True,
    )

    keys = [call.args[1] for call in mock_publish.call_args_list]
    assert "earnings/reactions/latest.json" in keys
    assert "earnings/reactions/current/by-ticker/NVDA.json" in keys
    assert any(key.startswith("earnings/reactions/current/events/NVDA/") for key in keys)


def test_scoped_publication_rejects_latest_overwrite():
    artifacts = build_earnings_reaction_artifacts(
        [_reaction(10)],
        as_of=date(2026, 2, 1),
    )

    with pytest.raises(ValueError, match="scoped reaction artifacts"):
        publish_earnings_reaction_artifacts(
            bucket="bucket",
            artifacts=artifacts,
            artifact_scope="manual",
            publish_latest=True,
        )
