"""Operator workflow safety tests for earnings event studies."""

import argparse
import importlib.util
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from src.db.connection import store


SCRIPT = Path(__file__).parents[2] / "scripts" / "build_earnings_event_studies.py"
SPEC = importlib.util.spec_from_file_location("build_earnings_event_studies", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _args(**overrides):
    values = {
        "tickers": None,
        "max_tickers": 0,
        "offset": 0,
        "lookback_days": 1825,
        "maturation_days": 35,
        "as_of": date(2026, 3, 5),
        "dry_run": True,
        "publish_artifact": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_only_uncapped_all_active_selection_is_full_universe():
    stocks = [{"ticker": "MSFT"}, {"ticker": "AAPL"}]

    selected, full = MODULE._selected_stocks(stocks, _args())
    assert [stock["ticker"] for stock in selected] == ["AAPL", "MSFT"]
    assert full is True

    _, targeted_full = MODULE._selected_stocks(stocks, _args(tickers="AAPL,MSFT"))
    _, limited_full = MODULE._selected_stocks(stocks, _args(max_tickers=2))
    _, offset_full = MODULE._selected_stocks(stocks, _args(offset=1))
    assert targeted_full is False
    assert limited_full is False
    assert offset_full is False


def test_manual_scope_is_stable_and_sanitized_by_publisher():
    scope = MODULE._artifact_scope(_args(tickers="msft, nvda", max_tickers=2))
    assert scope == "manual-limit-2-tickers-MSFT-NVDA"


def test_sector_benchmark_map_covers_every_supported_sector():
    assert MODULE.SECTOR_ETFS["Technology"] == "XLK"
    assert MODULE.SECTOR_ETFS["Telecommunications"] == "XLC"
    assert len(MODULE.SECTOR_ETFS) == 12


def _price_rows(ticker, start_date, _end_date):
    return [
        {
            "ticker": ticker,
            "trading_date": start_date + timedelta(days=index),
            "adjusted_close_price": Decimal(100 + index),
            "volume": 1000,
        }
        for index in range(100)
    ]


@patch("src.services.earnings_reaction_artifacts.publish_earnings_reaction_artifacts")
@patch("src.db.connection.DatabasePool.close")
@patch("src.db.connection.DatabasePool.initialize")
def test_targeted_run_publishes_only_scoped_artifacts(
    mock_initialize, mock_close, mock_publish, monkeypatch
):
    monkeypatch.setenv("STOCKARA_ARTIFACT_BUCKET", "bucket")
    with (
        patch.object(
            store,
            "active_stock_metadata",
            return_value=[{"ticker": "AAPL", "sector": "Technology"}],
        ),
        patch.object(
            store,
            "earnings_events",
            return_value=[
                {
                    "ticker": "AAPL",
                    "event_date": "2026-01-20",
                    "time_of_day": "before_market",
                }
            ],
        ),
        patch.object(store, "get_stock_data", side_effect=_price_rows),
    ):
        result = MODULE.build_event_studies(
            _args(tickers="AAPL", dry_run=False, publish_artifact=True)
        )

    assert result["event_count"] == 1
    assert result["full_universe"] is False
    assert result["published"] is True
    assert mock_publish.call_args.kwargs["artifact_scope"] == "manual-tickers-AAPL"
    assert mock_publish.call_args.kwargs["publish_latest"] is False
    mock_initialize.assert_called_once()
    mock_close.assert_called_once()


@patch("src.services.earnings_reaction_artifacts.publish_earnings_reaction_artifacts")
@patch("src.db.connection.DatabasePool.close")
@patch("src.db.connection.DatabasePool.initialize")
def test_dry_run_never_publishes(mock_initialize, mock_close, mock_publish):
    with (
        patch.object(
            store,
            "active_stock_metadata",
            return_value=[{"ticker": "AAPL", "sector": "Technology"}],
        ),
        patch.object(store, "earnings_events", return_value=[]),
        patch.object(store, "get_stock_data", return_value=[]),
    ):
        result = MODULE.build_event_studies(_args(publish_artifact=True))

    assert result["published"] is False
    mock_publish.assert_not_called()
    mock_initialize.assert_called_once()
    mock_close.assert_called_once()


@patch("src.db.connection.DatabasePool.close")
@patch("src.db.connection.DatabasePool.initialize")
def test_publish_requires_artifact_bucket(mock_initialize, mock_close, monkeypatch):
    monkeypatch.delenv("STOCKARA_ARTIFACT_BUCKET", raising=False)
    with (
        patch.object(
            store,
            "active_stock_metadata",
            return_value=[{"ticker": "AAPL", "sector": "Technology"}],
        ),
        patch.object(store, "earnings_events", return_value=[]),
        patch.object(store, "get_stock_data", return_value=[]),
    ):
        with pytest.raises(ValueError, match="STOCKARA_ARTIFACT_BUCKET"):
            MODULE.build_event_studies(
                _args(tickers="AAPL", dry_run=False, publish_artifact=True)
            )

    mock_initialize.assert_called_once()
    mock_close.assert_called_once()
