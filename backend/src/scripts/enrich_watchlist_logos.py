"""Enrich watchlist metadata with cached company logo assets.

The script prefers ticker-aware provider branding URLs when available, then
falls back to Logo.dev when a reliable company website domain is present. It
downloads provider assets and writes cached copies to the artifact bucket so
the frontend never hotlinks provider logo URLs.
"""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
import requests

from backend.src.scripts.enrich_watchlist_metadata import FIELDNAMES


POLYGON_TICKER_DETAILS_URL = "https://api.polygon.io/v3/reference/tickers/{ticker}"
LOGO_DEV_URL = "https://img.logo.dev/{domain}"
LOGO_FIELDNAMES = [
    "logo_url",
    "logo_icon_url",
    "logo_source",
    "logo_source_url",
    "logo_checked_at",
]


@dataclass(frozen=True)
class LogoCandidate:
    kind: str
    source: str
    source_url: str
    request_url: str | None = None


@dataclass(frozen=True)
class DownloadedLogo:
    kind: str
    source: str
    source_url: str
    content: bytes
    content_type: str


def canonical_fieldnames(fieldnames: list[str] | None = None) -> list[str]:
    fields = list(fieldnames or FIELDNAMES)
    for field in LOGO_FIELDNAMES:
        if field not in fields:
            fields.append(field)
    return fields


def cache_key(ticker: str, kind: str, content_type: str) -> str:
    extension = _extension_for_content_type(content_type)
    return f"logos/{ticker.upper()}/{kind}{extension}"


def metadata_key(ticker: str) -> str:
    return f"logos/{ticker.upper()}/metadata.json"


def public_url(base_url: str, key: str) -> str:
    return f"{base_url.rstrip('/')}/{key}"


def company_domain(website: str | None) -> str:
    if not website:
        return ""
    parsed = urlparse(website if "://" in website else f"https://{website}")
    host = (parsed.netloc or parsed.path).lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host.split("/")[0]


def polygon_logo_candidates(details: dict[str, Any]) -> list[LogoCandidate]:
    branding = details.get("branding") if isinstance(details.get("branding"), dict) else {}
    candidates: list[LogoCandidate] = []
    if branding.get("logo_url"):
        candidates.append(
            LogoCandidate("logo", "polygon_ticker_details", str(branding["logo_url"]))
        )
    if branding.get("icon_url"):
        candidates.append(
            LogoCandidate("icon", "polygon_ticker_details", str(branding["icon_url"]))
        )
    return candidates


def logo_dev_candidate(row: dict[str, str], token: str | None = None) -> LogoCandidate | None:
    domain = company_domain(row.get("website"))
    if not domain:
        return None
    source_url = LOGO_DEV_URL.format(domain=domain)
    request_url = f"{source_url}?token={token}" if token else source_url
    return LogoCandidate("logo", "logo_dev", source_url, request_url)


def download_logo(candidate: LogoCandidate, session: Any = requests) -> DownloadedLogo:
    response = session.get(candidate.request_url or candidate.source_url, timeout=30)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "application/octet-stream")
    return DownloadedLogo(
        kind=candidate.kind,
        source=candidate.source,
        source_url=candidate.source_url,
        content=response.content,
        content_type=content_type.split(";")[0].strip().lower(),
    )


def cache_downloaded_logos(
    s3_client: Any,
    bucket: str,
    ticker: str,
    logos: list[DownloadedLogo],
    public_base_url: str,
    checked_at: str,
) -> dict[str, str]:
    fields: dict[str, str] = {
        "logo_source": "",
        "logo_source_url": "",
        "logo_checked_at": checked_at,
    }
    metadata = {
        "ticker": ticker.upper(),
        "checked_at": checked_at,
        "assets": [],
    }

    for logo in logos:
        key = cache_key(ticker, logo.kind, logo.content_type)
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=logo.content,
            ContentType=logo.content_type,
            CacheControl="public, max-age=86400",
        )
        cached_url = public_url(public_base_url, key)
        if logo.kind == "icon":
            fields["logo_icon_url"] = cached_url
        else:
            fields["logo_url"] = cached_url
        fields["logo_source"] = logo.source
        fields["logo_source_url"] = logo.source_url
        metadata["assets"].append(
            {
                "kind": logo.kind,
                "source": logo.source,
                "source_url": logo.source_url,
                "cached_url": cached_url,
                "content_type": logo.content_type,
            }
        )

    s3_client.put_object(
        Bucket=bucket,
        Key=metadata_key(ticker),
        Body=json.dumps(metadata, indent=2).encode("utf-8"),
        ContentType="application/json",
        CacheControl="public, max-age=300",
    )
    return fields


def enrich_logo_fields(
    row: dict[str, str],
    *,
    polygon_details: dict[str, Any] | None,
    logo_dev_token: str | None,
    session: Any,
    s3_client: Any,
    bucket: str,
    public_base_url: str,
    checked_at: str,
) -> dict[str, str]:
    ticker = row["ticker"].strip().upper()
    candidates = polygon_logo_candidates(polygon_details or {})
    if not candidates:
        fallback = logo_dev_candidate(row, logo_dev_token)
        candidates = [fallback] if fallback else []

    downloaded: list[DownloadedLogo] = []
    for candidate in candidates:
        try:
            downloaded.append(download_logo(candidate, session=session))
        except Exception:
            continue

    if not downloaded:
        return {field: row.get(field, "") for field in LOGO_FIELDNAMES}
    return cache_downloaded_logos(
        s3_client, bucket, ticker, downloaded, public_base_url, checked_at
    )


def fetch_polygon_details(ticker: str, api_key: str, session: Any = requests) -> dict[str, Any]:
    response = session.get(
        POLYGON_TICKER_DETAILS_URL.format(ticker=ticker.upper()),
        params={"apiKey": api_key},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    result = data.get("results")
    return result if isinstance(result, dict) else {}


def _extension_for_content_type(content_type: str) -> str:
    normalized = content_type.split(";")[0].strip().lower()
    if normalized == "image/svg+xml":
        return ".svg"
    if normalized == "image/png":
        return ".png"
    if normalized in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    return mimetypes.guess_extension(normalized) or ".bin"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/watchlist_seed.csv")
    parser.add_argument("--output", default="data/watchlist_seed.csv")
    parser.add_argument("--bucket", default=os.environ.get("STOCKARA_ARTIFACT_BUCKET", ""))
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--polygon-api-key", default=os.environ.get("POLYGON_API_KEY", ""))
    parser.add_argument("--logo-dev-token", default=os.environ.get("LOGO_DEV_TOKEN", ""))
    parser.add_argument("--only-missing-logos", action="store_true")
    parser.add_argument("--max-rows", type=int, default=0)
    args = parser.parse_args()

    if not args.bucket:
        raise SystemExit("--bucket or STOCKARA_ARTIFACT_BUCKET is required")

    input_path = Path(args.input)
    with input_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        fieldnames = canonical_fieldnames(reader.fieldnames)
        rows = list(reader)

    checked_at = datetime.now(timezone.utc).isoformat()
    s3_client = boto3.client("s3")
    updated = 0
    for row in rows:
        if args.only_missing_logos and (
            (row.get("logo_url") or "").strip()
            or (row.get("logo_icon_url") or "").strip()
        ):
            continue
        if args.max_rows and updated >= args.max_rows:
            break
        polygon_details = (
            fetch_polygon_details(row["ticker"], args.polygon_api_key)
            if args.polygon_api_key
            else None
        )
        fields = enrich_logo_fields(
            row,
            polygon_details=polygon_details,
            logo_dev_token=args.logo_dev_token or None,
            session=requests,
            s3_client=s3_client,
            bucket=args.bucket,
            public_base_url=args.public_base_url,
            checked_at=checked_at,
        )
        row.update(fields)
        if fields.get("logo_url") or fields.get("logo_icon_url"):
            updated += 1

    output_path = Path(args.output)
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fieldnames} for row in rows
        )

    print(f"updated logo metadata for {updated} row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
