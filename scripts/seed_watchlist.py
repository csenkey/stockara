"""Sync the Phase 1 tracked universe static metadata into DynamoDB.

Usage:
    STOCKARA_TABLE_NAME=... python -m scripts.seed_watchlist
"""

import os
import sys

import boto3

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from backend.src.scripts.seed_watchlist_handler import (  # noqa: E402
    sync_static_metadata,
)


def _sell_alert_tickers() -> set[str]:
    raw_sell_alerts = os.environ.get("STOCKARA_SELL_ALERT_TICKERS", "AAPL,MSFT,NVDA")
    return {
        ticker.strip().upper()
        for ticker in raw_sell_alerts.split(",")
        if ticker.strip()
    }


def seed_watchlist(
    sell_alerts: set[str] | None = None, *, strict: bool = True
) -> dict[str, int]:
    table_name = os.environ.get("STOCKARA_TABLE_NAME", "stockara")
    table = boto3.resource("dynamodb").Table(table_name)
    configured_sell_alerts = (
        _sell_alert_tickers() if sell_alerts is None else sell_alerts
    )
    summary = sync_static_metadata(table, configured_sell_alerts, strict=strict)
    print(
        "Synced Phase 1 stock metadata into "
        f"{table_name}: created={summary['created']}, changed={summary['changed']}, "
        f"unchanged={summary['unchanged']}, invalid={summary['invalid']}."
    )
    return summary


if __name__ == "__main__":
    seed_watchlist()
