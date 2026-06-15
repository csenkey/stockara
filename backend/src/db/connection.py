"""DynamoDB access helpers for Stockara Phase 1."""

import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, AsyncGenerator, Iterable

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
import structlog

logger = structlog.get_logger(__name__)

TABLE_NAME = os.environ.get("STOCKARA_TABLE_NAME", "stockara")
GSI1 = "GSI1"


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
        rows = self._scan(FilterExpression=Attr("entity").eq("stock"))
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
        try:
            self.table.put_item(
                Item=item,
                ConditionExpression=Attr("PK").not_exists() & Attr("SK").not_exists(),
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def get_stock_data(
        self, ticker: str, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        rows = self._query(
            KeyConditionExpression=Key("PK").eq(f"STOCKDATA#{ticker}")
            & Key("SK").between(f"DATE#{_date_str(start_date)}", f"DATE#{_date_str(end_date)}")
        )
        return sorted((_strip_keys(row) for row in rows), key=lambda row: row["trading_date"])

    def latest_prices(self) -> dict[str, Decimal]:
        rows = self._scan(FilterExpression=Attr("entity").eq("stock_data"))
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            ticker = row["ticker"]
            if ticker not in latest or row["trading_date"] > latest[ticker]["trading_date"]:
                latest[ticker] = row
        return {ticker: _decimal(row["close_price"]) for ticker, row in latest.items()}

    def last_stock_collection(self) -> str | None:
        rows = self._scan(FilterExpression=Attr("entity").eq("stock_data"))
        return max((row.get("collected_at") for row in rows if row.get("collected_at")), default=None)

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
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def news_for_ticker(self, ticker: str, start_date: date, end_date: date) -> list[dict[str, Any]]:
        rows = self._scan(
            FilterExpression=Attr("entity").eq("news") & Attr("tickers").contains(ticker)
        )
        start = _date_str(start_date)
        end = _date_str(end_date)
        filtered = [
            _strip_keys(row)
            for row in rows
            if start <= str(row.get("published_at", ""))[:10] <= end
        ]
        return sorted(filtered, key=lambda row: row["published_at"], reverse=True)[:20]

    def last_news_collection(self) -> str | None:
        rows = self._scan(FilterExpression=Attr("entity").eq("news"))
        return max((row.get("collected_at") for row in rows if row.get("collected_at")), default=None)

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
            "signals": _to_jsonable(score.get("signals", [])),
            "created_at": score.get("created_at", _now()),
        }
        self.table.put_item(Item=item)

    def candidate_scores_for_date(self, score_date: date) -> list[dict[str, Any]]:
        date_key = _date_str(score_date)
        rows = self._scan(
            FilterExpression=Attr("entity").eq("candidate_score") & Attr("score_date").eq(date_key)
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
            **_to_jsonable(analysis),
            "analysis_date": analysis_date,
        }
        self.table.put_item(Item=item)

    def candidate_analysis_for_date(self, analysis_date: date) -> list[dict[str, Any]]:
        date_key = _date_str(analysis_date)
        rows = self._scan(
            FilterExpression=Attr("entity").eq("candidate_analysis")
            & Attr("analysis_date").eq(date_key)
        )
        return [_strip_keys(row) for row in rows]

    def put_publication_record(self, publication_date: date, payload: dict[str, Any]) -> None:
        date_key = _date_str(publication_date)
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
            }
        )

    def last_analysis(self) -> str | None:
        rows = self._scan(FilterExpression=Attr("entity").eq("candidate_analysis"))
        return max((row.get("created_at") for row in rows if row.get("created_at")), default=None)

    def last_publication(self) -> str | None:
        rows = self._scan(FilterExpression=Attr("entity").eq("publication"))
        return max((row.get("generated_at") for row in rows if row.get("generated_at")), default=None)


store = DynamoStore()
