"""Earnings calendar collector for Phase 1 signals."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
import pandas as pd
import structlog
import yfinance as yf

from src.db.connection import DatabasePool, store

logger = structlog.get_logger(__name__)

CLOUDWATCH_NAMESPACE = "StockMonitoring"
DEFAULT_LOOKBACK_DAYS = 730
DEFAULT_LOOKAHEAD_DAYS = 120
DEFAULT_LIMIT = 12
MAX_TICKERS_PER_RUN = 50


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    """Collect upcoming and recent past earnings events for active stocks."""
    event = event or {}
    log = logger.bind(lambda_event=event)
    log.info("earnings_collector_started")
    DatabasePool.initialize()
    try:
        stocks = store.active_stock_metadata()
        selected = _select_stocks(stocks, event)
        stored_count = 0
        failed_tickers: list[str] = []

        for stock in selected:
            ticker = stock["ticker"]
            try:
                events = fetch_earnings_events(
                    ticker,
                    company_name=stock.get("company_name"),
                    limit=int(event.get("limit", DEFAULT_LIMIT)),
                )
                for earnings_event in events:
                    enriched = enrich_price_reaction(earnings_event)
                    store.put_earnings_event(enriched)
                    stored_count += 1
            except Exception as exc:
                failed_tickers.append(ticker)
                log.warning("earnings_ticker_collection_failed", ticker=ticker, error=str(exc))

        _emit_metric("earnings_events_collected", stored_count)
        _emit_metric("earnings_collection_failed_tickers", len(failed_tickers))
        log.info(
            "earnings_collector_completed",
            selected_ticker_count=len(selected),
            events_collected=stored_count,
            failed_ticker_count=len(failed_tickers),
        )
        return {
            "statusCode": 200,
            "body": {
                "events_collected": stored_count,
                "selected_ticker_count": len(selected),
                "failed_tickers": failed_tickers,
            },
        }
    finally:
        DatabasePool.close()


def _select_stocks(stocks: list[dict[str, Any]], event: dict[str, Any]) -> list[dict[str, Any]]:
    requested = {str(ticker).upper() for ticker in event.get("tickers", [])}
    if requested:
        stocks = [stock for stock in stocks if stock["ticker"] in requested]
    max_tickers = int(event.get("max_tickers", MAX_TICKERS_PER_RUN))
    return sorted(stocks, key=lambda stock: stock["ticker"])[:max_tickers]


def fetch_earnings_events(
    ticker: str,
    company_name: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Fetch and normalize earnings calendar rows from yfinance."""
    yf_ticker = yf.Ticker(ticker)
    if hasattr(yf_ticker, "get_earnings_dates"):
        data = yf_ticker.get_earnings_dates(limit=limit)
    else:
        data = getattr(yf_ticker, "earnings_dates", None)
    if data is None or getattr(data, "empty", True):
        return []

    today = date.today()
    events: list[dict[str, Any]] = []
    for raw_date, row in data.iterrows():
        event_date = _normalize_event_date(raw_date)
        if event_date is None:
            continue
        events.append(
            {
                "ticker": ticker.upper(),
                "company_name": company_name,
                "event_date": event_date,
                "eps_estimate": _to_decimal(_row_value(row, "EPS Estimate")),
                "reported_eps": _to_decimal(_row_value(row, "Reported EPS")),
                "surprise_percent": _to_decimal(_row_value(row, "Surprise(%)")),
                "time_of_day": _normalize_time_of_day(_row_value(row, "Earnings Date")),
                "is_upcoming": event_date >= today,
                "provider": "yfinance",
                "source_url": f"https://finance.yahoo.com/quote/{ticker.upper()}/analysis",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return events


def enrich_price_reaction(event: dict[str, Any]) -> dict[str, Any]:
    """Attach pre/post earnings price movement for past events when OHLCV exists."""
    if event.get("is_upcoming"):
        return event
    event_date = event["event_date"]
    rows = store.get_stock_data(
        event["ticker"],
        event_date - timedelta(days=7),
        event_date + timedelta(days=7),
    )
    before = _nearest_row_before(rows, event_date)
    after = _nearest_row_after(rows, event_date)
    if not before or not after:
        return event
    price_before = _analysis_close_price(before)
    price_after = _analysis_close_price(after)
    if price_before <= 0:
        return event
    move = (price_after - price_before) / price_before * Decimal("100")
    return {
        **event,
        "price_before": price_before,
        "price_after": price_after,
        "post_earnings_price_move_percent": move.quantize(Decimal("0.01")),
    }


def _nearest_row_before(rows: list[dict[str, Any]], event_date: date) -> dict[str, Any] | None:
    candidates = [
        row for row in rows if (_parse_date(row.get("trading_date")) or date.max) < event_date
    ]
    return max(candidates, key=lambda row: _parse_date(row.get("trading_date")) or date.min, default=None)


def _nearest_row_after(rows: list[dict[str, Any]], event_date: date) -> dict[str, Any] | None:
    candidates = [
        row for row in rows if (_parse_date(row.get("trading_date")) or date.min) > event_date
    ]
    return min(candidates, key=lambda row: _parse_date(row.get("trading_date")) or date.max, default=None)


def _analysis_close_price(row: dict[str, Any]) -> Decimal:
    adjusted = row.get("adjusted_close_price")
    if adjusted is not None:
        return Decimal(str(adjusted))
    return Decimal(str(row["close_price"]))


def _normalize_event_date(value: Any) -> date | None:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalize_time_of_day(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).lower()
    if "before" in text or "bmo" in text:
        return "before_market"
    if "after" in text or "amc" in text:
        return "after_market"
    return None


def _row_value(row: Any, column: str) -> Any:
    try:
        return row.get(column)
    except AttributeError:
        return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    try:
        return Decimal(str(round(float(value), 4)))
    except (ValueError, TypeError, InvalidOperation):
        return None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _emit_metric(metric_name: str, value: float) -> None:
    try:
        boto3.client("cloudwatch").put_metric_data(
            Namespace=CLOUDWATCH_NAMESPACE,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": "Count",
                    "Timestamp": datetime.now(timezone.utc),
                }
            ],
        )
    except Exception as exc:
        logger.warning("earnings_metric_emit_failed", metric=metric_name, error=str(exc))
