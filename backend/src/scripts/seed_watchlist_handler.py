"""CloudFormation custom resource handler for first-run watchlist seeding."""

import csv
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key


VALID_SECTORS = {
    "Technology",
    "Healthcare",
    "Finance",
    "Energy",
    "Consumer Discretionary",
    "Consumer Staples",
    "Industrials",
    "Materials",
    "Utilities",
    "Real Estate",
    "Communication Services",
    "Telecommunications",
}

VALID_COMPANY_SIZES = {"blue_chip", "mid_cap", "startup"}

REQUIRED_METADATA_FIELDS = {
    "ticker",
    "company_name",
    "sector",
    "industry",
    "company_size",
    "source",
    "metadata_source",
    "metadata_source_url",
    "metadata_as_of",
}

OPTIONAL_TEXT_FIELDS = (
    "business_description",
    "competitive_position",
    "exchange",
    "currency",
    "country",
    "website",
    "founded_year",
    "headquarters",
    "ipo_year",
    "market_cap",
)

OPTIONAL_LIST_FIELDS = (
    "flagship_products",
    "revenue_segments",
    "primary_customers",
    "geographic_exposure",
    "key_static_risks",
)


def _required(row: dict[str, str], field: str, ticker: str) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise ValueError(f"{ticker}: missing required metadata field '{field}'")
    return value


def _split_list(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def _validate_header(fieldnames: list[str] | None) -> None:
    available = set(fieldnames or [])
    missing = sorted(REQUIRED_METADATA_FIELDS - available)
    if missing:
        raise ValueError(
            "watchlist seed is missing required metadata columns: "
            + ", ".join(missing)
        )


def _build_stock_item(row: dict[str, str], sell_alert_tickers: set[str]) -> dict[str, Any]:
    ticker = _required(row, "ticker", "<unknown>").upper()
    sector = _required(row, "sector", ticker)
    if sector not in VALID_SECTORS:
        raise ValueError(f"{ticker}: invalid sector '{sector}'")

    company_size = _required(row, "company_size", ticker).lower()
    if company_size not in VALID_COMPANY_SIZES:
        raise ValueError(f"{ticker}: invalid company_size '{company_size}'")

    item = {
        "PK": f"STOCK#{ticker}",
        "SK": "META",
        "GSI1PK": "STOCK",
        "GSI1SK": ticker,
        "entity": "stock",
        "ticker": ticker,
        "company_name": _required(row, "company_name", ticker),
        "sector": sector,
        "industry": _required(row, "industry", ticker),
        "company_size": company_size,
        "source": _required(row, "source", ticker),
        "metadata_source": _required(row, "metadata_source", ticker),
        "metadata_source_url": _required(row, "metadata_source_url", ticker),
        "metadata_as_of": _required(row, "metadata_as_of", ticker),
        "is_active": True,
        "is_sell_alert_watch": ticker in sell_alert_tickers,
    }

    for field in OPTIONAL_TEXT_FIELDS:
        value = (row.get(field) or "").strip()
        if value:
            item[field] = value

    for field in OPTIONAL_LIST_FIELDS:
        values = _split_list(row.get(field))
        if values:
            item[field] = values

    return item


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    props = event.get("ResourceProperties", {})
    table_name = props["TableName"]
    sell_alert_tickers = {
        ticker.strip().upper()
        for ticker in props.get("SellAlertTickers", "").split(",")
        if ticker.strip()
    }

    if event.get("RequestType") == "Delete":
        return {"PhysicalResourceId": f"{table_name}-watchlist-seed"}

    table = boto3.resource("dynamodb").Table(table_name)
    existing = table.query(
        IndexName="GSI1",
        Select="COUNT",
        KeyConditionExpression=Key("GSI1PK").eq("STOCK"),
        Limit=1,
    ).get("Count", 0)
    if existing:
        return {
            "PhysicalResourceId": f"{table_name}-watchlist-seed",
            "Data": {"Seeded": 0, "Skipped": True},
        }

    seeded = 0
    seed_path = Path(__file__).resolve().parents[3] / "data" / "watchlist_seed.csv"
    with seed_path.open(newline="") as file:
        reader = csv.DictReader(file)
        _validate_header(reader.fieldnames)
        with table.batch_writer() as batch:
            for row in reader:
                batch.put_item(Item=_build_stock_item(row, sell_alert_tickers))
                seeded += 1

            if sell_alert_tickers:
                batch.put_item(
                    Item={
                        "PK": "CONFIG#sell_alert_watchlist",
                        "SK": "VALUE",
                        "entity": "config",
                        "name": "sell_alert_watchlist",
                        "values": sorted(sell_alert_tickers),
                    }
                )

    return {
        "PhysicalResourceId": f"{table_name}-watchlist-seed",
        "Data": {"Seeded": seeded, "Skipped": False},
    }
