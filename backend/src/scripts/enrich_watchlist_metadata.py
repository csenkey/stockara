"""Enrich data/watchlist_seed.csv with source-backed static company metadata."""

import argparse
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any

import requests


NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks?download=true"
NASDAQ_PROFILE_URL = "https://api.nasdaq.com/api/company/{symbol}/company-profile"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}

SECTOR_MAP = {
    "Basic Materials": "Materials",
    "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples",
    "Energy": "Energy",
    "Finance": "Finance",
    "Health Care": "Healthcare",
    "Healthcare": "Healthcare",
    "Industrials": "Industrials",
    "Real Estate": "Real Estate",
    "Technology": "Technology",
    "Telecommunications": "Telecommunications",
    "Utilities": "Utilities",
}

MISC_INDUSTRY_SECTOR_MAP = {
    "Industrial Machinery/Components": "Industrials",
}

FIELDNAMES = [
    "ticker",
    "company_name",
    "sector",
    "industry",
    "company_size",
    "source",
    "metadata_source",
    "metadata_source_url",
    "metadata_as_of",
    "business_description",
    "flagship_products",
    "revenue_segments",
    "primary_customers",
    "geographic_exposure",
    "competitive_position",
    "key_static_risks",
    "exchange",
    "currency",
    "country",
    "website",
    "founded_year",
    "headquarters",
    "ipo_year",
    "market_cap",
]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split()).strip()


def _symbol_variants(ticker: str) -> list[str]:
    ticker = ticker.upper()
    variants = [ticker]
    if "." in ticker:
        variants.append(ticker.replace(".", "/"))
        variants.append(ticker.replace(".", "-"))
    return list(dict.fromkeys(variants))


def _extract_profile_value(profile: dict[str, Any], key: str) -> str:
    value = profile.get(key)
    if isinstance(value, dict):
        return _clean(value.get("value"))
    return _clean(value)


def _fetch_json(url: str, timeout: int = 30) -> dict[str, Any]:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()


def load_screener(cache_path: Path, refresh: bool) -> dict[str, dict[str, Any]]:
    if refresh or not cache_path.exists():
        data = _fetch_json(NASDAQ_SCREENER_URL, timeout=60)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    else:
        data = json.loads(cache_path.read_text(encoding="utf-8"))

    rows = data.get("data", {}).get("rows", [])
    return {str(row.get("symbol", "")).upper(): row for row in rows if row.get("symbol")}


def fetch_profile(ticker: str) -> tuple[str, dict[str, Any] | None]:
    for symbol in _symbol_variants(ticker):
        try:
            data = _fetch_json(NASDAQ_PROFILE_URL.format(symbol=symbol), timeout=20)
            if data.get("data"):
                return ticker, data["data"]
        except Exception:
            continue
    return ticker, None


def load_profiles(
    tickers: list[str], cache_path: Path, refresh: bool, max_workers: int
) -> dict[str, dict[str, Any]]:
    if cache_path.exists() and not refresh:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        cached = {}

    missing = [ticker for ticker in tickers if ticker not in cached]
    if missing:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_profile, ticker): ticker for ticker in missing}
            for index, future in enumerate(as_completed(futures), start=1):
                ticker, profile = future.result()
                cached[ticker] = profile or {}
                if index % 50 == 0:
                    cache_path.write_text(json.dumps(cached, indent=2), encoding="utf-8")
                time.sleep(0.02)
        cache_path.write_text(json.dumps(cached, indent=2), encoding="utf-8")

    return {ticker: profile for ticker, profile in cached.items() if profile}


def normalize_sector(raw_sector: str, raw_industry: str) -> str:
    sector = SECTOR_MAP.get(raw_sector, "")
    if sector:
        return sector
    return MISC_INDUSTRY_SECTOR_MAP.get(raw_industry, "")


def enrich_row(
    row: dict[str, str],
    screener: dict[str, dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    metadata_as_of: str,
) -> tuple[dict[str, str], list[str]]:
    ticker = row["ticker"].strip().upper()
    screen = screener.get(ticker, {})
    profile = profiles.get(ticker, {})

    profile_name = _extract_profile_value(profile, "CompanyName")
    screen_name = _clean(screen.get("name")).removesuffix(" Common Stock").strip()
    company_name = profile_name or screen_name

    raw_sector = _extract_profile_value(profile, "Sector") or _clean(screen.get("sector"))
    raw_industry = _extract_profile_value(profile, "Industry") or _clean(screen.get("industry"))
    sector = normalize_sector(raw_sector, raw_industry)
    description = _extract_profile_value(profile, "CompanyDescription")
    website = _extract_profile_value(profile, "CompanyUrl")
    headquarters = _extract_profile_value(profile, "Address")

    metadata_sources = []
    if screen:
        metadata_sources.append("nasdaq_screener")
    if profile:
        metadata_sources.append("nasdaq_company_profile")

    metadata_url = f"https://www.nasdaq.com/market-activity/stocks/{ticker.lower().replace('.', '-')}"
    enriched = {
        "ticker": ticker,
        "company_name": company_name,
        "sector": sector,
        "industry": raw_industry,
        "company_size": row["company_size"].strip().lower(),
        "source": row["source"].strip(),
        "metadata_source": "|".join(metadata_sources),
        "metadata_source_url": metadata_url if metadata_sources else "",
        "metadata_as_of": metadata_as_of if metadata_sources else "",
        "business_description": description,
        "flagship_products": "",
        "revenue_segments": "",
        "primary_customers": "",
        "geographic_exposure": _extract_profile_value(profile, "Region"),
        "competitive_position": "",
        "key_static_risks": "",
        "exchange": "",
        "currency": "USD" if screen else "",
        "country": _clean(screen.get("country")),
        "website": website,
        "founded_year": "",
        "headquarters": headquarters,
        "ipo_year": _clean(screen.get("ipoyear")),
        "market_cap": _clean(screen.get("marketCap")),
    }

    missing = [
        field
        for field in (
            "company_name",
            "sector",
            "industry",
            "metadata_source",
            "metadata_source_url",
            "metadata_as_of",
        )
        if not enriched[field]
    ]
    return enriched, missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/watchlist_seed.csv")
    parser.add_argument("--output", default="data/watchlist_seed.csv")
    parser.add_argument("--gap-report", default="docs/WATCHLIST_METADATA_GAPS.md")
    parser.add_argument("--cache-dir", default="/private/tmp/stockara_metadata_cache")
    parser.add_argument("--metadata-as-of", default=date.today().isoformat())
    parser.add_argument("--max-workers", type=int, default=12)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    screener = load_screener(cache_dir / "nasdaq_screener.json", args.refresh)

    input_path = Path(args.input)
    rows = list(csv.DictReader(input_path.open(newline="", encoding="utf-8")))
    tickers = [row["ticker"].strip().upper() for row in rows]
    profiles = load_profiles(
        tickers,
        cache_dir / "nasdaq_company_profiles.json",
        args.refresh,
        args.max_workers,
    )

    enriched_rows = []
    gaps: list[tuple[str, list[str]]] = []
    for row in rows:
        enriched, missing = enrich_row(row, screener, profiles, args.metadata_as_of)
        enriched_rows.append(enriched)
        if missing:
            gaps.append((enriched["ticker"], missing))

    output_path = Path(args.output)
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(enriched_rows)

    gap_path = Path(args.gap_report)
    with gap_path.open("w", encoding="utf-8") as file:
        file.write("# Watchlist Metadata Gaps\n\n")
        file.write(
            "Rows listed here still need manual or alternate-provider metadata before "
            "they should be treated as decision-grade Phase 1 inputs.\n\n"
        )
        file.write(f"- Total seed rows: {len(enriched_rows)}\n")
        file.write(f"- Rows with required metadata gaps: {len(gaps)}\n\n")
        if gaps:
            file.write("| Ticker | Missing required fields |\n")
            file.write("| --- | --- |\n")
            for ticker, missing in gaps:
                file.write(f"| {ticker} | {', '.join(missing)} |\n")

    print(
        f"wrote {output_path} with {len(enriched_rows)} rows; "
        f"{len(gaps)} rows still have required metadata gaps"
    )
    return 1 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
