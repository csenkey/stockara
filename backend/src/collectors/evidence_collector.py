"""Collect high-signal evidence feeds for Phase 1 scoring.

This collector turns SEC filing events and analyst recommendation actions into
stored market signals. The Phase 1 analyzer then consumes these alongside price
and volume signals without doing slow provider lookups during scoring.
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
        "failed_tickers": [],
    }

    for stock in stocks:
        ticker = stock["ticker"]
        try:
            sec_signal = _sec_filing_signal(ticker, sec_ticker_map)
            if sec_signal:
                store.put_market_signal(sec_signal)
                result["sec_signals_written"] += 1

            analyst_signal = _analyst_action_signal(ticker)
            if analyst_signal:
                store.put_market_signal(analyst_signal)
                result["analyst_signals_written"] += 1

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


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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
