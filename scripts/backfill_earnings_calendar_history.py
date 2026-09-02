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
import json
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
        "--alpha-vantage-max-calls",
        type=int,
        default=20,
        help="Maximum Alpha Vantage fallback calls for this run.",
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


def _selected_stocks(
    args: argparse.Namespace,
    eligible_stocks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    stocks = list(eligible_stocks) if eligible_stocks is not None else _eligible_stocks(args)
    offset = max(args.offset, 0)
    stocks = stocks[offset:]
    if args.max_tickers > 0:
        stocks = stocks[: args.max_tickers]
    return stocks


def _eligible_stocks(args: argparse.Namespace) -> list[dict[str, Any]]:
    from src.db.connection import store

    requested = {
        ticker.strip().upper()
        for ticker in (args.tickers or "").split(",")
        if ticker.strip()
    }
    stocks = sorted(store.active_stock_metadata(), key=lambda stock: stock["ticker"])
    if requested:
        stocks = [stock for stock in stocks if stock["ticker"] in requested]
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
        INCOMPLETE_COLLECTION_OUTCOMES,
        _collect_per_ticker,
        _reset_alpha_vantage_invocation_state,
        logger,
    )
    from src.db.connection import DatabasePool, store
    from src.services.calendar_artifacts import publish_calendar_artifacts
    from src.services.earnings_history_coverage import (
        build_earnings_history_coverage,
        publish_earnings_history_coverage,
    )
    from src.services.static_artifacts import safe_publish_json_artifact

    DatabasePool.initialize()
    try:
        _reset_alpha_vantage_invocation_state(
            {"alpha_vantage_max_calls": args.alpha_vantage_max_calls}
        )
        collection_date = date.today()
        range_start = collection_date - timedelta(days=max(args.lookback_days, 0))
        range_end = collection_date + timedelta(days=max(args.lookahead_days, 0))
        eligible_stocks = _eligible_stocks(args)
        stocks = _selected_stocks(args, eligible_stocks)
        collected_events: list[dict[str, Any]] = []
        provider_attempts: dict[str, dict[str, Any]] = {}
        collection_outcomes: dict[str, str] = {}

        for index, stock in enumerate(stocks, start=1):
            ticker = stock["ticker"]
            events, _ = _collect_per_ticker(
                [stock],
                {"limit": args.limit},
                logger,
                range_start=range_start,
                range_end=range_end,
                provider_events=[],
                provider_attempts=provider_attempts,
                ticker_collection_outcomes=collection_outcomes,
                include_range_calendar=False,
            )
            collected_events.extend(events)
            if not args.dry_run:
                for event in events:
                    store.put_earnings_event(event)
            outcome = collection_outcomes.get(ticker, "failed")
            print(
                f"[{index}/{len(stocks)}] {ticker}: {len(events)} event(s) "
                f"{'fetched' if args.dry_run else 'stored'}; outcome={outcome}"
            )
            if args.sleep > 0 and index < len(stocks):
                time.sleep(args.sleep)

        incomplete_tickers = [
            stock["ticker"]
            for stock in stocks
            if collection_outcomes.get(stock["ticker"]) != "collected"
        ]
        provider_skipped_tickers = [
            ticker
            for ticker in incomplete_tickers
            if collection_outcomes.get(ticker) in INCOMPLETE_COLLECTION_OUTCOMES
        ]
        first_incomplete_index = next(
            (
                index
                for index, stock in enumerate(stocks)
                if stock["ticker"] in incomplete_tickers
            ),
            len(stocks),
        )
        resume_offset = max(args.offset, 0) + first_incomplete_index
        has_more = resume_offset < len(eligible_stocks)
        history_end = collection_date - timedelta(days=1)
        stored_history: list[dict[str, Any]] = []
        if not args.dry_run and range_start <= history_end:
            for stock in stocks:
                stored_history.extend(
                    store.earnings_events_for_ticker(
                        stock["ticker"], range_start, history_end
                    )
                )
        coverage = build_earnings_history_coverage(
            tickers=[stock["ticker"] for stock in stocks],
            events=(collected_events if args.dry_run else stored_history),
            as_of=collection_date,
            collection_outcomes=collection_outcomes,
        )
        artifact_scope = _manual_artifact_scope(args) or "manual-full-watchlist"
        bucket = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "")
        if args.publish_artifact and not args.dry_run:
            publish_calendar_artifacts(
                bucket=bucket,
                event_type="earnings",
                events=collected_events,
                collection_date=collection_date,
                range_start=range_start,
                range_end=range_end,
                selected_tickers=[stock["ticker"] for stock in stocks],
                collection_status=(
                    "partial" if incomplete_tickers else "success"
                ),
                warnings=(
                    [
                        f"{len(incomplete_tickers)} ticker(s) remained incomplete "
                        "during manual history backfill."
                    ]
                    if incomplete_tickers
                    else []
                ),
                zero_event_tickers=[
                    stock["ticker"]
                    for stock in stocks
                    if stock["ticker"]
                    not in {event["ticker"] for event in collected_events}
                ],
                artifact_scope=artifact_scope,
                publish_latest=False,
            )
            publish_earnings_history_coverage(
                bucket=bucket,
                payload=coverage,
                artifact_scope=artifact_scope,
                publish_latest=False,
            )

        summary = {
            "status": "incomplete" if incomplete_tickers else "success",
            "selected_ticker_count": len(stocks),
            "events_fetched": len(collected_events),
            "successful_ticker_count": len(stocks) - len(incomplete_tickers),
            "incomplete_ticker_count": len(incomplete_tickers),
            "incomplete_tickers": incomplete_tickers,
            "provider_skipped_ticker_count": len(provider_skipped_tickers),
            "provider_skipped_tickers": provider_skipped_tickers,
            "collection_outcomes": collection_outcomes,
            "provider_attempts": provider_attempts,
            "offset": max(args.offset, 0),
            "resume_offset": resume_offset,
            "eligible_ticker_count": len(eligible_stocks),
            "has_more": has_more,
            "coverage_summary": coverage["summary"],
            "dry_run": args.dry_run,
        }
        if not args.dry_run and bucket:
            checkpoint = {
                "schema_version": 1,
                "as_of_date": collection_date.isoformat(),
                "generated_at": coverage["generated_at"],
                **summary,
            }
            safe_publish_json_artifact(
                bucket,
                "earnings/history-backfill/latest.json",
                checkpoint,
            )
            safe_publish_json_artifact(
                bucket,
                (
                    "earnings/history-backfill/"
                    f"as_of_date={collection_date.isoformat()}/"
                    f"offset={max(args.offset, 0)}/checkpoint.json"
                ),
                checkpoint,
            )
        print(
            json.dumps(summary, sort_keys=True, default=str)
        )
        print(
            "Done: "
            f"status={summary['status']}, "
            f"tickers={summary['selected_ticker_count']}, "
            f"events={summary['events_fetched']}, "
            f"incomplete={summary['incomplete_ticker_count']}, "
            f"provider_skipped={summary['provider_skipped_ticker_count']}, "
            f"resume_offset={summary['resume_offset']}, "
            f"dry_run={summary['dry_run']}"
        )
        return summary
    finally:
        DatabasePool.close()


if __name__ == "__main__":
    result = backfill_earnings_calendar_history(_parse_args())
    if result["provider_skipped_ticker_count"]:
        raise SystemExit(2)
