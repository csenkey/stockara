"""Download Tiingo EOD OHLCV CSVs for Stockara backtest imports.

This is an operator script for manual historical data collection. It writes raw
provider CSV files and a manifest; it does not normalize data, upload to S3, or
run any backtest.

Example:
    TIINGO_API_TOKEN=... \
    python -m scripts.download_tiingo_ohlcv \
      --start-date 2021-12-01 \
      --end-date 2023-01-31 \
      --max-tickers 496 \
      --sleep 75
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCHLIST = PROJECT_ROOT / "data/watchlist_seed.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/backtest-import/raw/tiingo"
TIINGO_PRICE_URL = "https://api.tiingo.com/tiingo/daily/{symbol}/prices"


@dataclass(frozen=True)
class DownloadTarget:
    ticker: str
    provider_symbol: str
    instrument_type: str
    output_path: Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Tiingo EOD OHLCV CSVs for Stockara watchlist tickers."
    )
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-date", default="2021-12-01")
    parser.add_argument("--end-date", default="2023-01-31")
    parser.add_argument(
        "--tickers",
        help="Comma-separated stock tickers. Defaults to all tickers in --watchlist.",
    )
    parser.add_argument(
        "--etfs",
        default="SPY,VOO,QQQ,VTI",
        help="Comma-separated ETF tickers to download in addition to stocks.",
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=0,
        help="Maximum selected stock tickers to process. 0 means no cap.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Start offset after stock ticker sorting, useful for chunked runs.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=75,
        help="Seconds to sleep between Tiingo requests. Default stays below 50 requests/hour.",
    )
    parser.add_argument(
        "--max-symbols-per-run",
        type=int,
        default=500,
        help="Safety cap for unique symbols in one run. Tiingo basic API allows 500/month.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retry count for transient HTTP failures.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing ticker CSVs. By default existing files are skipped.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned downloads and write no files.",
    )
    parser.add_argument(
        "--dot-symbol-style",
        choices=["dash", "dot"],
        default="dash",
        help="Tiingo symbol style for tickers such as BRK.B. Default BRK-B.",
    )
    parser.add_argument(
        "--token-env",
        default="TIINGO_API_TOKEN",
        help="Environment variable containing the Tiingo API token.",
    )
    return parser.parse_args()


def load_watchlist_tickers(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if "ticker" not in (reader.fieldnames or []):
            raise ValueError(f"{path} must contain a ticker column")
        tickers = {row["ticker"].strip().upper() for row in reader if row.get("ticker", "").strip()}
    return sorted(tickers)


def parse_tickers(value: str | None) -> list[str]:
    if not value:
        return []
    return sorted({ticker.strip().upper() for ticker in value.split(",") if ticker.strip()})


def tiingo_symbol(ticker: str, *, dot_symbol_style: str = "dash") -> str:
    normalized = ticker.strip().upper()
    if dot_symbol_style == "dash":
        return normalized.replace(".", "-")
    return normalized


def select_stock_tickers(args: argparse.Namespace) -> list[str]:
    requested = parse_tickers(args.tickers)
    stocks = requested or load_watchlist_tickers(args.watchlist)
    offset = max(args.offset, 0)
    stocks = stocks[offset:]
    if args.max_tickers > 0:
        stocks = stocks[: args.max_tickers]
    return stocks


def build_targets(args: argparse.Namespace) -> list[DownloadTarget]:
    output_dir = args.output_dir
    targets: list[DownloadTarget] = []
    for ticker in select_stock_tickers(args):
        targets.append(
            DownloadTarget(
                ticker=ticker,
                provider_symbol=tiingo_symbol(ticker, dot_symbol_style=args.dot_symbol_style),
                instrument_type="stock",
                output_path=output_dir / "prices/stocks" / f"{ticker}.csv",
            )
        )
    for ticker in parse_tickers(args.etfs):
        targets.append(
            DownloadTarget(
                ticker=ticker,
                provider_symbol=tiingo_symbol(ticker, dot_symbol_style=args.dot_symbol_style),
                instrument_type="etf",
                output_path=output_dir / "prices/etfs" / f"{ticker}.csv",
            )
        )
    return targets


def _write_manifest(output_dir: Path, manifest: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _request_csv(
    session: requests.Session,
    target: DownloadTarget,
    *,
    token: str,
    start_date: str,
    end_date: str,
    retries: int,
) -> str:
    params = {
        "startDate": start_date,
        "endDate": end_date,
        "format": "csv",
        "token": token,
    }
    url = TIINGO_PRICE_URL.format(symbol=target.provider_symbol)
    last_error = ""
    for attempt in range(retries + 1):
        response = session.get(url, params=params, timeout=30)
        if response.status_code == 200:
            text = response.text.strip()
            if not text:
                raise RuntimeError("empty CSV response")
            if "date" not in text.splitlines()[0].lower():
                raise RuntimeError("CSV response does not include a date header")
            return text + "\n"
        last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < retries:
            time.sleep(2**attempt)
            continue
        break
    raise RuntimeError(last_error)


def download_targets(
    targets: list[DownloadTarget],
    *,
    token: str,
    start_date: str,
    end_date: str,
    output_dir: Path,
    sleep_seconds: float,
    retries: int,
    force: bool,
    dry_run: bool,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    session = session or requests.Session()
    manifest: dict[str, Any] = {
        "source": "tiingo",
        "source_url": "https://www.tiingo.com/documentation/end-of-day",
        "terms_url": "https://www.tiingo.com/about/terms",
        "downloaded_at": datetime.now(UTC).isoformat(),
        "coverage_start": start_date,
        "coverage_end": end_date,
        "output_dir": str(output_dir),
        "dry_run": dry_run,
        "requested_count": len(targets),
        "downloaded": [],
        "skipped_existing": [],
        "failed": [],
    }

    for index, target in enumerate(targets, start=1):
        record = {
            "ticker": target.ticker,
            "provider_symbol": target.provider_symbol,
            "instrument_type": target.instrument_type,
            "path": str(target.output_path),
        }
        if dry_run:
            print(f"[{index}/{len(targets)}] would download {target.ticker} -> {target.output_path}")
            manifest["downloaded"].append({**record, "status": "planned"})
            continue
        if target.output_path.exists() and not force:
            print(f"[{index}/{len(targets)}] skip existing {target.ticker}: {target.output_path}")
            manifest["skipped_existing"].append(record)
            continue

        try:
            csv_text = _request_csv(
                session,
                target,
                token=token,
                start_date=start_date,
                end_date=end_date,
                retries=retries,
            )
            target.output_path.parent.mkdir(parents=True, exist_ok=True)
            target.output_path.write_text(csv_text, encoding="utf-8")
            row_count = max(len(csv_text.splitlines()) - 1, 0)
            print(f"[{index}/{len(targets)}] downloaded {target.ticker}: {row_count} rows")
            manifest["downloaded"].append({**record, "row_count": row_count})
        except Exception as exc:  # noqa: BLE001 - operator script should continue.
            print(f"[{index}/{len(targets)}] failed {target.ticker}: {exc}")
            manifest["failed"].append({**record, "error": str(exc)})
        if sleep_seconds > 0 and index < len(targets):
            time.sleep(sleep_seconds)

    if not dry_run:
        _write_manifest(output_dir, manifest)
        print(f"Manifest written to {output_dir / 'manifest.json'}")
    return manifest


def main() -> int:
    args = _parse_args()
    token = os.environ.get(args.token_env, "").strip()
    if not token and not args.dry_run:
        raise SystemExit(f"Missing Tiingo token. Set {args.token_env}=...")

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        raise SystemExit("--end-date must be on or after --start-date")

    targets = build_targets(args)
    if not targets:
        raise SystemExit("No tickers selected")
    if args.max_symbols_per_run > 0 and len(targets) > args.max_symbols_per_run:
        raise SystemExit(
            f"Selected {len(targets)} symbols, above --max-symbols-per-run "
            f"{args.max_symbols_per_run}. Use --offset/--max-tickers to download in chunks."
        )
    download_targets(
        targets,
        token=token,
        start_date=args.start_date,
        end_date=args.end_date,
        output_dir=args.output_dir,
        sleep_seconds=max(args.sleep, 0),
        retries=max(args.retries, 0),
        force=args.force,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
