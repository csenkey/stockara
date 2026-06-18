"""DynamoDB access helpers for Stockara Phase 1."""

import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, AsyncGenerator, Iterable

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
import structlog

logger = structlog.get_logger(__name__)

TABLE_NAME = os.environ.get("STOCKARA_TABLE_NAME", "stockara")
GSI1 = "GSI1"

STOCK_STATIC_METADATA_FIELDS = (
    "industry",
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
)


def _now() -> str:
    return datetime.utcnow().isoformat()


def _date_str(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _strip_keys(item: dict[str, Any]) -> dict[str, Any]:
    hidden = {"PK", "SK", "GSI1PK", "GSI1SK", "entity"}
    return {key: value for key, value in item.items() if key not in hidden}


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def _to_dynamodb_value(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, Decimal):
        return value
    if isinstance(value, list):
        return [_to_dynamodb_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_dynamodb_value(v) for k, v in value.items()}
    return value


class DatabasePool:
    """Small compatibility facade around the DynamoDB table resource."""

    _resource = None
    _table = None

    @classmethod
    def initialize(cls) -> None:
        if cls._table is None:
            cls._resource = boto3.resource("dynamodb")
            cls._table = cls._resource.Table(TABLE_NAME)
            logger.info("dynamodb_table_initialized", table=TABLE_NAME)

    @classmethod
    def close(cls) -> None:
        return None

    @classmethod
    def table(cls):
        cls.initialize()
        return cls._table

    @classmethod
    @asynccontextmanager
    async def get_connection(cls) -> AsyncGenerator:
        yield cls.table()


@asynccontextmanager
async def get_db_connection() -> AsyncGenerator:
    yield DatabasePool.table()


async def run_migrations(migrations_dir: str | None = None) -> None:
    logger.info("dynamodb_migrations_skipped", table=TABLE_NAME)


class DynamoStore:
    """Single-table repository for the Phase 1 batch pipeline."""

    @property
    def table(self):
        return DatabasePool.table()

    def ping(self) -> bool:
        self.table.load()
        return True

    def _scan(self, **kwargs) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        response = self.table.scan(**kwargs)
        items.extend(response.get("Items", []))
        while "LastEvaluatedKey" in response:
            response = self.table.scan(
                ExclusiveStartKey=response["LastEvaluatedKey"], **kwargs
            )
            items.extend(response.get("Items", []))
        return items

    def _query(self, **kwargs) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        response = self.table.query(**kwargs)
        items.extend(response.get("Items", []))
        while "LastEvaluatedKey" in response:
            response = self.table.query(
                ExclusiveStartKey=response["LastEvaluatedKey"], **kwargs
            )
            items.extend(response.get("Items", []))
        return items

    def list_stocks(
        self,
        sector: str | None = None,
        company_size: str | None = None,
        is_active: bool | None = None,
        sell_alert_watch: bool | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._query(
            IndexName=GSI1,
            KeyConditionExpression=Key("GSI1PK").eq("STOCK"),
        )
        if sector is not None:
            rows = [row for row in rows if row.get("sector") == sector]
        if company_size is not None:
            rows = [row for row in rows if row.get("company_size") == company_size]
        if is_active is not None:
            rows = [row for row in rows if bool(row.get("is_active")) is is_active]
        if sell_alert_watch is not None:
            rows = [
                row
                for row in rows
                if bool(row.get("is_sell_alert_watch")) is sell_alert_watch
            ]
        return sorted((_strip_keys(row) for row in rows), key=lambda row: row["ticker"])

    def get_stock(self, ticker: str) -> dict[str, Any] | None:
        row = self.table.get_item(Key={"PK": f"STOCK#{ticker}", "SK": "META"}).get("Item")
        return _strip_keys(row) if row else None

    def put_stock(self, stock: dict[str, Any], create_only: bool = False) -> dict[str, Any]:
        ticker = stock["ticker"].upper()
        now = _now()
        existing = self.get_stock(ticker)
        item = {
            "PK": f"STOCK#{ticker}",
            "SK": "META",
            "GSI1PK": "STOCK",
            "GSI1SK": ticker,
            "entity": "stock",
            "ticker": ticker,
            "company_name": stock.get("company_name") or ticker,
            "sector": stock["sector"],
            "company_size": stock["company_size"],
            "source": stock.get("source", "seed"),
            "added_at": existing.get("added_at") if existing else now,
            "is_active": bool(stock.get("is_active", existing.get("is_active") if existing else True)),
            "is_sell_alert_watch": bool(stock.get("is_sell_alert_watch", existing.get("is_sell_alert_watch") if existing else False)),
        }
        for field in STOCK_STATIC_METADATA_FIELDS:
            if field in stock:
                item[field] = stock[field]
            elif existing and field in existing:
                item[field] = existing[field]
        kwargs: dict[str, Any] = {"Item": item}
        if create_only:
            kwargs["ConditionExpression"] = Attr("PK").not_exists()
        self.table.put_item(**kwargs)
        return _strip_keys(item)

    def active_tickers(self) -> list[str]:
        return [row["ticker"] for row in self.list_stocks(is_active=True)]

    def active_stock_metadata(self) -> list[dict[str, Any]]:
        return self.list_stocks(is_active=True)

    def sell_alert_tickers(self) -> list[str]:
        configured = self.get_config_list("sell_alert_watchlist")
        if configured:
            return configured
        return [row["ticker"] for row in self.list_stocks(is_active=True, sell_alert_watch=True)]

    def put_stock_data(self, record: dict[str, Any]) -> bool:
        trading_date = _date_str(record["trading_date"])
        collected_at = record.get("collected_at", _now())
        item = {
            "PK": f"STOCKDATA#{record['ticker']}",
            "SK": f"DATE#{trading_date}",
            "GSI1PK": "STOCKDATA",
            "GSI1SK": collected_at,
            "entity": "stock_data",
            "ticker": record["ticker"],
            "trading_date": trading_date,
            "open_price": _decimal(record["open_price"]),
            "high_price": _decimal(record["high_price"]),
            "low_price": _decimal(record["low_price"]),
            "close_price": _decimal(record["close_price"]),
            "volume": int(record["volume"]),
            "collected_at": collected_at,
        }
        if record.get("adjusted_close_price") is not None:
            item["adjusted_close_price"] = _decimal(record["adjusted_close_price"])
        optional_fields = (
            "data_provider",
            "provider_symbol",
            "provider_endpoint",
            "provider_priority",
            "price_adjustment",
            "has_adjusted_close",
            "corporate_action_adjusted",
            "adjustment_context",
            "split_dividend_adjustment",
            "exchange",
            "currency",
            "fetch_period",
            "fetch_window_start",
            "fetch_window_end",
        )
        for field in optional_fields:
            if field in record:
                item[field] = record[field]
        try:
            self.table.put_item(
                Item=item,
                ConditionExpression=Attr("PK").not_exists() & Attr("SK").not_exists(),
            )
            self._mark_stock_data_collected(
                record["ticker"],
                trading_date,
                collected_at,
                item["close_price"],
                item.get("data_provider"),
                item.get("price_adjustment"),
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def _mark_stock_data_collected(
        self,
        ticker: str,
        trading_date: str,
        collected_at: str,
        close_price: Decimal,
        data_provider: str | None = None,
        price_adjustment: str | None = None,
    ) -> None:
        update_expression = (
            "SET latest_stock_data_date = :trading_date, "
            "latest_stock_data_collected_at = :collected_at, "
            "latest_close_price = :close_price"
        )
        expression_values: dict[str, Any] = {
            ":trading_date": trading_date,
            ":collected_at": collected_at,
            ":close_price": close_price,
        }
        if data_provider is not None:
            update_expression += ", latest_stock_data_provider = :data_provider"
            expression_values[":data_provider"] = data_provider
        if price_adjustment is not None:
            update_expression += ", latest_stock_price_adjustment = :price_adjustment"
            expression_values[":price_adjustment"] = price_adjustment
        update_expression += (
            " REMOVE latest_stock_collection_failed_at, "
            "latest_stock_collection_failure_reason, "
            "latest_stock_collection_retry_after, "
            "latest_stock_collection_failure_count"
        )
        try:
            self.table.update_item(
                Key={"PK": f"STOCK#{ticker}", "SK": "META"},
                UpdateExpression=update_expression,
                ConditionExpression=Attr("latest_stock_data_date").not_exists()
                | Attr("latest_stock_data_date").lt(trading_date),
                ExpressionAttributeValues=expression_values,
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
        self._put_system_status("STOCK_COLLECTION", collected_at)

    def mark_stock_collection_failed(
        self,
        ticker: str,
        reason: str,
        retry_after_hours: int,
        failed_at: str | None = None,
    ) -> None:
        failed_at = failed_at or _now()
        failed_time = datetime.fromisoformat(str(failed_at).replace("Z", "+00:00"))
        retry_after = failed_time + timedelta(hours=retry_after_hours)
        retry_after = retry_after.isoformat()
        self.table.update_item(
            Key={"PK": f"STOCK#{ticker}", "SK": "META"},
            UpdateExpression=(
                "SET latest_stock_collection_failed_at = :failed_at, "
                "latest_stock_collection_failure_reason = :reason, "
                "latest_stock_collection_retry_after = :retry_after, "
                "latest_stock_collection_failure_count = "
                "if_not_exists(latest_stock_collection_failure_count, :zero) + :one"
            ),
            ExpressionAttributeValues={
                ":failed_at": failed_at,
                ":reason": reason,
                ":retry_after": retry_after,
                ":zero": 0,
                ":one": 1,
            },
        )

    def clear_stock_collection_failure(self, ticker: str) -> None:
        self.table.update_item(
            Key={"PK": f"STOCK#{ticker}", "SK": "META"},
            UpdateExpression=(
                "REMOVE latest_stock_collection_failed_at, "
                "latest_stock_collection_failure_reason, "
                "latest_stock_collection_retry_after, "
                "latest_stock_collection_failure_count"
            ),
        )

    def get_stock_data(
        self, ticker: str, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        rows = self._query(
            KeyConditionExpression=Key("PK").eq(f"STOCKDATA#{ticker}")
            & Key("SK").between(f"DATE#{_date_str(start_date)}", f"DATE#{_date_str(end_date)}")
        )
        return sorted((_strip_keys(row) for row in rows), key=lambda row: row["trading_date"])

    def put_market_signal(self, signal: dict[str, Any]) -> None:
        ticker = str(signal["ticker"]).upper()
        signal_date = _date_str(signal["signal_date"])
        signal_type = str(signal["signal_type"])
        created_at = signal.get("created_at", _now())
        item = {
            "PK": f"MARKETSIGNAL#{ticker}",
            "SK": f"DATE#{signal_date}#{signal_type}",
            "GSI1PK": "MARKET_SIGNAL",
            "GSI1SK": f"{signal_date}#{ticker}#{signal_type}",
            "entity": "market_signal",
            "ticker": ticker,
            "signal_date": signal_date,
            "signal_type": signal_type,
            "direction": signal["direction"],
            "score": int(signal["score"]),
            "title": signal["title"],
            "summary": signal["summary"],
            "source": _to_jsonable(signal.get("source", {})),
            "created_at": created_at,
        }
        optional_fields = (
            "price_change_percent",
            "volume_ratio",
            "close_price",
            "previous_close_price",
            "volume",
            "average_volume",
        )
        for field in optional_fields:
            value = signal.get(field)
            if value is None:
                continue
            if field in {
                "price_change_percent",
                "volume_ratio",
                "close_price",
                "previous_close_price",
                "average_volume",
            }:
                item[field] = _decimal(value)
            else:
                item[field] = int(value)
        self.table.put_item(Item=item)

    def market_signals_for_ticker(
        self, ticker: str, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        rows = self._query(
            KeyConditionExpression=Key("PK").eq(f"MARKETSIGNAL#{ticker}")
            & Key("SK").between(
                f"DATE#{_date_str(start_date)}#",
                f"DATE#{_date_str(end_date)}#~",
            )
        )
        return sorted(
            (_strip_keys(row) for row in rows),
            key=lambda row: (row["signal_date"], row["signal_type"]),
        )

    def latest_prices(self) -> dict[str, Decimal]:
        rows = self.list_stocks(is_active=True)
        return {
            row["ticker"]: _decimal(row["latest_close_price"])
            for row in rows
            if row.get("latest_close_price") is not None
        }

    def put_earnings_event(self, event: dict[str, Any]) -> None:
        ticker = str(event["ticker"]).upper()
        event_date = _date_str(event["event_date"])
        collected_at = event.get("collected_at", _now())
        item = {
            "PK": f"EARNINGS#{ticker}",
            "SK": f"DATE#{event_date}",
            "GSI1PK": "EARNINGS",
            "GSI1SK": f"{event_date}#{ticker}",
            "entity": "earnings_event",
            "ticker": ticker,
            "event_date": event_date,
            "is_upcoming": bool(event.get("is_upcoming", False)),
            "collected_at": collected_at,
            "provider": event.get("provider", "yfinance"),
        }
        optional_fields = (
            "company_name",
            "eps_estimate",
            "reported_eps",
            "surprise_percent",
            "time_of_day",
            "price_before",
            "price_after",
            "post_earnings_price_move_percent",
            "source_url",
        )
        for field in optional_fields:
            value = event.get(field)
            if value is None:
                continue
            if field in {
                "eps_estimate",
                "reported_eps",
                "surprise_percent",
                "price_before",
                "price_after",
                "post_earnings_price_move_percent",
            }:
                item[field] = _decimal(value)
            else:
                item[field] = value
        self.table.put_item(Item=item)
        self._put_system_status("EARNINGS_COLLECTION", collected_at)

    def earnings_events_for_ticker(
        self, ticker: str, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        rows = self._query(
            KeyConditionExpression=Key("PK").eq(f"EARNINGS#{ticker}")
            & Key("SK").between(
                f"DATE#{_date_str(start_date)}",
                f"DATE#{_date_str(end_date)}",
            )
        )
        return sorted((_strip_keys(row) for row in rows), key=lambda row: row["event_date"])

    def upcoming_earnings(self, start_date: date, end_date: date, limit: int = 25) -> list[dict[str, Any]]:
        rows = self._query(
            IndexName=GSI1,
            KeyConditionExpression=Key("GSI1PK").eq("EARNINGS")
            & Key("GSI1SK").between(
                f"{_date_str(start_date)}#",
                f"{_date_str(end_date)}#~",
            ),
        )
        upcoming = [
            _strip_keys(row)
            for row in rows
            if bool(row.get("is_upcoming", False))
        ]
        return sorted(upcoming, key=lambda row: (row["event_date"], row["ticker"]))[:limit]

    def put_dividend_event(self, event: dict[str, Any]) -> None:
        ticker = str(event["ticker"]).upper()
        ex_dividend_date = _date_str(event["ex_dividend_date"])
        collected_at = event.get("collected_at", _now())
        item = {
            "PK": f"DIVIDEND#{ticker}",
            "SK": f"DATE#{ex_dividend_date}",
            "GSI1PK": "DIVIDEND",
            "GSI1SK": f"{ex_dividend_date}#{ticker}",
            "entity": "dividend_event",
            "ticker": ticker,
            "ex_dividend_date": ex_dividend_date,
            "is_upcoming": bool(event.get("is_upcoming", False)),
            "collected_at": collected_at,
            "provider": event.get("provider", "yfinance"),
        }
        optional_fields = (
            "company_name",
            "pay_date",
            "dividend_amount",
            "dividend_yield",
            "price_before",
            "price_after",
            "post_ex_dividend_price_move_percent",
            "source_url",
        )
        for field in optional_fields:
            value = event.get(field)
            if value is None:
                continue
            if field in {
                "dividend_amount",
                "dividend_yield",
                "price_before",
                "price_after",
                "post_ex_dividend_price_move_percent",
            }:
                item[field] = _decimal(value)
            else:
                item[field] = value
        self.table.put_item(Item=item)
        self._put_system_status("DIVIDEND_COLLECTION", collected_at)

    def dividend_events_for_ticker(
        self, ticker: str, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        rows = self._query(
            KeyConditionExpression=Key("PK").eq(f"DIVIDEND#{ticker}")
            & Key("SK").between(
                f"DATE#{_date_str(start_date)}",
                f"DATE#{_date_str(end_date)}",
            )
        )
        return sorted((_strip_keys(row) for row in rows), key=lambda row: row["ex_dividend_date"])

    def upcoming_dividends(self, start_date: date, end_date: date, limit: int = 25) -> list[dict[str, Any]]:
        rows = self._query(
            IndexName=GSI1,
            KeyConditionExpression=Key("GSI1PK").eq("DIVIDEND")
            & Key("GSI1SK").between(
                f"{_date_str(start_date)}#",
                f"{_date_str(end_date)}#~",
            ),
        )
        upcoming = [
            _strip_keys(row)
            for row in rows
            if bool(row.get("is_upcoming", False))
        ]
        return sorted(upcoming, key=lambda row: (row["ex_dividend_date"], row["ticker"]))[:limit]

    def last_stock_collection(self) -> str | None:
        return self._get_system_status_timestamp("STOCK_COLLECTION")

    def last_stock_collection_summary(self) -> dict[str, Any] | None:
        return self._get_system_status_summary("STOCK_COLLECTION")

    def existing_news_hashes(self, hashes: Iterable[str]) -> set[str]:
        result: set[str] = set()
        keys = [{"PK": f"NEWS#{hash_value}", "SK": "META"} for hash_value in hashes]
        for i in range(0, len(keys), 100):
            response = self.table.meta.client.batch_get_item(
                RequestItems={TABLE_NAME: {"Keys": keys[i : i + 100]}}
            )
            result.update(
                item["title_source_hash"]
                for item in response.get("Responses", {}).get(TABLE_NAME, [])
            )
        return result

    def put_news_summary(
        self, article: dict[str, Any], summary_data: dict[str, Any], title_source_hash: str
    ) -> bool:
        published_at = str(article["published_at"])
        item = {
            "PK": f"NEWS#{title_source_hash}",
            "SK": "META",
            "GSI1PK": "NEWS",
            "GSI1SK": published_at,
            "entity": "news",
            "title": article["title"][:500],
            "source": article["source"][:100],
            "published_at": published_at,
            "tickers": summary_data.get("tickers", []),
            "summary": summary_data["summary"][:500],
            "sentiment": summary_data.get("sentiment", "neutral"),
            "is_classified": bool(summary_data.get("tickers")),
            "collected_at": _now(),
            "title_source_hash": title_source_hash,
        }
        try:
            self.table.put_item(
                Item=item,
                ConditionExpression=Attr("PK").not_exists() & Attr("SK").not_exists(),
            )
            self._put_news_ticker_items(item)
            self._put_system_status("NEWS_COLLECTION", item["collected_at"])
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def news_for_ticker(self, ticker: str, start_date: date, end_date: date) -> list[dict[str, Any]]:
        rows = self._query(
            KeyConditionExpression=Key("PK").eq(f"NEWS_TICKER#{ticker}")
            & Key("SK").between(
                f"PUBLISHED#{_date_str(start_date)}",
                f"PUBLISHED#{_date_str(end_date)}~",
            ),
            ScanIndexForward=False,
        )
        return [_strip_keys(row) for row in rows[:20]]

    def last_news_collection(self) -> str | None:
        return self._get_system_status_timestamp("NEWS_COLLECTION")

    def last_news_collection_summary(self) -> dict[str, Any] | None:
        return self._get_system_status_summary("NEWS_COLLECTION")

    def put_config_list(self, name: str, values: list[str]) -> None:
        self.table.put_item(
            Item={
                "PK": f"CONFIG#{name}",
                "SK": "VALUE",
                "entity": "config",
                "name": name,
                "values": values,
                "updated_at": _now(),
            }
        )

    def get_config_list(self, name: str) -> list[str]:
        row = self.table.get_item(Key={"PK": f"CONFIG#{name}", "SK": "VALUE"}).get("Item")
        if not row:
            return []
        return [str(value).upper() for value in row.get("values", [])]

    def put_candidate_score(self, score: dict[str, Any]) -> None:
        score_date = _date_str(score["score_date"])
        item = {
            "PK": f"CANDIDATE#{score['ticker']}",
            "SK": f"DATE#{score_date}",
            "GSI1PK": "CANDIDATE",
            "GSI1SK": score_date,
            "entity": "candidate_score",
            "ticker": score["ticker"],
            "score_date": score_date,
            "opportunity_score": int(score["opportunity_score"]),
            "negative_score": int(score["negative_score"]),
            "signals": _to_dynamodb_value(score.get("signals", [])),
            "created_at": score.get("created_at", _now()),
        }
        self.table.put_item(Item=item)

    def candidate_scores_for_date(self, score_date: date) -> list[dict[str, Any]]:
        date_key = _date_str(score_date)
        rows = self._query(
            IndexName=GSI1,
            KeyConditionExpression=Key("GSI1PK").eq("CANDIDATE")
            & Key("GSI1SK").eq(date_key),
        )
        return [_strip_keys(row) for row in rows]

    def put_candidate_analysis(self, analysis: dict[str, Any]) -> None:
        analysis_date = _date_str(analysis["analysis_date"])
        item = {
            "PK": f"ANALYSIS#{analysis['ticker']}",
            "SK": f"DATE#{analysis_date}",
            "GSI1PK": "ANALYSIS",
            "GSI1SK": analysis_date,
            "entity": "candidate_analysis",
            "created_at": analysis.get("created_at", _now()),
            **_to_dynamodb_value(analysis),
            "analysis_date": analysis_date,
        }
        self.table.put_item(Item=item)
        self._put_system_status("ANALYSIS", item["created_at"])

    def candidate_analysis_for_date(self, analysis_date: date) -> list[dict[str, Any]]:
        date_key = _date_str(analysis_date)
        rows = self._query(
            IndexName=GSI1,
            KeyConditionExpression=Key("GSI1PK").eq("ANALYSIS")
            & Key("GSI1SK").eq(date_key),
        )
        return [_strip_keys(row) for row in rows]

    def put_publication_record(self, publication_date: date, payload: dict[str, Any]) -> None:
        date_key = _date_str(publication_date)
        data_quality = payload.get("data_quality") or {}
        self.table.put_item(
            Item={
                "PK": f"PUBLICATION#{date_key}",
                "SK": "TOP_PICKS",
                "GSI1PK": "PUBLICATION",
                "GSI1SK": date_key,
                "entity": "publication",
                "publication_date": date_key,
                "generated_at": payload["generated_at"],
                "top_pick_count": len(payload.get("top_picks", [])),
                "sell_alert_count": len(payload.get("sell_alerts", [])),
                "candidate_count": int(payload.get("candidate_count", 0)),
                "analyzed_count": int(payload.get("analyzed_count", 0)),
                "coverage_status": data_quality.get("coverage_status"),
                "eligible_ticker_count": int(data_quality.get("eligible_ticker_count", 0)),
                "excluded_ticker_count": int(data_quality.get("excluded_ticker_count", 0)),
                "news_stale": bool(data_quality.get("news_stale", False)),
            }
        )
        self._put_system_status("PUBLICATION", payload["generated_at"])

    def last_analysis(self) -> str | None:
        return self._get_system_status_timestamp("ANALYSIS")

    def last_publication(self) -> str | None:
        return self._get_system_status_timestamp("PUBLICATION")

    def put_collection_summary(self, component: str, summary: dict[str, Any]) -> None:
        timestamp = _now()
        self.table.put_item(
            Item={
                "PK": f"COLLECTIONSUMMARY#{component}",
                "SK": f"RUN#{timestamp}",
                "GSI1PK": "COLLECTION_SUMMARY",
                "GSI1SK": f"{timestamp}#{component}",
                "entity": "collection_summary",
                "component": component,
                "run_at": timestamp,
                "summary": _to_dynamodb_value(summary),
            }
        )
        self._put_system_status(component, timestamp, summary=summary)

    def _put_news_ticker_items(self, item: dict[str, Any]) -> None:
        tickers = [
            str(ticker).upper().strip()
            for ticker in item.get("tickers", [])
            if str(ticker).strip()
        ]
        if not tickers:
            return
        with self.table.batch_writer() as batch:
            for ticker in sorted(set(tickers)):
                batch.put_item(
                    Item={
                        **item,
                        "PK": f"NEWS_TICKER#{ticker}",
                        "SK": f"PUBLISHED#{item['published_at']}#{item['title_source_hash']}",
                        "entity": "news_ticker",
                        "ticker": ticker,
                    }
                )

    def _put_system_status(
        self,
        component: str,
        timestamp: str,
        summary: dict[str, Any] | None = None,
    ) -> None:
        item = {
            "PK": "SYSTEM#STATUS",
            "SK": component,
            "entity": "system_status",
            "component": component,
            "last_success_at": timestamp,
            "updated_at": _now(),
        }
        if summary is not None:
            item["last_summary"] = _to_dynamodb_value(summary)
        self.table.put_item(Item=item)

    def _get_system_status_timestamp(self, component: str) -> str | None:
        row = self.table.get_item(
            Key={"PK": "SYSTEM#STATUS", "SK": component}
        ).get("Item")
        if not row:
            return None
        value = row.get("last_success_at")
        return str(value) if value else None

    def _get_system_status_summary(self, component: str) -> dict[str, Any] | None:
        row = self.table.get_item(
            Key={"PK": "SYSTEM#STATUS", "SK": component}
        ).get("Item")
        if not row:
            return None
        summary = row.get("last_summary")
        return _to_jsonable(summary) if isinstance(summary, dict) else None


store = DynamoStore()
