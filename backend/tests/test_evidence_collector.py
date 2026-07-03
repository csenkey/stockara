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
        "items": ["", "2.02,9.01", ""],
        "primaryDocument": ["", "nvda-20260617.htm", ""],
        "primaryDocDescription": ["", "Results of Operations and Financial Condition", ""],
    }

    filing = evidence_collector._latest_material_filing(recent)

    assert filing == {
        "form": "8-K",
        "filing_date": date.today() - timedelta(days=2),
        "accession_number": "0001234567-26-000001",
        "items": "2.02,9.01",
        "primary_document": "nvda-20260617.htm",
        "primary_doc_description": "Results of Operations and Financial Condition",
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
        patch.object(evidence_collector, "_finnhub_earnings_content_signals", return_value=[]),
        patch.object(evidence_collector, "_sector_context_by_sector", return_value={}),
        patch.object(evidence_collector, "_macro_context", return_value=None),
    ):
        result = evidence_collector.collect_evidence(tickers=["NVDA", "MSFT"])

    assert result["status"] == "success"
    assert result["tickers_requested"] == 2
    assert result["tickers_processed"] == 2
    assert result["sec_signals_written"] == 1
    assert result["analyst_signals_written"] == 1
    assert result["analyst_rating_signals_written"] == 0
    assert result["price_target_signals_written"] == 0
    assert result["earnings_release_signals_written"] == 0
    assert result["earnings_transcript_signals_written"] == 0
    assert result["sector_context_signals_written"] == 0
    assert result["macro_context_signals_written"] == 0
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
                "items": ["2.02,9.01"],
                "primaryDocument": ["nvda-20260617.htm"],
                "primaryDocDescription": ["Results of Operations and Financial Condition"],
            }
        }
    }

    with patch.object(evidence_collector.requests, "get", return_value=company_response):
        signal = evidence_collector._sec_filing_signal("NVDA", {"NVDA": "0001234567"})

    assert signal is not None
    assert signal["signal_type"] == "sec_filing"
    assert signal["direction"] == "positive"
    assert "items 2.02,9.01" in signal["summary"]
    assert "Results of Operations and Financial Condition" in signal["summary"]
    assert signal["source"]["provider"] == "sec"
    assert signal["source"]["raw"]["form"] == "8-K"
    assert signal["source"]["raw"]["items"] == "2.02,9.01"
    assert signal["source"]["raw"]["primary_document"] == "nvda-20260617.htm"
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


def test_earnings_release_signal_links_nearby_earnings_event():
    published = date.today()
    article = {
        "headline": "NVIDIA reports earnings and beats estimates",
        "summary": "Revenue growth was above expectations.",
        "source": "Business Wire",
        "datetime": int(
            evidence_collector.datetime(
                published.year,
                published.month,
                published.day,
                tzinfo=evidence_collector.timezone.utc,
            ).timestamp()
        ),
        "url": "https://example.com/nvda-earnings",
    }
    earnings_event = {
        "ticker": "NVDA",
        "event_date": published.isoformat(),
        "eps_estimate": "1.00",
        "reported_eps": "1.20",
        "provider": "finnhub",
    }

    signal = evidence_collector._earnings_content_signal_from_articles(
        "NVDA",
        [article],
        [earnings_event],
        "earnings_release",
        evidence_collector.EARNINGS_RELEASE_KEYWORDS,
    )

    assert signal is not None
    assert signal["signal_type"] == "earnings_release"
    assert signal["direction"] == "positive"
    assert signal["score"] > 20
    assert signal["source"]["provider"] == "finnhub"
    assert signal["source"]["raw"]["linked_earnings_event"]["event_date"] == published.isoformat()


def test_earnings_transcript_signal_matches_transcript_article():
    article = {
        "headline": "Microsoft earnings call transcript",
        "summary": "Management discussed quarterly results.",
        "source": "Seeking Alpha",
        "published_at": date.today().isoformat(),
        "url": "https://example.com/msft-transcript",
    }

    signal = evidence_collector._earnings_content_signal_from_articles(
        "MSFT",
        [article],
        [],
        "earnings_transcript",
        evidence_collector.EARNINGS_TRANSCRIPT_KEYWORDS,
    )

    assert signal is not None
    assert signal["signal_type"] == "earnings_transcript"
    assert signal["direction"] == "neutral"
    assert signal["score"] == 16
    assert signal["title"] == "Earnings call transcript available"


def test_collect_evidence_counts_earnings_release_and_transcript_signals():
    stocks = [{"ticker": "NVDA"}]
    release_signal = {
        "ticker": "NVDA",
        "signal_date": date.today(),
        "signal_type": "earnings_release",
        "direction": "positive",
        "score": 25,
        "title": "Earnings release available",
        "summary": "NVDA reported earnings.",
        "source": {"provider": "finnhub"},
    }
    transcript_signal = {
        "ticker": "NVDA",
        "signal_date": date.today(),
        "signal_type": "earnings_transcript",
        "direction": "neutral",
        "score": 16,
        "title": "Earnings call transcript available",
        "summary": "NVDA transcript published.",
        "source": {"provider": "finnhub"},
    }

    with (
        patch.object(evidence_collector.store, "active_stock_metadata", return_value=stocks),
        patch.object(evidence_collector.store, "put_market_signal") as put_market_signal,
        patch.object(evidence_collector, "_load_sec_ticker_map", return_value={}),
        patch.object(evidence_collector, "_sec_filing_signal", return_value=None),
        patch.object(evidence_collector, "_analyst_action_signal", return_value=None),
        patch.object(evidence_collector, "_finnhub_rating_signal", return_value=None),
        patch.object(evidence_collector, "_finnhub_price_target_signal", return_value=None),
        patch.object(
            evidence_collector,
            "_finnhub_earnings_content_signals",
            return_value=[release_signal, transcript_signal],
        ),
        patch.object(evidence_collector, "_sector_context_by_sector", return_value={}),
        patch.object(evidence_collector, "_macro_context", return_value=None),
    ):
        result = evidence_collector.collect_evidence(tickers=["NVDA"])

    assert result["earnings_release_signals_written"] == 1
    assert result["earnings_transcript_signals_written"] == 1
    assert put_market_signal.call_count == 2


def test_sector_context_signal_scores_sector_etf_move_as_context():
    signal = evidence_collector._sector_context_signal(
        {"ticker": "NVDA", "sector": "Technology"},
        {
            "Technology": {
                "sector": "Technology",
                "sector_etf": "XLK",
                "move_percent": 4.2,
                "lookback_days": 7,
            }
        },
    )

    assert signal is not None
    assert signal["signal_type"] == "sector_context"
    assert signal["direction"] == "positive"
    assert signal["score"] == 10
    assert signal["source"]["provider"] == "yfinance"
    assert signal["source"]["raw"]["context_only"] is True
    assert signal["source"]["raw"]["sector_etf"] == "XLK"


def test_macro_context_signal_uses_yfinance_proxies_as_small_modifier():
    with patch.object(
        evidence_collector,
        "_yfinance_move_percent",
        side_effect=lambda symbol, _: {
            "SPY": 2.0,
            "QQQ": 3.0,
            "IWM": 1.0,
            "TLT": -1.0,
            "^TNX": 0.8,
            "UUP": 0.5,
            "GLD": 0.0,
            "TIP": 0.2,
            "IEF": -0.1,
        }[symbol],
    ):
        context = evidence_collector._macro_context()

    assert context is not None
    assert context["score"] == 0
    assert context["direction"] == "neutral"
    assert context["moves"]["broad_equity"] == 2.0

    signal = evidence_collector._macro_context_signal("NVDA", context)

    assert signal is not None
    assert signal["signal_type"] == "macro_context"
    assert signal["source"]["raw"]["context_only"] is True
    assert signal["source"]["raw"]["moves"]["ten_year_yield"] == 0.8


def test_collect_evidence_writes_sector_and_macro_context_signals():
    stocks = [{"ticker": "NVDA", "sector": "Technology"}]
    sector_signal = {
        "ticker": "NVDA",
        "signal_date": date.today(),
        "signal_type": "sector_context",
        "direction": "positive",
        "score": 10,
        "title": "Sector ETF context",
        "summary": "Technology sector moved higher.",
        "source": {"provider": "yfinance"},
    }
    macro_signal = {
        "ticker": "NVDA",
        "signal_date": date.today(),
        "signal_type": "macro_context",
        "direction": "neutral",
        "score": 0,
        "title": "Macro market context",
        "summary": "Macro context was mixed.",
        "source": {"provider": "yfinance"},
    }

    with (
        patch.object(evidence_collector.store, "active_stock_metadata", return_value=stocks),
        patch.object(evidence_collector.store, "put_market_signal") as put_market_signal,
        patch.object(evidence_collector, "_load_sec_ticker_map", return_value={}),
        patch.object(evidence_collector, "_sector_context_by_sector", return_value={"Technology": {}}),
        patch.object(evidence_collector, "_macro_context", return_value={}),
        patch.object(evidence_collector, "_sector_context_signal", return_value=sector_signal),
        patch.object(evidence_collector, "_macro_context_signal", return_value=macro_signal),
        patch.object(evidence_collector, "_sec_filing_signal", return_value=None),
        patch.object(evidence_collector, "_analyst_action_signal", return_value=None),
        patch.object(evidence_collector, "_finnhub_rating_signal", return_value=None),
        patch.object(evidence_collector, "_finnhub_price_target_signal", return_value=None),
        patch.object(evidence_collector, "_finnhub_earnings_content_signals", return_value=[]),
    ):
        result = evidence_collector.collect_evidence(tickers=["NVDA"])

    assert result["sector_context_signals_written"] == 1
    assert result["macro_context_signals_written"] == 1
    put_market_signal.assert_any_call(sector_signal)
    put_market_signal.assert_any_call(macro_signal)
