"""Tests for high-signal evidence collection."""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from src.collectors import evidence_collector


def test_latest_material_filing_selects_recent_sec_8k():
    recent = {
        "form": ["4", "8-K", "10-Q"],
        "filingDate": [
            date.today().isoformat(),
            (date.today() - timedelta(days=2)).isoformat(),
            (date.today() - timedelta(days=80)).isoformat(),
        ],
        "accessionNumber": ["ignored", "0001234567-26-000001", "old"],
    }

    filing = evidence_collector._latest_material_filing(recent)

    assert filing == {
        "form": "8-K",
        "filing_date": date.today() - timedelta(days=2),
        "accession_number": "0001234567-26-000001",
    }


def test_recommendation_signal_from_counts_creates_bullish_analyst_action():
    signal = evidence_collector._recommendation_signal_from_counts(
        "NVDA",
        {
            "period": date.today().isoformat(),
            "strongBuy": 3,
            "buy": 7,
            "hold": 2,
            "sell": 0,
            "strongSell": 0,
        },
        "finnhub",
    )

    assert signal is not None
    assert signal["signal_type"] == "analyst_action"
    assert signal["direction"] == "positive"
    assert signal["score"] == 35
    assert signal["signal_date"] == date.today()
    assert signal["source"]["provider"] == "finnhub"
    assert signal["source"]["raw"]["period"] == date.today().isoformat()
    assert signal["source"]["raw"]["coverage"] == 12


def test_recommendation_signal_ignores_stale_periods():
    signal = evidence_collector._recommendation_signal_from_counts(
        "NVDA",
        {
            "period": (date.today() - timedelta(days=120)).isoformat(),
            "strongBuy": 3,
            "buy": 7,
            "hold": 2,
        },
        "finnhub",
    )

    assert signal is None


def test_collect_evidence_writes_sec_and_analyst_signals():
    stocks = [{"ticker": "NVDA"}, {"ticker": "MSFT"}]
    sec_signal = {
        "ticker": "NVDA",
        "signal_date": date.today(),
        "signal_type": "sec_filing",
        "direction": "positive",
        "score": 22,
        "title": "Recent SEC 8-K filing",
        "summary": "NVDA filed a material 8-K.",
        "source": {"provider": "sec"},
    }
    analyst_signal = {
        "ticker": "NVDA",
        "signal_date": date.today(),
        "signal_type": "analyst_action",
        "direction": "positive",
        "score": 30,
        "title": "Bullish analyst consensus",
        "summary": "NVDA analyst mix is constructive.",
        "source": {"provider": "finnhub"},
    }

    with (
        patch.object(evidence_collector.store, "active_stock_metadata", return_value=stocks),
        patch.object(evidence_collector.store, "put_market_signal") as put_market_signal,
        patch.object(evidence_collector, "_load_sec_ticker_map", return_value={"NVDA": "1"}),
        patch.object(
            evidence_collector,
            "_sec_filing_signal",
            side_effect=lambda ticker, _: sec_signal if ticker == "NVDA" else None,
        ),
        patch.object(
            evidence_collector,
            "_analyst_action_signal",
            side_effect=lambda ticker: analyst_signal if ticker == "NVDA" else None,
        ),
        patch.object(evidence_collector, "_finnhub_rating_signal", return_value=None),
        patch.object(evidence_collector, "_finnhub_price_target_signal", return_value=None),
    ):
        result = evidence_collector.collect_evidence(tickers=["NVDA", "MSFT"])

    assert result["status"] == "success"
    assert result["tickers_requested"] == 2
    assert result["tickers_processed"] == 2
    assert result["sec_signals_written"] == 1
    assert result["analyst_signals_written"] == 1
    assert result["analyst_rating_signals_written"] == 0
    assert result["price_target_signals_written"] == 0
    assert put_market_signal.call_count == 2
    put_market_signal.assert_any_call(sec_signal)
    put_market_signal.assert_any_call(analyst_signal)


def test_sec_filing_signal_maps_submission_to_market_signal():
    company_response = MagicMock()
    company_response.raise_for_status.return_value = None
    company_response.json.return_value = {
        "filings": {
            "recent": {
                "form": ["8-K"],
                "filingDate": [date.today().isoformat()],
                "accessionNumber": ["0001234567-26-000001"],
            }
        }
    }

    with patch.object(evidence_collector.requests, "get", return_value=company_response):
        signal = evidence_collector._sec_filing_signal("NVDA", {"NVDA": "0001234567"})

    assert signal is not None
    assert signal["signal_type"] == "sec_filing"
    assert signal["direction"] == "positive"
    assert signal["source"]["provider"] == "sec"
    assert signal["source"]["raw"]["form"] == "8-K"
    assert "000123456726000001" in signal["source"]["raw"]["source_url"]


def test_rating_signal_from_upgrade_row_creates_positive_catalyst():
    signal = evidence_collector._rating_signal_from_row(
        "NVDA",
        {
            "gradeTime": date.today().isoformat(),
            "company": "Example Securities",
            "fromGrade": "Neutral",
            "toGrade": "Buy",
            "action": "upgrade",
        },
        "finnhub",
    )

    assert signal is not None
    assert signal["signal_type"] == "analyst_rating"
    assert signal["direction"] == "positive"
    assert signal["score"] == 28
    assert signal["title"] == "Analyst rating upgraded"
    assert signal["source"]["provider"] == "finnhub"
    assert signal["source"]["raw"]["firm"] == "Example Securities"
    assert signal["source"]["raw"]["to_grade"] == "Buy"


def test_rating_signal_from_downgrade_row_creates_negative_catalyst():
    signal = evidence_collector._rating_signal_from_row(
        "TSLA",
        {
            "gradeTime": date.today().isoformat(),
            "company": "Example Securities",
            "fromGrade": "Buy",
            "toGrade": "Sell",
            "action": "downgrade",
        },
        "finnhub",
    )

    assert signal is not None
    assert signal["signal_type"] == "analyst_rating"
    assert signal["direction"] == "negative"
    assert signal["score"] == -28
    assert signal["title"] == "Analyst rating downgraded"


def test_price_target_signal_scores_target_upside():
    signal = evidence_collector._price_target_signal_from_row(
        "NVDA",
        {
            "updatedDate": date.today().isoformat(),
            "targetMean": 125,
            "targetMedian": 120,
            "targetHigh": 150,
            "targetLow": 90,
            "lastClose": 100,
        },
        "finnhub",
    )

    assert signal is not None
    assert signal["signal_type"] == "price_target"
    assert signal["direction"] == "positive"
    assert signal["score"] == 25
    assert signal["source"]["provider"] == "finnhub"
    assert signal["source"]["raw"]["target_mean"] == 125
    assert signal["source"]["raw"]["upside_percent"] == 25.0


def test_price_target_signal_ignores_stale_updates():
    signal = evidence_collector._price_target_signal_from_row(
        "NVDA",
        {
            "updatedDate": (date.today() - timedelta(days=120)).isoformat(),
            "targetMean": 125,
            "lastClose": 100,
        },
        "finnhub",
    )

    assert signal is None
