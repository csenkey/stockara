"""Manually backfill earnings calendar history into DynamoDB.

Usage:
    STOCKARA_TABLE_NAME=stockara-prod \
    AWS_REGION=eu-central-1 \
    python -m scripts.backfill_earnings_calendar_history --max-tickers 100 --sleep 1.5

This is an operator script for one-time or occasional backfills. It intentionally
does not create infrastructure or schedules.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_ROOT = os.path.join(PROJECT_ROOT, "backend")
sys.path.insert(0, BACKEND_ROOT)
DEFAULT_YFINANCE_LIMIT = 32


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill earnings calendar history from yfinance into DynamoDB."
    )
    parser.add_argument(
        "--tickers",
        help="Comma-separated tickers to backfill. Defaults to all active stocks.",
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=0,
        help="Maximum number of selected tickers to process. 0 means no cap.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Start offset after ticker sorting, useful for chunked manual runs.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_YFINANCE_LIMIT,
        help="Rows requested from yfinance per ticker.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=1825,
        help="Historical calendar window to keep.",
    )
    parser.add_argument(
        "--lookahead-days",
        type=int,
        default=120,
        help="Upcoming calendar window to keep.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Seconds to wait between ticker requests.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and summarize events without writing DynamoDB rows.",
    )
    parser.add_argument(
        "--publish-artifact",
        action="store_true",
        help=(
            "Publish an S3 audit artifact. Partial runs use a scoped collection-date "
            "key; uncapped all-active runs also refresh latest.json."
        ),
    )
    return parser.parse_args()


def _selected_stocks(args: argparse.Namespace) -> list[dict[str, Any]]:
    from src.db.connection import store

    requested = {
        ticker.strip().upper()
        for ticker in (args.tickers or "").split(",")
        if ticker.strip()
    }
    stocks = sorted(store.active_stock_metadata(), key=lambda stock: stock["ticker"])
    if requested:
        stocks = [stock for stock in stocks if stock["ticker"] in requested]
    offset = max(args.offset, 0)
    stocks = stocks[offset:]
    if args.max_tickers > 0:
        stocks = stocks[: args.max_tickers]
    return stocks


def _manual_artifact_scope(args: argparse.Namespace) -> str | None:
    if not args.tickers and args.max_tickers <= 0 and args.offset <= 0:
        return None
    parts = ["manual"]
    if args.offset > 0:
        parts.append(f"offset-{args.offset}")
    if args.max_tickers > 0:
        parts.append(f"limit-{args.max_tickers}")
    if args.tickers:
        tickers = [
            ticker.strip().upper()
            for ticker in args.tickers.split(",")
            if ticker.strip()
        ]
        parts.append(f"tickers-{'-'.join(tickers[:5])}")
        if len(tickers) > 5:
            parts.append(f"plus-{len(tickers) - 5}")
    return "-".join(parts)


def backfill_earnings_calendar_history(args: argparse.Namespace) -> dict[str, Any]:
    from src.collectors.earnings_collector import (
        enrich_price_reaction,
        fetch_earnings_events,
    )
    from src.db.connection import DatabasePool, store
    from src.services.calendar_artifacts import publish_calendar_artifacts

    DatabasePool.initialize()
    try:
        collection_date = date.today()
        range_start = collection_date - timedelta(days=max(args.lookback_days, 0))
        range_end = collection_date + timedelta(days=max(args.lookahead_days, 0))
        stocks = _selected_stocks(args)
        collected_events: list[dict[str, Any]] = []
        failed_tickers: list[str] = []

        for index, stock in enumerate(stocks, start=1):
            ticker = stock["ticker"]
            try:
                events = fetch_earnings_events(
                    ticker,
                    company_name=stock.get("company_name"),
                    limit=args.limit,
                    start_date=range_start,
                    end_date=range_end,
                )
                enriched = [enrich_price_reaction(event) for event in events]
                collected_events.extend(enriched)
                if not args.dry_run:
                    for event in enriched:
                        store.put_earnings_event(event)
                print(
                    f"[{index}/{len(stocks)}] {ticker}: {len(enriched)} event(s)"
                    + (" fetched" if args.dry_run else " stored")
                )
            except Exception as exc:  # noqa: BLE001 - operator script should continue.
                failed_tickers.append(ticker)
                print(f"[{index}/{len(stocks)}] {ticker}: failed: {exc}")
            if args.sleep > 0 and index < len(stocks):
                time.sleep(args.sleep)

        if args.publish_artifact and not args.dry_run:
            bucket = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "")
            artifact_scope = _manual_artifact_scope(args)
            publish_calendar_artifacts(
                bucket=bucket,
                event_type="earnings",
                events=collected_events,
                collection_date=collection_date,
                range_start=range_start,
                range_end=range_end,
                selected_tickers=[stock["ticker"] for stock in stocks],
                collection_status="partial" if failed_tickers else "success",
                warnings=(
                    [f"{len(failed_tickers)} ticker(s) failed during manual backfill."]
                    if failed_tickers
                    else []
                ),
                zero_event_tickers=[
                    stock["ticker"]
                    for stock in stocks
                    if stock["ticker"]
                    not in {event["ticker"] for event in collected_events}
                ],
                artifact_scope=artifact_scope,
                publish_latest=artifact_scope is None,
            )

        summary = {
            "selected_ticker_count": len(stocks),
            "events_fetched": len(collected_events),
            "failed_ticker_count": len(failed_tickers),
            "failed_tickers": failed_tickers,
            "dry_run": args.dry_run,
        }
        print(
            "Done: "
            f"tickers={summary['selected_ticker_count']}, "
            f"events={summary['events_fetched']}, "
            f"failed={summary['failed_ticker_count']}, "
            f"dry_run={summary['dry_run']}"
        )
        return summary
    finally:
        DatabasePool.close()


if __name__ == "__main__":
    backfill_earnings_calendar_history(_parse_args())
