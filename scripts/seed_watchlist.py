"""Seed the Phase 1 tracked universe into DynamoDB.

Usage:
    STOCKARA_TABLE_NAME=... python -m scripts.seed_watchlist
"""

import csv
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from backend.src.db.connection import DatabasePool, store  # noqa: E402


SECTOR_MAP = {
    "AAPL": "Technology",
    "MSFT": "Technology",
    "NVDA": "Technology",
    "AVGO": "Technology",
    "ORCL": "Technology",
    "CRM": "Technology",
    "AMD": "Technology",
    "ADBE": "Technology",
    "JPM": "Finance",
    "V": "Finance",
    "MA": "Finance",
    "BAC": "Finance",
    "WFC": "Finance",
    "UNH": "Healthcare",
    "JNJ": "Healthcare",
    "LLY": "Healthcare",
    "ABBV": "Healthcare",
    "MRK": "Healthcare",
    "AMZN": "Consumer Discretionary",
    "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary",
    "MCD": "Consumer Discretionary",
    "PG": "Consumer Staples",
    "KO": "Consumer Staples",
    "PEP": "Consumer Staples",
    "COST": "Consumer Staples",
    "GOOGL": "Communication Services",
    "GOOG": "Communication Services",
    "META": "Communication Services",
    "NFLX": "Communication Services",
    "GE": "Industrials",
    "CAT": "Industrials",
    "HON": "Industrials",
    "BA": "Industrials",
    "XOM": "Energy",
    "CVX": "Energy",
    "COP": "Energy",
    "NEE": "Utilities",
    "DUK": "Utilities",
    "PLD": "Real Estate",
    "AMT": "Real Estate",
    "LIN": "Materials",
    "APD": "Materials",
}


def sector_for(ticker: str) -> str:
    return SECTOR_MAP.get(ticker, "Technology")


def seed_watchlist(csv_path: str | None = None, sell_alerts: list[str] | None = None) -> int:
    if csv_path is None:
        csv_path = os.path.join(PROJECT_ROOT, "data", "watchlist_seed.csv")

    DatabasePool.initialize()
    count = 0
    with open(csv_path, newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            ticker = row["ticker"].strip().upper()
            store.put_stock(
                {
                    "ticker": ticker,
                    "company_name": ticker,
                    "sector": sector_for(ticker),
                    "company_size": row["company_size"].strip().lower(),
                    "source": row.get("source", "seed"),
                    "is_active": True,
                    "is_sell_alert_watch": ticker in set(sell_alerts or []),
                }
            )
            count += 1

    if sell_alerts:
        store.put_config_list("sell_alert_watchlist", [ticker.upper() for ticker in sell_alerts])

    print(f"Seeded {count} Phase 1 stocks into {os.environ.get('STOCKARA_TABLE_NAME')}.")
    return count


if __name__ == "__main__":
    raw_sell_alerts = os.environ.get("STOCKARA_SELL_ALERT_TICKERS", "")
    sell_alert_tickers = [ticker.strip().upper() for ticker in raw_sell_alerts.split(",") if ticker.strip()]
    seed_watchlist(sell_alerts=sell_alert_tickers)
