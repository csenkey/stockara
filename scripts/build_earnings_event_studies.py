"""Build historical earnings-reaction artifacts from stored Stockara data.

The command reads DynamoDB only. Scoped/manual runs always publish under a
dated task path and can never replace the full-universe ``latest`` artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, timedelta
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_ROOT = os.path.join(PROJECT_ROOT, "backend")
sys.path.insert(0, BACKEND_ROOT)

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
PRICE_PADDING_BEFORE_DAYS = 45
PRICE_PADDING_AFTER_DAYS = 45
DEFAULT_MATURATION_DAYS = 35


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build earnings event-study artifacts from stored data."
    )
    parser.add_argument("--tickers", help="Optional comma-separated ticker subset.")
    parser.add_argument("--max-tickers", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--lookback-days", type=int, default=1825)
    parser.add_argument(
        "--maturation-days",
        type=int,
        default=DEFAULT_MATURATION_DAYS,
        help="Exclude newer reports so the +20-session window can mature.",
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--publish-artifact", action="store_true")
    return parser.parse_args()


def _requested_tickers(value: str | None) -> set[str]:
    return {ticker.strip().upper() for ticker in (value or "").split(",") if ticker.strip()}


def _selected_stocks(
    stocks: list[dict[str, Any]], args: argparse.Namespace
) -> tuple[list[dict[str, Any]], bool]:
    ordered = sorted(stocks, key=lambda stock: stock["ticker"])
    requested = _requested_tickers(args.tickers)
    eligible = [stock for stock in ordered if not requested or stock["ticker"] in requested]
    offset = max(args.offset, 0)
    selected = eligible[offset:]
    if args.max_tickers > 0:
        selected = selected[: args.max_tickers]
    is_full_universe = not requested and offset == 0 and args.max_tickers <= 0
    return selected, is_full_universe


def _artifact_scope(args: argparse.Namespace) -> str:
    parts = ["manual"]
    if args.offset > 0:
        parts.append(f"offset-{args.offset}")
    if args.max_tickers > 0:
        parts.append(f"limit-{args.max_tickers}")
    requested = sorted(_requested_tickers(args.tickers))
    if requested:
        parts.append(f"tickers-{'-'.join(requested[:5])}")
        if len(requested) > 5:
            parts.append(f"plus-{len(requested) - 5}")
    return "-".join(parts)


def build_event_studies(args: argparse.Namespace) -> dict[str, Any]:
    from src.db.connection import DatabasePool, store
    from src.services.earnings_event_study import build_earnings_event_reaction
    from src.services.earnings_reaction_artifacts import (
        build_earnings_reaction_artifacts,
        publish_earnings_reaction_artifacts,
    )

    if min(args.offset, args.max_tickers, args.lookback_days, args.maturation_days) < 0:
        raise ValueError("numeric inputs must be non-negative")

    DatabasePool.initialize()
    try:
        stocks, is_full_universe = _selected_stocks(store.active_stock_metadata(), args)
        as_of = args.as_of
        event_start = as_of - timedelta(days=args.lookback_days)
        event_end = as_of - timedelta(days=args.maturation_days)
        if event_end < event_start:
            events: list[dict[str, Any]] = []
        else:
            selected = {stock["ticker"] for stock in stocks}
            events = [
                event
                for event in store.earnings_events(event_start, event_end)
                if event["ticker"] in selected
            ]

        if events:
            price_start = min(date.fromisoformat(str(event["event_date"])[:10]) for event in events)
            price_end = max(date.fromisoformat(str(event["event_date"])[:10]) for event in events)
            price_start -= timedelta(days=PRICE_PADDING_BEFORE_DAYS)
            price_end = min(as_of, price_end + timedelta(days=PRICE_PADDING_AFTER_DAYS))
        else:
            price_start = event_start
            price_end = as_of

        stock_by_ticker = {stock["ticker"]: stock for stock in stocks}
        benchmark_tickers = {"SPY"} | {
            benchmark
            for stock in stocks
            if (benchmark := SECTOR_ETFS.get(str(stock.get("sector") or "")))
        }
        price_cache = {
            ticker: store.get_stock_data(ticker, price_start, price_end)
            for ticker in benchmark_tickers
        }
        reaction_rows = []
        for ticker in sorted({event["ticker"] for event in events}):
            price_cache[ticker] = store.get_stock_data(ticker, price_start, price_end)
        trading_sessions = [
            date.fromisoformat(str(row["trading_date"])[:10])
            for row in price_cache.get("SPY", [])
        ]
        for event in events:
            ticker = event["ticker"]
            sector_benchmark = SECTOR_ETFS.get(
                str(stock_by_ticker[ticker].get("sector") or "")
            )
            reaction_rows.append(
                build_earnings_event_reaction(
                    ticker=ticker,
                    report_date=date.fromisoformat(str(event["event_date"])[:10]),
                    time_of_day=event.get("time_of_day"),
                    stock_rows=price_cache.get(ticker, []),
                    broad_market_rows=price_cache.get("SPY", []),
                    sector_rows=price_cache.get(sector_benchmark, []) if sector_benchmark else [],
                    sector_benchmark_ticker=sector_benchmark,
                    trading_sessions=trading_sessions or None,
                )
            )

        artifacts = build_earnings_reaction_artifacts(reaction_rows, as_of=as_of)
        quality_counts = Counter(row.evidence_quality for row in reaction_rows)
        complete_count = quality_counts["high"]
        partial_count = quality_counts["medium"] + quality_counts["low"]
        summary = {
            "status": "success",
            "as_of_date": as_of.isoformat(),
            "selected_ticker_count": len(stocks),
            "event_count": len(reaction_rows),
            "complete_event_count": complete_count,
            "partial_event_count": partial_count,
            "insufficient_event_count": quality_counts["insufficient"],
            "full_universe": is_full_universe,
            "dry_run": args.dry_run,
            "published": bool(args.publish_artifact and not args.dry_run),
        }
        if args.publish_artifact and not args.dry_run:
            bucket = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "").strip()
            if not bucket:
                raise ValueError(
                    "STOCKARA_ARTIFACT_BUCKET is required when publishing artifacts"
                )
            publish_earnings_reaction_artifacts(
                bucket=bucket,
                artifacts=artifacts,
                artifact_scope=None if is_full_universe else _artifact_scope(args),
                publish_latest=is_full_universe,
            )
        print(json.dumps(summary, sort_keys=True))
        return summary
    finally:
        DatabasePool.close()


if __name__ == "__main__":
    build_event_studies(_parse_args())
