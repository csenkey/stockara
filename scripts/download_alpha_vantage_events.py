"""Download Alpha Vantage event data for Stockara backtest imports.

This operator script writes raw provider files plus a manifest. It does not
normalize data, upload to S3, or run any backtest.

Useful first pass:
    ALPHA_VANTAGE_API_KEY=... \
    python -m scripts.download_alpha_vantage_events \
      --include earnings,dividends \
      --max-requests-per-run 20 \
      --sleep 15

Repeated runs automatically skip existing files and continue with the next
missing targets.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCHLIST = PROJECT_ROOT / "data/watchlist_seed.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/backtest-import/raw/alpha_vantage"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
DEFAULT_LISTING_DATES = "2021-12-01,2022-01-01,2022-12-31,2023-01-31"
EVENT_TYPES = {"earnings", "dividends", "listing-status"}


@dataclass(frozen=True)
class ApiKeyRef:
    env_name: str
    value: str


@dataclass(frozen=True)
class DownloadTarget:
    ticker: str | None
    function: str
    output_path: Path
    params: dict[str, str]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download raw Alpha Vantage earnings/dividends/listing-status files."
    )
    parser.add_argument("--watchlist", type=Path, default=DEFAULT_WATCHLIST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--include",
        default="earnings,dividends,listing-status",
        help="Comma-separated: earnings, dividends, listing-status.",
    )
    parser.add_argument(
        "--tickers",
        help="Comma-separated stock tickers. Defaults to all tickers in --watchlist.",
    )
    parser.add_argument("--max-tickers", type=int, default=0, help="0 means no cap.")
    parser.add_argument("--offset", type=int, default=0, help="Sorted ticker offset.")
    parser.add_argument(
        "--listing-dates",
        default=DEFAULT_LISTING_DATES,
        help="Comma-separated dates for listing-status snapshots.",
    )
    parser.add_argument(
        "--listing-states",
        default="active,delisted",
        help="Comma-separated listing states, usually active,delisted.",
    )
    parser.add_argument(
        "--api-key-envs",
        default="ALPHA_VANTAGE_API_KEY",
        help="Comma-separated env vars. Multiple keys rotate by request.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=15,
        help="Seconds between requests. Tune to your Alpha Vantage plan.",
    )
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--max-requests-per-run",
        type=int,
        default=25,
        help=(
            "Download at most this many missing targets. Re-run the same command "
            "to continue. Set to 0 to disable after checking your plan."
        ),
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def parse_csv_arg(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def load_watchlist_tickers(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if "ticker" not in (reader.fieldnames or []):
            raise ValueError(f"{path} must contain a ticker column")
        tickers = {row["ticker"].strip().upper() for row in reader if row.get("ticker", "").strip()}
    return sorted(tickers)


def select_tickers(args: argparse.Namespace) -> list[str]:
    tickers = sorted({ticker.upper() for ticker in parse_csv_arg(args.tickers)})
    if not tickers:
        tickers = load_watchlist_tickers(args.watchlist)
    tickers = tickers[max(args.offset, 0) :]
    if args.max_tickers > 0:
        tickers = tickers[: args.max_tickers]
    return tickers


def selected_event_types(include: str) -> set[str]:
    selected = {item.lower() for item in parse_csv_arg(include)}
    unknown = selected - EVENT_TYPES
    if unknown:
        raise ValueError(f"Unknown include value(s): {', '.join(sorted(unknown))}")
    return selected


def load_api_keys(env_names: str) -> list[ApiKeyRef]:
    keys: list[ApiKeyRef] = []
    for env_name in parse_csv_arg(env_names):
        value = os.environ.get(env_name, "").strip()
        if value:
            keys.append(ApiKeyRef(env_name=env_name, value=value))
    return keys


def build_targets(args: argparse.Namespace) -> list[DownloadTarget]:
    event_types = selected_event_types(args.include)
    tickers = select_tickers(args)
    output_dir = args.output_dir
    targets: list[DownloadTarget] = []

    if "earnings" in event_types:
        for ticker in tickers:
            targets.append(
                DownloadTarget(
                    ticker=ticker,
                    function="EARNINGS",
                    output_path=output_dir / "events/earnings" / f"{ticker}.json",
                    params={"function": "EARNINGS", "symbol": ticker},
                )
            )
    if "dividends" in event_types:
        for ticker in tickers:
            targets.append(
                DownloadTarget(
                    ticker=ticker,
                    function="DIVIDENDS",
                    output_path=output_dir / "events/dividends" / f"{ticker}.json",
                    params={"function": "DIVIDENDS", "symbol": ticker},
                )
            )
    if "listing-status" in event_types:
        for listing_date in parse_csv_arg(args.listing_dates):
            for state in parse_csv_arg(args.listing_states):
                normalized_state = state.lower()
                targets.append(
                    DownloadTarget(
                        ticker=None,
                        function="LISTING_STATUS",
                        output_path=(
                            output_dir
                            / "instruments/listing_status"
                            / f"{listing_date}_{normalized_state}.csv"
                        ),
                        params={
                            "function": "LISTING_STATUS",
                            "date": listing_date,
                            "state": normalized_state,
                        },
                    )
                )
    return targets


def pending_targets(targets: list[DownloadTarget], *, force: bool) -> list[DownloadTarget]:
    if force:
        return targets
    return [target for target in targets if not target.output_path.exists()]


def _payload_has_provider_limit(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("Information", "Note", "Error Message"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _request_target(
    session: requests.Session,
    target: DownloadTarget,
    *,
    api_key: ApiKeyRef,
    retries: int,
) -> str:
    params = {**target.params, "apikey": api_key.value}
    last_error = ""
    for attempt in range(retries + 1):
        response = session.get(ALPHA_VANTAGE_URL, params=params, timeout=30)
        if response.status_code == 200:
            text = response.text.strip()
            if not text:
                raise RuntimeError("empty provider response")
            if target.function == "LISTING_STATUS":
                if "symbol" not in text.splitlines()[0].lower():
                    raise RuntimeError(f"unexpected listing-status CSV: {text[:200]}")
                return text + "\n"
            payload = response.json()
            provider_limit = _payload_has_provider_limit(payload)
            if provider_limit:
                raise RuntimeError(provider_limit)
            return json.dumps(payload, indent=2, sort_keys=True) + "\n"
        last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        if response.status_code in {408, 429, 500, 502, 503, 504} and attempt < retries:
            time.sleep(2**attempt)
            continue
        break
    raise RuntimeError(last_error)


def download_targets(
    targets: list[DownloadTarget],
    *,
    api_keys: list[ApiKeyRef],
    output_dir: Path,
    sleep_seconds: float,
    retries: int,
    force: bool,
    dry_run: bool,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    session = session or requests.Session()
    manifest: dict[str, Any] = {
        "source": "alpha_vantage",
        "source_url": "https://www.alphavantage.co/documentation/",
        "downloaded_at": datetime.now(UTC).isoformat(),
        "output_dir": str(output_dir),
        "dry_run": dry_run,
        "requested_count": len(targets),
        "api_key_envs": [key.env_name for key in api_keys],
        "downloaded": [],
        "skipped_existing": [],
        "failed": [],
    }

    for index, target in enumerate(targets, start=1):
        record = {
            "ticker": target.ticker,
            "function": target.function,
            "path": str(target.output_path),
            "params": target.params,
        }
        if dry_run:
            print(f"[{index}/{len(targets)}] would download {target.function} {target.ticker or ''}")
            manifest["downloaded"].append({**record, "status": "planned"})
            continue
        if target.output_path.exists() and not force:
            print(f"[{index}/{len(targets)}] skip existing {target.output_path}")
            manifest["skipped_existing"].append(record)
            continue

        api_key = api_keys[(index - 1) % len(api_keys)]
        try:
            text = _request_target(session, target, api_key=api_key, retries=retries)
            target.output_path.parent.mkdir(parents=True, exist_ok=True)
            target.output_path.write_text(text, encoding="utf-8")
            print(f"[{index}/{len(targets)}] downloaded {target.function} {target.ticker or ''}")
            manifest["downloaded"].append({**record, "api_key_env": api_key.env_name})
        except Exception as exc:  # noqa: BLE001 - operator script should continue.
            print(f"[{index}/{len(targets)}] failed {target.function} {target.ticker or ''}: {exc}")
            manifest["failed"].append({**record, "api_key_env": api_key.env_name, "error": str(exc)})
        if sleep_seconds > 0 and index < len(targets):
            time.sleep(sleep_seconds)

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Manifest written to {manifest_path}")
    return manifest


def main() -> int:
    args = _parse_args()
    all_targets = build_targets(args)
    if not all_targets:
        raise SystemExit("No targets selected")
    targets = pending_targets(all_targets, force=args.force)
    total_pending = len(targets)
    if args.max_requests_per_run > 0:
        targets = targets[: args.max_requests_per_run]
    if not targets:
        print("No missing targets. Existing files cover the selected request set.")
        return 0
    api_keys = load_api_keys(args.api_key_envs)
    if not api_keys and not args.dry_run:
        raise SystemExit(f"Missing API key. Set one of: {args.api_key_envs}")
    if args.dry_run:
        api_keys = api_keys or [ApiKeyRef(env_name="dry-run", value="")]
    download_targets(
        targets,
        api_keys=api_keys,
        output_dir=args.output_dir,
        sleep_seconds=max(args.sleep, 0),
        retries=max(args.retries, 0),
        force=args.force,
        dry_run=args.dry_run,
    )
    if args.max_requests_per_run > 0 and total_pending > len(targets):
        print(
            f"Downloaded batch of {len(targets)} missing target(s). "
            f"{total_pending - len(targets)} missing target(s) remain; "
            "re-run the same command to continue."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
