"""Executable contract checks for the canonical Phase 1 watchlist seed."""

import csv
from pathlib import Path

from backend.src.scripts.seed_watchlist_handler import (
    REQUIRED_METADATA_FIELDS,
    VALID_COMPANY_SIZES,
    VALID_SECTORS,
)


WATCHLIST_SEED = Path(__file__).resolve().parents[2] / "data" / "watchlist_seed.csv"
PACKAGED_WATCHLIST_SEED = (
    Path(__file__).resolve().parents[1] / "src" / "data" / "watchlist_seed.csv"
)


def test_watchlist_seed_has_decision_grade_required_metadata():
    with WATCHLIST_SEED.open(newline="") as file:
        rows = list(csv.DictReader(file))

    assert rows, "watchlist seed must contain active Phase 1 stocks"
    assert REQUIRED_METADATA_FIELDS <= set(rows[0])

    seen_tickers: set[str] = set()
    missing_by_ticker: dict[str, list[str]] = {}
    invalid_sectors: dict[str, str] = {}
    invalid_sizes: dict[str, str] = {}

    for row in rows:
        ticker = (row.get("ticker") or "").strip().upper()
        assert ticker, "watchlist seed row is missing ticker"
        assert ticker not in seen_tickers, f"duplicate watchlist ticker: {ticker}"
        seen_tickers.add(ticker)

        missing = [
            field
            for field in sorted(REQUIRED_METADATA_FIELDS)
            if not (row.get(field) or "").strip()
        ]
        if missing:
            missing_by_ticker[ticker] = missing

        sector = (row.get("sector") or "").strip()
        if sector not in VALID_SECTORS:
            invalid_sectors[ticker] = sector

        company_size = (row.get("company_size") or "").strip().lower()
        if company_size not in VALID_COMPANY_SIZES:
            invalid_sizes[ticker] = company_size

    assert missing_by_ticker == {}
    assert invalid_sectors == {}
    assert invalid_sizes == {}


def test_packaged_watchlist_seed_matches_canonical_seed():
    assert PACKAGED_WATCHLIST_SEED.read_text() == WATCHLIST_SEED.read_text()
