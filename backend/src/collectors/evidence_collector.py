"""Collect high-signal evidence feeds for Phase 1 scoring.

This collector turns SEC filing events, analyst recommendation actions, rating
changes, price-target updates, earnings releases, transcripts, sector moves, and
macro context into stored market signals. The Phase 1 analyzer then consumes
these alongside price and volume signals without doing slow provider lookups
during scoring.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import os
from typing import Any

import requests
import structlog
import yfinance as yf

from src.db.connection import DatabasePool, store
from src.services.secrets import get_provider_api_key

logger = structlog.get_logger(__name__)

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_USER_AGENT = os.environ.get(
    "STOCKARA_SEC_USER_AGENT",
    "Stockara evidence collector contact@example.com",
)
SEC_MATERIAL_FORMS = {"8-K", "10-K", "10-Q", "S-1", "S-3", "S-4", "SC 13D", "SC 13G"}
SEC_FILING_LOOKBACK_DAYS = int(os.environ.get("EVIDENCE_SEC_FILING_LOOKBACK_DAYS", "45"))
ANALYST_LOOKBACK_DAYS = int(os.environ.get("EVIDENCE_ANALYST_LOOKBACK_DAYS", "45"))
EARNINGS_EVIDENCE_LOOKBACK_DAYS = int(
    os.environ.get("EVIDENCE_EARNINGS_LOOKBACK_DAYS", "30")
)
EARNINGS_EVENT_LINK_WINDOW_DAYS = int(
    os.environ.get("EVIDENCE_EARNINGS_EVENT_LINK_WINDOW_DAYS", "7")
)
SECTOR_CONTEXT_LOOKBACK_DAYS = int(os.environ.get("EVIDENCE_SECTOR_LOOKBACK_DAYS", "7"))
MACRO_CONTEXT_LOOKBACK_DAYS = int(os.environ.get("EVIDENCE_MACRO_LOOKBACK_DAYS", "7"))

SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Finance": "XLF",
    "Energy": "XLE",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
    "Telecommunications": "XLC",
}

MACRO_PROXIES = {
    "broad_equity": "SPY",
    "growth_equity": "QQQ",
    "small_cap": "IWM",
    "long_duration_bonds": "TLT",
    "ten_year_yield": "^TNX",
    "us_dollar": "UUP",
    "gold": "GLD",
    "inflation_protected_bonds": "TIP",
    "intermediate_treasuries": "IEF",
}

EARNINGS_RELEASE_KEYWORDS = (
    "reports earnings",
    "reported earnings",
    "earnings release",
    "quarterly results",
    "financial results",
    "fiscal quarter",
    "q1 results",
    "q2 results",
    "q3 results",
    "q4 results",
)
EARNINGS_TRANSCRIPT_KEYWORDS = (
    "earnings call transcript",
    "earnings transcript",
    "conference call transcript",
    "results call transcript",
)
POSITIVE_EARNINGS_KEYWORDS = (
    "beats",
    "beat estimates",
    "raises guidance",
    "record revenue",
    "above expectations",
    "growth",
)
NEGATIVE_EARNINGS_KEYWORDS = (
    "misses",
    "missed estimates",
    "cuts guidance",
    "below expectations",
    "decline",
    "loss widens",
)


def collect_evidence(
    tickers: list[str] | None = None,
    max_tickers: int | None = None,
) -> dict[str, Any]:
    """Collect SEC filing and analyst-action signals for active tickers."""
    stocks = _target_stocks(tickers, max_tickers)
    sec_ticker_map = _load_sec_ticker_map()

    result = {
        "status": "success",
        "tickers_requested": len(stocks),
        "tickers_processed": 0,
        "sec_signals_written": 0,
        "analyst_signals_written": 0,
        "analyst_rating_signals_written": 0,
        "price_target_signals_written": 0,
        "earnings_release_signals_written": 0,
        "earnings_transcript_signals_written": 0,
        "sector_context_signals_written": 0,
        "macro_context_signals_written": 0,
        "failed_tickers": [],
    }
    sector_context = _sector_context_by_sector(stocks)
    macro_context = _macro_context()

    for stock in stocks:
        ticker = stock["ticker"]
        try:
            sector_signal = _sector_context_signal(stock, sector_context)
            if sector_signal:
                store.put_market_signal(sector_signal)
                result["sector_context_signals_written"] += 1

            macro_signal = _macro_context_signal(ticker, macro_context)
            if macro_signal:
                store.put_market_signal(macro_signal)
                result["macro_context_signals_written"] += 1

            sec_signal = _sec_filing_signal(ticker, sec_ticker_map)
            if sec_signal:
                store.put_market_signal(sec_signal)
                result["sec_signals_written"] += 1

            analyst_signal = _analyst_action_signal(ticker)
            if analyst_signal:
                store.put_market_signal(analyst_signal)
                result["analyst_signals_written"] += 1

            analyst_rating_signal = _finnhub_rating_signal(ticker)
            if analyst_rating_signal:
                store.put_market_signal(analyst_rating_signal)
                result["analyst_rating_signals_written"] += 1

            price_target_signal = _finnhub_price_target_signal(ticker)
            if price_target_signal:
                store.put_market_signal(price_target_signal)
                result["price_target_signals_written"] += 1

            for earnings_signal in _finnhub_earnings_content_signals(ticker):
                store.put_market_signal(earnings_signal)
                if earnings_signal["signal_type"] == "earnings_release":
                    result["earnings_release_signals_written"] += 1
                elif earnings_signal["signal_type"] == "earnings_transcript":
                    result["earnings_transcript_signals_written"] += 1

            result["tickers_processed"] += 1
        except Exception as exc:
            logger.warning("evidence_collection_ticker_failed", ticker=ticker, error=str(exc))
            result["failed_tickers"].append({"ticker": ticker, "reason": str(exc)})

    if result["failed_tickers"]:
        result["status"] = "partial"

    return result


def _target_stocks(
    tickers: list[str] | None,
    max_tickers: int | None,
) -> list[dict[str, Any]]:
    if tickers:
        requested = {ticker.strip().upper() for ticker in tickers if ticker.strip()}
        stocks = [
            stock
            for stock in store.active_stock_metadata()
            if stock.get("ticker", "").upper() in requested
        ]
    else:
        stocks = store.active_stock_metadata()

    stocks = sorted(stocks, key=lambda row: row["ticker"])
    if max_tickers is not None and max_tickers > 0:
        return stocks[:max_tickers]
    return stocks


def _load_sec_ticker_map() -> dict[str, str]:
    try:
        response = requests.get(
            SEC_COMPANY_TICKERS_URL,
            headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("sec_company_tickers_unavailable", error=str(exc))
        return {}

    result: dict[str, str] = {}
    for row in payload.values():
        ticker = str(row.get("ticker", "")).upper()
        cik = str(row.get("cik_str", "")).zfill(10)
        if ticker and cik:
            result[ticker] = cik
    return result


def _sec_filing_signal(ticker: str, sec_ticker_map: dict[str, str]) -> dict[str, Any] | None:
    cik = sec_ticker_map.get(ticker.upper())
    if not cik:
        return None

    try:
        response = requests.get(
            SEC_SUBMISSIONS_URL.format(cik=cik),
            headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.info("sec_submission_unavailable", ticker=ticker, error=str(exc))
        return None

    latest = _latest_material_filing(payload.get("filings", {}).get("recent", {}))
    if not latest:
        return None

    filed_at = latest["filing_date"]
    form = latest["form"]
    score = _sec_form_score(form)
    accession = latest.get("accession_number", "")
    source_url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession.replace('-', '')}/{accession}-index.html"
        if accession
        else f"https://www.sec.gov/edgar/browse/?CIK={int(cik)}"
    )
    return _market_signal(
        ticker,
        date.today(),
        "sec_filing",
        "neutral" if score < 15 else "positive",
        score,
        f"Recent SEC {form} filing",
        f"{ticker} filed a {form} with the SEC on {filed_at.isoformat()}.",
        {
            "provider": "sec",
            "cik": cik,
            "form": form,
            "filing_date": filed_at.isoformat(),
            "accession_number": accession,
            "source_url": source_url,
        },
    )


def _latest_material_filing(recent: dict[str, list[Any]]) -> dict[str, Any] | None:
    forms = recent.get("form") or []
    filing_dates = recent.get("filingDate") or []
    accession_numbers = recent.get("accessionNumber") or []
    cutoff = date.today() - timedelta(days=SEC_FILING_LOOKBACK_DAYS)

    for index, form_value in enumerate(forms):
        form = str(form_value).upper()
        if form not in SEC_MATERIAL_FORMS:
            continue
        filing_date = _parse_date(filing_dates[index] if index < len(filing_dates) else None)
        if filing_date is None or filing_date < cutoff:
            continue
        return {
            "form": form,
            "filing_date": filing_date,
            "accession_number": (
                str(accession_numbers[index]) if index < len(accession_numbers) else ""
            ),
        }
    return None


def _sec_form_score(form: str) -> int:
    if form == "8-K":
        return 22
    if form in {"10-K", "10-Q"}:
        return 12
    if form.startswith("S-"):
        return 16
    if form.startswith("SC 13"):
        return 18
    return 8


def _analyst_action_signal(ticker: str) -> dict[str, Any] | None:
    signal = _finnhub_recommendation_signal(ticker)
    if signal:
        return signal
    return _yfinance_recommendation_signal(ticker)


def _finnhub_recommendation_signal(ticker: str) -> dict[str, Any] | None:
    api_key = get_provider_api_key(
        "finnhub",
        "FINNHUB_KEY",
        "FINNHUB_KEY_SECRET_NAME",
        supported_json_keys=("FINNHUB_KEY", "finnhub_key", "api_key"),
    )
    if not api_key:
        return None
    try:
        response = requests.get(
            "https://finnhub.io/api/v1/stock/recommendation",
            params={"symbol": ticker, "token": api_key},
            timeout=20,
        )
        response.raise_for_status()
        rows = response.json()
    except Exception as exc:
        logger.info("finnhub_recommendation_unavailable", ticker=ticker, error=str(exc))
        return None
    if not rows:
        return None
    return _recommendation_signal_from_counts(ticker, rows[0], "finnhub")


def _finnhub_rating_signal(ticker: str) -> dict[str, Any] | None:
    api_key = _finnhub_api_key()
    if not api_key:
        return None
    cutoff = date.today() - timedelta(days=ANALYST_LOOKBACK_DAYS)
    try:
        response = requests.get(
            "https://finnhub.io/api/v1/stock/upgrade-downgrade",
            params={"symbol": ticker, "from": cutoff.isoformat(), "token": api_key},
            timeout=20,
        )
        response.raise_for_status()
        rows = response.json()
    except Exception as exc:
        logger.info("finnhub_rating_unavailable", ticker=ticker, error=str(exc))
        return None

    if not rows:
        return None
    return _rating_signal_from_row(ticker, rows[0], "finnhub")


def _finnhub_price_target_signal(ticker: str) -> dict[str, Any] | None:
    api_key = _finnhub_api_key()
    if not api_key:
        return None
    try:
        response = requests.get(
            "https://finnhub.io/api/v1/stock/price-target",
            params={"symbol": ticker, "token": api_key},
            timeout=20,
        )
        response.raise_for_status()
        row = response.json()
    except Exception as exc:
        logger.info("finnhub_price_target_unavailable", ticker=ticker, error=str(exc))
        return None

    if not row:
        return None
    return _price_target_signal_from_row(ticker, row, "finnhub")


def _finnhub_earnings_content_signals(ticker: str) -> list[dict[str, Any]]:
    api_key = _finnhub_api_key()
    if not api_key:
        return []
    to_date = date.today()
    from_date = to_date - timedelta(days=EARNINGS_EVIDENCE_LOOKBACK_DAYS)
    try:
        response = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": ticker,
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "token": api_key,
            },
            timeout=20,
        )
        response.raise_for_status()
        rows = response.json()
    except Exception as exc:
        logger.info("finnhub_earnings_content_unavailable", ticker=ticker, error=str(exc))
        return []

    linked_events = _recent_and_upcoming_earnings_events(ticker, to_date)
    release_signal = _earnings_content_signal_from_articles(
        ticker,
        rows,
        linked_events,
        "earnings_release",
        EARNINGS_RELEASE_KEYWORDS,
    )
    transcript_signal = _earnings_content_signal_from_articles(
        ticker,
        rows,
        linked_events,
        "earnings_transcript",
        EARNINGS_TRANSCRIPT_KEYWORDS,
    )
    return [signal for signal in (release_signal, transcript_signal) if signal]


def _yfinance_recommendation_signal(ticker: str) -> dict[str, Any] | None:
    try:
        recs = yf.Ticker(ticker).recommendations
    except Exception as exc:
        logger.info("yfinance_recommendation_unavailable", ticker=ticker, error=str(exc))
        return None
    if recs is None or len(recs) == 0:
        return None

    latest = recs.iloc[-1].to_dict()
    return _recommendation_signal_from_counts(ticker, latest, "yfinance")


def _finnhub_api_key() -> str | None:
    return get_provider_api_key(
        "finnhub",
        "FINNHUB_KEY",
        "FINNHUB_KEY_SECRET_NAME",
        supported_json_keys=("FINNHUB_KEY", "finnhub_key", "api_key"),
    )


def _sector_context_by_sector(stocks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    sectors = {
        str(stock.get("sector") or "").strip()
        for stock in stocks
        if str(stock.get("sector") or "").strip() in SECTOR_ETFS
    }
    context: dict[str, dict[str, Any]] = {}
    for sector in sectors:
        etf = SECTOR_ETFS[sector]
        move = _yfinance_move_percent(etf, SECTOR_CONTEXT_LOOKBACK_DAYS)
        if move is None:
            continue
        context[sector] = {
            "sector": sector,
            "sector_etf": etf,
            "move_percent": move,
            "lookback_days": SECTOR_CONTEXT_LOOKBACK_DAYS,
        }
    return context


def _sector_context_signal(
    stock: dict[str, Any],
    context: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    ticker = str(stock.get("ticker") or "").upper()
    sector = str(stock.get("sector") or "").strip()
    row = context.get(sector)
    if not ticker or not row:
        return None
    move = float(row["move_percent"])
    if abs(move) < 1.5:
        return None
    score = int(max(-14, min(14, move * 2.5)))
    direction = "positive" if score > 0 else "negative"
    return _market_signal(
        ticker,
        date.today(),
        "sector_context",
        direction,
        score,
        "Sector ETF context",
        (
            f"{sector} sector proxy {row['sector_etf']} moved {move:.2f}% over "
            f"the last {row['lookback_days']} calendar days."
        ),
        {
            "provider": "yfinance",
            "sector": sector,
            "sector_etf": row["sector_etf"],
            "lookback_days": row["lookback_days"],
            "move_percent": round(move, 2),
            "context_only": True,
        },
    )


def _macro_context() -> dict[str, Any] | None:
    moves = {
        name: _yfinance_move_percent(symbol, MACRO_CONTEXT_LOOKBACK_DAYS)
        for name, symbol in MACRO_PROXIES.items()
    }
    observed = {name: value for name, value in moves.items() if value is not None}
    if len(observed) < 3:
        return None

    equity_moves = [
        value
        for name, value in observed.items()
        if name in {"broad_equity", "growth_equity", "small_cap"}
    ]
    equity_risk = sum(equity_moves) / len(equity_moves) if equity_moves else 0.0
    yield_move = observed.get("ten_year_yield", 0.0)
    dollar_move = observed.get("us_dollar", 0.0)
    bond_move = observed.get("long_duration_bonds", 0.0)
    inflation_proxy = None
    if (
        observed.get("inflation_protected_bonds") is not None
        and observed.get("intermediate_treasuries") is not None
    ):
        inflation_proxy = (
            observed["inflation_protected_bonds"]
            - observed["intermediate_treasuries"]
        )

    raw_score = equity_risk * 1.1 + bond_move * 0.35 - dollar_move * 0.7 - yield_move * 1.5
    if inflation_proxy is not None:
        raw_score -= max(0.0, inflation_proxy) * 0.6
    score = int(max(-12, min(12, raw_score)))
    direction = "positive" if score > 3 else "negative" if score < -3 else "neutral"
    return {
        "direction": direction,
        "score": score,
        "lookback_days": MACRO_CONTEXT_LOOKBACK_DAYS,
        "moves": {name: round(value, 2) for name, value in observed.items()},
        "equity_risk_move_percent": round(equity_risk, 2),
        "inflation_proxy_percent": (
            round(inflation_proxy, 2) if inflation_proxy is not None else None
        ),
    }


def _macro_context_signal(ticker: str, context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not context:
        return None
    return _market_signal(
        ticker,
        date.today(),
        "macro_context",
        context["direction"],
        context["score"],
        "Macro market context",
        (
            f"Macro proxies over {context['lookback_days']} calendar days: "
            f"equity risk basket {context['equity_risk_move_percent']:.2f}%, "
            f"score {context['score']}."
        ),
        {
            "provider": "yfinance",
            "lookback_days": context["lookback_days"],
            "moves": context["moves"],
            "equity_risk_move_percent": context["equity_risk_move_percent"],
            "inflation_proxy_percent": context["inflation_proxy_percent"],
            "context_only": True,
        },
    )


def _yfinance_move_percent(symbol: str, lookback_days: int) -> float | None:
    try:
        frame = yf.download(
            symbol,
            period=f"{max(lookback_days + 5, 10)}d",
            interval="1d",
            progress=False,
            timeout=10,
        )
    except Exception as exc:
        logger.info("yfinance_context_unavailable", symbol=symbol, error=str(exc))
        return None
    closes = _close_values(frame)
    if len(closes) < 2:
        return None
    start_index = max(0, len(closes) - max(lookback_days, 2) - 1)
    start = closes[start_index]
    end = closes[-1]
    if start <= 0:
        return None
    return (end - start) / start * 100


def _close_values(frame: Any) -> list[float]:
    if frame is None or getattr(frame, "empty", True):
        return []
    try:
        close = frame["Close"]
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        values = close.dropna().tolist()
    except Exception:
        return []
    return [float(value) for value in values if _float(value) is not None]


def _recent_and_upcoming_earnings_events(
    ticker: str,
    run_date: date,
) -> list[dict[str, Any]]:
    try:
        return store.earnings_events_for_ticker(
            ticker,
            run_date - timedelta(days=EARNINGS_EVIDENCE_LOOKBACK_DAYS),
            run_date + timedelta(days=EARNINGS_EVENT_LINK_WINDOW_DAYS),
        )
    except Exception as exc:
        logger.info("earnings_event_link_lookup_failed", ticker=ticker, error=str(exc))
        return []


def _recommendation_signal_from_counts(
    ticker: str,
    row: dict[str, Any],
    provider: str,
) -> dict[str, Any] | None:
    period = _parse_date(row.get("period")) or date.today()
    if period < date.today() - timedelta(days=ANALYST_LOOKBACK_DAYS):
        return None
    strong_buy = _int(row.get("strongBuy") or row.get("strong_buy"))
    buy = _int(row.get("buy"))
    hold = _int(row.get("hold"))
    sell = _int(row.get("sell"))
    strong_sell = _int(row.get("strongSell") or row.get("strong_sell"))
    coverage = strong_buy + buy + hold + sell + strong_sell
    if coverage <= 0:
        return None

    bullish = strong_buy * 2 + buy
    bearish = strong_sell * 2 + sell
    net = bullish - bearish
    if abs(net) < 2:
        direction = "neutral"
        score = 10
    else:
        direction = "positive" if net > 0 else "negative"
        score = max(-35, min(35, net * 5))

    title = "Bullish analyst consensus" if direction == "positive" else "Bearish analyst consensus"
    if direction == "neutral":
        title = "Mixed analyst consensus"

    return _market_signal(
        ticker,
        date.today(),
        "analyst_action",
        direction,
        score,
        title,
        (
            f"{ticker} analyst recommendation mix: {strong_buy} strong buy, "
            f"{buy} buy, {hold} hold, {sell} sell, {strong_sell} strong sell."
        ),
        {
            "provider": provider,
            "period": period.isoformat(),
            "strong_buy": strong_buy,
            "buy": buy,
            "hold": hold,
            "sell": sell,
            "strong_sell": strong_sell,
            "coverage": coverage,
        },
    )


def _earnings_content_signal_from_articles(
    ticker: str,
    rows: list[dict[str, Any]],
    earnings_events: list[dict[str, Any]],
    signal_type: str,
    keywords: tuple[str, ...],
) -> dict[str, Any] | None:
    article = _latest_matching_article(rows, keywords)
    if not article:
        return None

    published_at = _article_published_at(article)
    published_date = published_at.date() if published_at else date.today()
    linked_event = _nearest_earnings_event(earnings_events, published_date)
    direction, score = _earnings_content_direction_and_score(article, signal_type)
    title = (
        "Earnings call transcript available"
        if signal_type == "earnings_transcript"
        else "Earnings release available"
    )
    article_title = str(article.get("headline") or article.get("title") or "").strip()
    source = str(article.get("source") or "finnhub").strip()
    summary = str(article.get("summary") or "").strip()
    summary_text = summary or article_title

    return _market_signal(
        ticker,
        date.today(),
        signal_type,
        direction,
        score,
        title,
        f"{source} published {ticker} earnings evidence: {summary_text[:220]}",
        {
            "provider": "finnhub",
            "published_at": published_at.isoformat() if published_at else None,
            "article_title": article_title,
            "source": source,
            "url": article.get("url"),
            "linked_earnings_event": (
                _jsonable_earnings_event(linked_event) if linked_event else None
            ),
        },
    )


def _latest_matching_article(
    rows: list[dict[str, Any]],
    keywords: tuple[str, ...],
) -> dict[str, Any] | None:
    matches = [
        row
        for row in rows
        if any(keyword in _article_text(row) for keyword in keywords)
    ]
    if not matches:
        return None
    return max(matches, key=lambda row: _article_published_at(row) or datetime.min)


def _earnings_content_direction_and_score(
    article: dict[str, Any],
    signal_type: str,
) -> tuple[str, int]:
    text = _article_text(article)
    positive_hits = sum(1 for keyword in POSITIVE_EARNINGS_KEYWORDS if keyword in text)
    negative_hits = sum(1 for keyword in NEGATIVE_EARNINGS_KEYWORDS if keyword in text)
    base_score = 16 if signal_type == "earnings_transcript" else 20
    if positive_hits > negative_hits:
        return "positive", min(35, base_score + positive_hits * 5)
    if negative_hits > positive_hits:
        return "negative", -min(35, base_score + negative_hits * 5)
    return "neutral", base_score


def _nearest_earnings_event(
    events: list[dict[str, Any]],
    target_date: date,
) -> dict[str, Any] | None:
    parsed_events = [
        (event, _parse_date(event.get("event_date")))
        for event in events
    ]
    candidates = [
        (event, event_date)
        for event, event_date in parsed_events
        if event_date is not None
        and abs((event_date - target_date).days) <= EARNINGS_EVENT_LINK_WINDOW_DAYS
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: abs((item[1] - target_date).days))[0]


def _jsonable_earnings_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _jsonable_value(value)
        for key, value in event.items()
        if key
        in {
            "ticker",
            "company_name",
            "event_date",
            "eps_estimate",
            "reported_eps",
            "surprise_percent",
            "time_of_day",
            "is_upcoming",
            "provider",
            "source_url",
        }
    }


def _rating_signal_from_row(
    ticker: str,
    row: dict[str, Any],
    provider: str,
) -> dict[str, Any] | None:
    event_date = _parse_date(row.get("gradeTime") or row.get("grade_time") or row.get("date"))
    if event_date is None or event_date < date.today() - timedelta(days=ANALYST_LOOKBACK_DAYS):
        return None

    action = str(row.get("action") or "").lower()
    from_grade = str(row.get("fromGrade") or row.get("from_grade") or "").strip()
    to_grade = str(row.get("toGrade") or row.get("to_grade") or "").strip()
    firm = str(row.get("company") or row.get("firm") or "analyst").strip()

    score = _rating_score(action, from_grade, to_grade)
    if score == 0:
        direction = "neutral"
        title = "Analyst rating reiterated"
    elif score > 0:
        direction = "positive"
        title = "Analyst rating upgraded"
    else:
        direction = "negative"
        title = "Analyst rating downgraded"

    grade_change = f"{from_grade or 'unknown'} to {to_grade or 'unknown'}"
    return _market_signal(
        ticker,
        date.today(),
        "analyst_rating",
        direction,
        score,
        title,
        f"{firm} changed {ticker}'s analyst rating from {grade_change}.",
        {
            "provider": provider,
            "event_date": event_date.isoformat(),
            "action": action,
            "from_grade": from_grade,
            "to_grade": to_grade,
            "firm": firm,
        },
    )


def _price_target_signal_from_row(
    ticker: str,
    row: dict[str, Any],
    provider: str,
) -> dict[str, Any] | None:
    updated_at = _parse_date(
        row.get("updatedDate") or row.get("updated_date") or row.get("lastUpdated")
    )
    if updated_at is not None and updated_at < date.today() - timedelta(days=ANALYST_LOOKBACK_DAYS):
        return None

    target_mean = _float(row.get("targetMean") or row.get("target_mean"))
    target_median = _float(row.get("targetMedian") or row.get("target_median"))
    target_high = _float(row.get("targetHigh") or row.get("target_high"))
    target_low = _float(row.get("targetLow") or row.get("target_low"))
    last_price = _float(
        row.get("lastClose")
        or row.get("last_close")
        or row.get("close")
        or row.get("currentPrice")
    )
    target = target_mean or target_median
    if target is None or target <= 0:
        return None

    upside_percent = _pct_delta(last_price, target) if last_price else None
    if upside_percent is None:
        direction = "neutral"
        score = 12
    elif upside_percent >= 15:
        direction = "positive"
        score = min(35, int(upside_percent))
    elif upside_percent <= -10:
        direction = "negative"
        score = max(-35, int(upside_percent))
    else:
        direction = "neutral"
        score = max(8, min(18, int(abs(upside_percent))))

    summary_suffix = (
        f", implying {upside_percent:.1f}% upside versus the latest close"
        if upside_percent is not None
        else ""
    )
    return _market_signal(
        ticker,
        date.today(),
        "price_target",
        direction,
        score,
        "Analyst price target update",
        f"{ticker}'s analyst mean price target is {target:.2f}{summary_suffix}.",
        {
            "provider": provider,
            "updated_at": updated_at.isoformat() if updated_at else None,
            "target_mean": target_mean,
            "target_median": target_median,
            "target_high": target_high,
            "target_low": target_low,
            "last_price": last_price,
            "upside_percent": round(upside_percent, 2) if upside_percent is not None else None,
        },
    )


def _rating_score(action: str, from_grade: str, to_grade: str) -> int:
    if "upgrade" in action:
        return 28
    if "downgrade" in action:
        return -28
    if "initiat" in action:
        return _grade_sentiment_score(to_grade, default=16)
    if "reit" in action or "maintain" in action:
        return _grade_sentiment_score(to_grade, default=8)

    from_score = _grade_sentiment_score(from_grade, default=0)
    to_score = _grade_sentiment_score(to_grade, default=0)
    return max(-28, min(28, (to_score - from_score) * 7))


def _grade_sentiment_score(grade: str, default: int) -> int:
    grade_text = grade.lower()
    if any(word in grade_text for word in ("buy", "outperform", "overweight", "positive")):
        return 2
    if any(word in grade_text for word in ("sell", "underperform", "underweight", "negative")):
        return -2
    if any(word in grade_text for word in ("hold", "neutral", "market perform", "equal")):
        return 0
    return default


def _market_signal(
    ticker: str,
    signal_date: date,
    signal_type: str,
    direction: str,
    score: int,
    title: str,
    summary: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "signal_date": signal_date,
        "signal_type": signal_type,
        "direction": direction,
        "score": score,
        "title": title,
        "summary": summary,
        "source": {
            "provider": raw.get("provider", "evidence_collector"),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "raw": raw,
        },
    }


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _article_published_at(article: dict[str, Any]) -> datetime | None:
    value = article.get("datetime") or article.get("published_at") or article.get("publishedAt")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _article_text(article: dict[str, Any]) -> str:
    return " ".join(
        str(article.get(field) or "")
        for field in ("headline", "title", "summary", "content")
    ).lower()


def _jsonable_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_delta(previous: float | None, current: float) -> float | None:
    if previous is None or previous <= 0:
        return None
    return (current - previous) / previous * 100


def _event_tickers(event: dict[str, Any]) -> list[str] | None:
    tickers = event.get("tickers")
    if not tickers:
        tickers_csv = str(event.get("tickers_csv") or "").strip()
        tickers = tickers_csv.split(",") if tickers_csv else []
    cleaned = [str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()]
    return cleaned or None


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    event = event or {}
    logger.info("Evidence collector Lambda invoked", lambda_event=event)
    DatabasePool.initialize()
    try:
        result = collect_evidence(
            tickers=_event_tickers(event),
            max_tickers=int(event.get("max_tickers") or 0) or None,
        )
        return {"statusCode": 200, "body": result}
    except Exception as exc:
        logger.error("Evidence collector Lambda failed", error=str(exc))
        return {"statusCode": 500, "body": {"status": "error", "message": str(exc)}}
    finally:
        DatabasePool.close()
