"""CloudFormation custom resource handler for first-run watchlist seeding."""

import csv
import os
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr
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

OPTIONAL_PROVIDER_SYMBOL_FIELDS = (
    "provider_symbols",
    "provider_symbol_sources",
)

SYNC_FIELDS = (
    "company_name",
    "sector",
    "industry",
    "company_size",
    "source",
    "metadata_source",
    "metadata_source_url",
    "metadata_as_of",
    *OPTIONAL_TEXT_FIELDS,
    *OPTIONAL_LIST_FIELDS,
    *OPTIONAL_PROVIDER_SYMBOL_FIELDS,
    "provider_symbol_updated_at",
    "is_active",
    "is_sell_alert_watch",
)


def _required(row: dict[str, str], field: str, ticker: str) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise ValueError(f"{ticker}: missing required metadata field '{field}'")
    return value


def _split_list(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split("|") if part.strip()]


def _split_provider_map(value: str | None) -> dict[str, str]:
    entries: dict[str, str] = {}
    for part in _split_list(value):
        if ":" not in part:
            continue
        provider, symbol = part.split(":", 1)
        provider = provider.strip().lower()
        symbol = symbol.strip()
        if provider and symbol:
            entries[provider] = symbol
    return entries


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

    for field in OPTIONAL_PROVIDER_SYMBOL_FIELDS:
        values = _split_provider_map(row.get(field))
        if values:
            item[field] = values
    provider_symbol_updated_at = (row.get("provider_symbol_updated_at") or "").strip()
    if provider_symbol_updated_at:
        item["provider_symbol_updated_at"] = provider_symbol_updated_at

    return item


def _seed_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "watchlist_seed.csv"


def _load_seed_rows() -> list[dict[str, str]]:
    with _seed_path().open(newline="") as file:
        reader = csv.DictReader(file)
        _validate_header(reader.fieldnames)
        return list(reader)


def _sync_values(item: dict[str, Any]) -> dict[str, Any]:
    return {field: item[field] for field in SYNC_FIELDS if field in item}


def _metadata_changed(existing: dict[str, Any], desired: dict[str, Any]) -> bool:
    for field in SYNC_FIELDS:
        if existing.get(field) != desired.get(field):
            return True
    return False


def _update_stock_metadata(table: Any, item: dict[str, Any]) -> None:
    ticker = item["ticker"]
    values = _sync_values(item)
    expression_names = {f"#f{index}": field for index, field in enumerate(values)}
    expression_values = {f":v{index}": value for index, value in enumerate(values.values())}
    set_parts = [
        f"{name} = {value_name}"
        for name, value_name in zip(expression_names, expression_values)
    ]
    remove_fields = [
        field
        for field in (*OPTIONAL_TEXT_FIELDS, *OPTIONAL_LIST_FIELDS)
        if field not in values
    ]
    remove_names = {
        f"#r{index}": field for index, field in enumerate(remove_fields)
    }
    update_expression = "SET " + ", ".join(set_parts)
    if remove_names:
        update_expression += " REMOVE " + ", ".join(remove_names)
    table.update_item(
        Key={"PK": f"STOCK#{ticker}", "SK": "META"},
        UpdateExpression=update_expression,
        ExpressionAttributeNames={**expression_names, **remove_names},
        ExpressionAttributeValues=expression_values,
        ConditionExpression=Attr("PK").exists(),
    )


def sync_static_metadata(
    table: Any, sell_alert_tickers: set[str], *, strict: bool = True
) -> dict[str, int]:
    summary = {
        "created": 0,
        "changed": 0,
        "unchanged": 0,
        "invalid": 0,
    }
    with table.batch_writer() as batch:
        for row in _load_seed_rows():
            try:
                item = _build_stock_item(row, sell_alert_tickers)
            except ValueError:
                summary["invalid"] += 1
                if strict:
                    raise
                continue
            existing = table.get_item(
                Key={"PK": item["PK"], "SK": item["SK"]}
            ).get("Item")
            if not existing:
                batch.put_item(Item=item)
                summary["created"] += 1
            elif _metadata_changed(existing, item):
                _update_stock_metadata(table, item)
                summary["changed"] += 1
            else:
                summary["unchanged"] += 1
    return summary


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    props = event.get("ResourceProperties", {})
    table_name = props.get("TableName") or event.get("table_name") or os.environ.get(
        "STOCKARA_TABLE_NAME"
    )
    if not table_name:
        raise ValueError("TableName, table_name, or STOCKARA_TABLE_NAME is required")
    sell_alert_tickers = {
        ticker.strip().upper()
        for ticker in (
            props.get("SellAlertTickers")
            or event.get("sell_alert_tickers")
            or "AAPL,MSFT,NVDA"
        ).split(",")
        if ticker.strip()
    }

    if event.get("RequestType") == "Delete":
        return {"PhysicalResourceId": f"{table_name}-watchlist-seed"}

    table = boto3.resource("dynamodb").Table(table_name)
    if event.get("mode") == "sync_static_metadata":
        summary = sync_static_metadata(table, sell_alert_tickers)
        return {"statusCode": 200, "body": summary}

    existing = table.query(
        IndexName="GSI1",
        Select="COUNT",
        KeyConditionExpression=Key("GSI1PK").eq("STOCK"),
        Limit=1,
    ).get("Count", 0)
    if existing:
        summary = sync_static_metadata(table, sell_alert_tickers, strict=False)
        return {
            "PhysicalResourceId": f"{table_name}-watchlist-seed",
            "Data": {
                "Seeded": summary["created"],
                "Skipped": True,
                "MetadataCreated": summary["created"],
                "MetadataChanged": summary["changed"],
                "MetadataUnchanged": summary["unchanged"],
                "MetadataInvalid": summary["invalid"],
            },
        }

    seeded = 0
    with _seed_path().open(newline="") as file:
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
