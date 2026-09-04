"""Operator-script tests for production earnings reconciliation."""

import argparse
import importlib.util
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from src.db.connection import store

SCRIPT = (
    Path(__file__).parents[2] / "scripts" / "reconcile_earnings_event_reaction.py"
)
SPEC = importlib.util.spec_from_file_location(
    "reconcile_earnings_event_reaction_script", SCRIPT
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _args(**overrides) -> argparse.Namespace:
    values = {
        "ticker": "aapl",
        "report_date": date(2024, 8, 1),
        "timing": "after_market",
        "timing_evidence_url": "https://www.sec.gov/example",
        "timing_evidence_timestamp": datetime(
            2024, 8, 1, 20, 30, tzinfo=timezone.utc
        ),
        "tolerance": Decimal("0.0001"),
        "publish_artifact": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _rows():
    return [
        {
            "trading_date": date(2024, 6, 24) + timedelta(days=index),
            "adjusted_close_price": Decimal(100 + index),
            "volume": 1000 + index,
        }
        for index in range(70)
    ]


@patch("src.db.connection.DatabasePool.close")
@patch("src.db.connection.DatabasePool.initialize")
def test_script_reads_stored_prices_without_publishing(
    mock_initialize, mock_close
):
    with patch.object(store, "get_stock_data", return_value=_rows()) as mock_prices:
        result = MODULE.run_reconciliation(_args())

    assert result["status"] == "passed"
    assert result["ticker"] == "AAPL"
    mock_prices.assert_called_once_with(
        "AAPL", date(2024, 6, 17), date(2024, 9, 15)
    )
    mock_initialize.assert_called_once()
    mock_close.assert_called_once()


@patch("src.services.static_artifacts.safe_publish_json_artifact")
@patch("src.db.connection.DatabasePool.close")
@patch("src.db.connection.DatabasePool.initialize")
def test_script_publishes_traceable_artifact(
    mock_initialize, mock_close, mock_publish, monkeypatch
):
    monkeypatch.setenv("STOCKARA_ARTIFACT_BUCKET", "bucket")
    with patch.object(store, "get_stock_data", return_value=_rows()):
        result = MODULE.run_reconciliation(_args(publish_artifact=True))

    assert result["status"] == "passed"
    mock_publish.assert_called_once()
    assert mock_publish.call_args.args[:2] == (
        "bucket",
        "earnings/reactions/reconciliations/AAPL/2024-08-01.json",
    )


@patch("src.db.connection.DatabasePool.close")
@patch("src.db.connection.DatabasePool.initialize")
def test_script_requires_bucket_before_claiming_publication(
    mock_initialize, mock_close, monkeypatch
):
    monkeypatch.delenv("STOCKARA_ARTIFACT_BUCKET", raising=False)
    with patch.object(store, "get_stock_data", return_value=_rows()):
        with pytest.raises(ValueError, match="STOCKARA_ARTIFACT_BUCKET"):
            MODULE.run_reconciliation(_args(publish_artifact=True))

    mock_initialize.assert_called_once()
    mock_close.assert_called_once()
