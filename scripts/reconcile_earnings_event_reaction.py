"""Reconcile one production earnings reaction against an independent reference."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_ROOT = os.path.join(PROJECT_ROOT, "backend")
sys.path.insert(0, BACKEND_ROOT)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--report-date", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--timing",
        required=True,
        choices=("before_market", "after_market"),
    )
    parser.add_argument("--timing-evidence-url", required=True)
    parser.add_argument(
        "--timing-evidence-timestamp",
        required=True,
        type=_timestamp,
    )
    parser.add_argument("--broad-market-ticker", default="SPY")
    parser.add_argument("--sector-benchmark-ticker", required=True)
    parser.add_argument("--tolerance", type=Decimal, default=Decimal("0.0001"))
    parser.add_argument("--publish-artifact", action="store_true")
    return parser.parse_args()


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def run_reconciliation(args: argparse.Namespace) -> dict:
    from src.db.connection import DatabasePool, store
    from src.services.earnings_reaction_reconciliation import (
        reconcile_earnings_reaction,
    )
    from src.services.static_artifacts import safe_publish_json_artifact

    ticker = args.ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker is required")
    DatabasePool.initialize()
    try:
        start_date = args.report_date - timedelta(days=45)
        end_date = args.report_date + timedelta(days=45)
        rows = store.get_stock_data(ticker, start_date, end_date)
        broad_market_rows = store.get_stock_data(
            args.broad_market_ticker, start_date, end_date
        )
        sector_rows = store.get_stock_data(
            args.sector_benchmark_ticker, start_date, end_date
        )
        result = reconcile_earnings_reaction(
            ticker=ticker,
            report_date=args.report_date,
            time_of_day=args.timing,
            timing_evidence_url=args.timing_evidence_url,
            timing_evidence_timestamp=args.timing_evidence_timestamp,
            stock_rows=rows,
            broad_market_rows=broad_market_rows,
            sector_rows=sector_rows,
            broad_market_ticker=args.broad_market_ticker,
            sector_benchmark_ticker=args.sector_benchmark_ticker,
            tolerance=args.tolerance,
        )
        if args.publish_artifact:
            bucket = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "").strip()
            if not bucket:
                raise ValueError(
                    "STOCKARA_ARTIFACT_BUCKET is required when publishing artifacts"
                )
            safe_publish_json_artifact(
                bucket,
                (
                    "earnings/reactions/reconciliations/"
                    f"{ticker}/{args.report_date.isoformat()}.json"
                ),
                result,
            )
        print(json.dumps(result, sort_keys=True, default=str))
        if result["status"] != "passed":
            raise RuntimeError("earnings reaction reconciliation failed")
        return result
    finally:
        DatabasePool.close()


if __name__ == "__main__":
    run_reconciliation(_parse_args())
