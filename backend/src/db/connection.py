"""DynamoDB data access helpers for Stockara.

The application uses a single-table design. Entity types are encoded in PK/SK
prefixes and the two GSIs support common type/date and name lookup patterns.
"""

import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, AsyncGenerator, Iterable
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
import structlog

logger = structlog.get_logger(__name__)

TABLE_NAME = os.environ.get("STOCKARA_TABLE_NAME", "stockara")
GSI1 = "GSI1"
GSI2 = "GSI2"


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


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _strip_keys(item: dict[str, Any]) -> dict[str, Any]:
    hidden = {"PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "entity"}
    return {k: v for k, v in item.items() if k not in hidden}


class DatabasePool:
    """Compatibility facade around the DynamoDB table resource."""

    _resource = None
    _table = None

    @classmethod
    def initialize(cls) -> None:
        if cls._table is None:
            cls._resource = boto3.resource("dynamodb")
            cls._table = cls._resource.Table(TABLE_NAME)
            logger.info("DynamoDB table initialized", table=TABLE_NAME)

    @classmethod
    def close(cls) -> None:
        """Kept for compatibility with the previous connection lifecycle."""
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
    """Compatibility context manager that yields the DynamoDB table."""
    yield DatabasePool.table()


async def run_migrations(migrations_dir: str | None = None) -> None:
    """DynamoDB schema is provisioned by CDK, so runtime migrations are unused."""
    logger.info("DynamoDB migrations skipped", table=TABLE_NAME)


class DynamoStore:
    """Repository helpers for the single DynamoDB table."""

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

    # Stocks
    def list_stocks(
        self,
        sector: str | None = None,
        company_size: str | None = None,
        is_active: bool | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._scan(FilterExpression=Attr("entity").eq("stock"))
        if sector is not None:
            rows = [r for r in rows if r.get("sector") == sector]
        if company_size is not None:
            rows = [r for r in rows if r.get("company_size") == company_size]
        if is_active is not None:
            rows = [r for r in rows if r.get("is_active") is is_active]
        return sorted((_strip_keys(r) for r in rows), key=lambda r: r["ticker"])

    def get_stock(self, ticker: str) -> dict[str, Any] | None:
        row = self.table.get_item(
            Key={"PK": f"STOCK#{ticker}", "SK": "META"}
        ).get("Item")
        return _strip_keys(row) if row else None

    def put_stock(self, stock: dict[str, Any], create_only: bool = False) -> dict[str, Any]:
        ticker = stock["ticker"]
        now = _now()
        existing = self.get_stock(ticker)
        item = {
            "PK": f"STOCK#{ticker}",
            "SK": "META",
            "GSI1PK": "STOCK",
            "GSI1SK": ticker,
            "entity": "stock",
            "ticker": ticker,
            "company_name": stock["company_name"],
            "sector": stock["sector"],
            "company_size": stock["company_size"],
            "added_at": existing.get("added_at") if existing else now,
            "is_active": stock.get("is_active", existing.get("is_active") if existing else True),
        }
        kwargs: dict[str, Any] = {"Item": item}
        if create_only:
            kwargs["ConditionExpression"] = Attr("PK").not_exists()
        self.table.put_item(**kwargs)
        return _strip_keys(item)

    def update_stock(self, ticker: str, updates: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_stock(ticker)
        if not existing:
            raise KeyError(ticker)
        existing.update({k: v for k, v in updates.items() if v is not None})
        return self.put_stock(existing)

    def delete_stock(self, ticker: str) -> None:
        self.table.delete_item(Key={"PK": f"STOCK#{ticker}", "SK": "META"})

    def active_tickers(self) -> list[str]:
        return [row["ticker"] for row in self.list_stocks(is_active=True)]

    def active_stock_metadata(self) -> list[dict[str, Any]]:
        return [
            {"ticker": r["ticker"], "sector": r["sector"], "company_size": r["company_size"]}
            for r in self.list_stocks(is_active=True)
        ]

    def put_stock_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        ticker = profile["ticker"]
        item = {
            "PK": f"STOCK#{ticker}",
            "SK": "PROFILE",
            "entity": "stock_profile",
            "ticker": ticker,
            "company_history": profile.get("company_history"),
            "business_description": profile.get("business_description"),
            "leading_products": profile.get("leading_products", []),
            "business_stats": profile.get("business_stats", {}),
            "updated_at": profile.get("updated_at", _now()),
        }
        self.table.put_item(Item=item)
        return _strip_keys(item)

    def get_stock_profile(self, ticker: str) -> dict[str, Any] | None:
        row = self.table.get_item(
            Key={"PK": f"STOCK#{ticker}", "SK": "PROFILE"}
        ).get("Item")
        return _strip_keys(row) if row else None

    # Stock data
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
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def get_stock_data(
        self, ticker: str, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        rows = self._query(
            KeyConditionExpression=Key("PK").eq(f"STOCKDATA#{ticker}")
            & Key("SK").between(f"DATE#{_date_str(start_date)}", f"DATE#{_date_str(end_date)}")
        )
        result = []
        for row in rows:
            clean = _strip_keys(row)
            clean["trading_date"] = _parse_date(clean["trading_date"])
            result.append(clean)
        return sorted(result, key=lambda r: r["trading_date"])

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
        return max((r.get("collected_at") for r in rows if r.get("collected_at")), default=None)

    # Dividends and earnings calls
    def put_dividend_event(self, event: dict[str, Any]) -> dict[str, Any]:
        ticker = event["ticker"]
        ex_dividend_date = _date_str(event["ex_dividend_date"])
        item = {
            "PK": f"DIVIDEND#{ticker}",
            "SK": f"DATE#{ex_dividend_date}",
            "GSI1PK": "DIVIDEND",
            "GSI1SK": f"{ex_dividend_date}#{ticker}",
            "entity": "dividend_event",
            "ticker": ticker,
            "ex_dividend_date": ex_dividend_date,
            "dividend_value": _decimal(event["dividend_value"]),
            "currency": event.get("currency", "USD"),
            "payment_date": _date_str(event["payment_date"])
            if event.get("payment_date")
            else None,
            "price_impact": event.get("price_impact"),
            "collected_at": event.get("collected_at", _now()),
        }
        self.table.put_item(Item=item)
        return _strip_keys(item)

    def list_dividend_events(
        self, ticker: str, start_date: date | None = None, end_date: date | None = None
    ) -> list[dict[str, Any]]:
        start = f"DATE#{_date_str(start_date)}" if start_date else "DATE#0000-00-00"
        end = f"DATE#{_date_str(end_date)}" if end_date else "DATE#9999-99-99"
        rows = self._query(
            KeyConditionExpression=Key("PK").eq(f"DIVIDEND#{ticker}")
            & Key("SK").between(start, end)
        )
        return sorted((_strip_keys(row) for row in rows), key=lambda r: r["ex_dividend_date"])

    def put_earnings_call_summary(self, summary: dict[str, Any]) -> dict[str, Any]:
        ticker = summary["ticker"]
        call_date = _date_str(summary["call_date"])
        item = {
            "PK": f"EARNINGS_CALL#{ticker}",
            "SK": f"DATE#{call_date}",
            "GSI1PK": "EARNINGS_CALL",
            "GSI1SK": f"{call_date}#{ticker}",
            "entity": "earnings_call_summary",
            "ticker": ticker,
            "call_date": call_date,
            "fiscal_period": summary["fiscal_period"],
            "summary": summary["summary"],
            "key_topics": summary.get("key_topics", []),
            "sentiment": summary.get("sentiment"),
            "price_impact": summary.get("price_impact"),
            "collected_at": summary.get("collected_at", _now()),
        }
        self.table.put_item(Item=item)
        return _strip_keys(item)

    def list_earnings_call_summaries(
        self, ticker: str, start_date: date | None = None, end_date: date | None = None
    ) -> list[dict[str, Any]]:
        start = f"DATE#{_date_str(start_date)}" if start_date else "DATE#0000-00-00"
        end = f"DATE#{_date_str(end_date)}" if end_date else "DATE#9999-99-99"
        rows = self._query(
            KeyConditionExpression=Key("PK").eq(f"EARNINGS_CALL#{ticker}")
            & Key("SK").between(start, end)
        )
        return sorted((_strip_keys(row) for row in rows), key=lambda r: r["call_date"])

    # News
    def existing_news_hashes(self, hashes: Iterable[str]) -> set[str]:
        result: set[str] = set()
        keys = [{"PK": f"NEWS#{h}", "SK": "META"} for h in hashes]
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
            "summary": summary_data["summary"],
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
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
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
        return sorted(filtered, key=lambda r: r["published_at"], reverse=True)[:20]

    def last_news_collection(self) -> str | None:
        rows = self._scan(FilterExpression=Attr("entity").eq("news"))
        return max((r.get("collected_at") for r in rows if r.get("collected_at")), default=None)

    # Analysis
    def put_analysis(self, result: dict[str, Any], analysis_date: date) -> None:
        ticker = result["ticker"]
        date_key = _date_str(analysis_date)
        item = {
            "PK": f"ANALYSIS#{ticker}",
            "SK": f"DATE#{date_key}",
            "GSI1PK": "ANALYSIS",
            "GSI1SK": date_key,
            "entity": "analysis",
            "ticker": ticker,
            "analysis_date": date_key,
            "short_term_recommendation": result["short_term_recommendation"],
            "long_term_recommendation": result["long_term_recommendation"],
            "risk_level": result["risk_level"],
            "confidence_score": int(result["confidence_score"]),
            "reasoning": result.get("reasoning", ""),
            "created_at": _now(),
        }
        self.table.put_item(Item=item)

    def latest_analysis_date(self) -> date | None:
        rows = self._scan(FilterExpression=Attr("entity").eq("analysis"))
        latest = max((r.get("analysis_date") for r in rows if r.get("analysis_date")), default=None)
        return _parse_date(latest) if latest else None

    def analysis_for_date(self, analysis_date: date) -> list[dict[str, Any]]:
        date_key = _date_str(analysis_date)
        rows = self._scan(
            FilterExpression=Attr("entity").eq("analysis") & Attr("analysis_date").eq(date_key)
        )
        stocks = {row["ticker"]: row for row in self.list_stocks(is_active=True)}
        result = []
        for row in rows:
            ticker = row["ticker"]
            if ticker not in stocks:
                continue
            clean = _strip_keys(row)
            clean.update(
                sector=stocks[ticker]["sector"],
                company_size=stocks[ticker]["company_size"],
                analysis_date=_parse_date(clean["analysis_date"]),
                created_at=_parse_datetime(clean["created_at"]),
            )
            result.append(clean)
        return result

    def latest_analysis_for_ticker(self, ticker: str) -> dict[str, Any] | None:
        rows = self._query(KeyConditionExpression=Key("PK").eq(f"ANALYSIS#{ticker}"))
        if not rows:
            return None
        row = max(rows, key=lambda r: r["analysis_date"])
        clean = _strip_keys(row)
        clean["analysis_date"] = _parse_date(clean["analysis_date"])
        clean["created_at"] = _parse_datetime(clean["created_at"])
        return clean

    def latest_recommendations(self) -> dict[str, str]:
        rows = self._scan(FilterExpression=Attr("entity").eq("analysis"))
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            ticker = row["ticker"]
            if ticker not in latest or row["analysis_date"] > latest[ticker]["analysis_date"]:
                latest[ticker] = row
        recommendations = {}
        for ticker, row in latest.items():
            short = row.get("short_term_recommendation", "HOLD")
            long = row.get("long_term_recommendation", "HOLD")
            recommendations[ticker] = "SELL" if "SELL" in (short, long) else "BUY" if "BUY" in (short, long) else "HOLD"
        return recommendations

    def last_analysis(self) -> str | None:
        rows = self._scan(FilterExpression=Attr("entity").eq("analysis"))
        return max((r.get("created_at") for r in rows if r.get("created_at")), default=None)

    # Sectors and trend/correlation data
    def put_sector(self, sector: dict[str, Any]) -> dict[str, Any]:
        name = sector["name"]
        item = {
            "PK": f"SECTOR#{name}",
            "SK": "META",
            "GSI1PK": "SECTOR",
            "GSI1SK": name,
            "entity": "sector",
            "name": name,
            "description": sector.get("description"),
            "benchmark_symbol": sector.get("benchmark_symbol"),
            "updated_at": sector.get("updated_at", _now()),
        }
        self.table.put_item(Item=item)
        return _strip_keys(item)

    def get_sector(self, name: str) -> dict[str, Any] | None:
        row = self.table.get_item(
            Key={"PK": f"SECTOR#{name}", "SK": "META"}
        ).get("Item")
        return _strip_keys(row) if row else None

    def put_sector_trend(self, trend: dict[str, Any]) -> dict[str, Any]:
        sector = trend["sector"]
        trend_date = _date_str(trend["trend_date"])
        item = {
            "PK": f"SECTOR#{sector}",
            "SK": f"TREND#DATE#{trend_date}",
            "GSI1PK": "SECTOR_TREND",
            "GSI1SK": f"{trend_date}#{sector}",
            "entity": "sector_trend",
            "sector": sector,
            "trend_date": trend_date,
            "benchmark_symbol": trend.get("benchmark_symbol"),
            "benchmark_close": _decimal(trend["benchmark_close"])
            if trend.get("benchmark_close") is not None
            else None,
            "percent_change": _decimal(trend["percent_change"])
            if trend.get("percent_change") is not None
            else None,
            "trend_score": _decimal(trend["trend_score"])
            if trend.get("trend_score") is not None
            else None,
            "collected_at": trend.get("collected_at", _now()),
        }
        self.table.put_item(Item=item)
        return _strip_keys(item)

    def list_sector_trends(
        self, sector: str, start_date: date | None = None, end_date: date | None = None
    ) -> list[dict[str, Any]]:
        start = f"TREND#DATE#{_date_str(start_date)}" if start_date else "TREND#DATE#0000-00-00"
        end = f"TREND#DATE#{_date_str(end_date)}" if end_date else "TREND#DATE#9999-99-99"
        rows = self._query(
            KeyConditionExpression=Key("PK").eq(f"SECTOR#{sector}")
            & Key("SK").between(start, end)
        )
        return sorted((_strip_keys(row) for row in rows), key=lambda r: r["trend_date"])

    def put_sector_ticker_correlation(self, correlation: dict[str, Any]) -> dict[str, Any]:
        sector = correlation["sector"]
        ticker = correlation["ticker"]
        calculation_date = _date_str(correlation["calculation_date"])
        window_days = int(correlation["window_days"])
        item = {
            "PK": f"SECTOR#{sector}",
            "SK": f"CORRELATION#TICKER#{ticker}#DATE#{calculation_date}#WINDOW#{window_days}",
            "GSI1PK": f"SECTOR_CORRELATION#{ticker}",
            "GSI1SK": f"{calculation_date}#{sector}#{window_days}",
            "entity": "sector_ticker_correlation",
            "sector": sector,
            "ticker": ticker,
            "calculation_date": calculation_date,
            "window_days": window_days,
            "correlation": _decimal(correlation["correlation"]),
            "sample_size": int(correlation["sample_size"]),
            "method": correlation.get("method", "pearson"),
        }
        self.table.put_item(Item=item)
        return _strip_keys(item)

    def list_sector_ticker_correlations(
        self, sector: str, ticker: str | None = None
    ) -> list[dict[str, Any]]:
        prefix = "CORRELATION#"
        if ticker:
            prefix = f"CORRELATION#TICKER#{ticker}#"
        rows = self._query(
            KeyConditionExpression=Key("PK").eq(f"SECTOR#{sector}")
            & Key("SK").begins_with(prefix)
        )
        return sorted(
            (_strip_keys(row) for row in rows),
            key=lambda r: (r["ticker"], r["calculation_date"], r["window_days"]),
        )

    # Users, portfolios, preferences
    def put_user(self, user_id: str, email: str) -> None:
        self.table.put_item(
            Item={
                "PK": f"USER#{user_id}",
                "SK": "PROFILE",
                "GSI1PK": "USER",
                "GSI1SK": email,
                "entity": "user",
                "id": user_id,
                "email": email,
                "created_at": _now(),
            }
        )

    def get_portfolio(self, user_id: str) -> dict[str, Any] | None:
        row = self.table.get_item(
            Key={"PK": f"USER#{user_id}", "SK": "PORTFOLIO"}
        ).get("Item")
        return _strip_keys(row) if row else None

    def put_portfolio(self, user_id: str, encrypted_data: str) -> dict[str, Any]:
        updated_at = _now()
        item = {
            "PK": f"USER#{user_id}",
            "SK": "PORTFOLIO",
            "entity": "portfolio",
            "user_id": user_id,
            "encrypted_data": encrypted_data,
            "updated_at": updated_at,
        }
        self.table.put_item(Item=item)
        return _strip_keys(item)

    def get_preferences(self, user_id: str) -> dict[str, Any] | None:
        row = self.table.get_item(
            Key={"PK": f"USER#{user_id}", "SK": "PREFERENCES"}
        ).get("Item")
        return _strip_keys(row) if row else None

    def put_preferences(
        self,
        user_id: str,
        preferred_sectors: list[str],
        preferred_sizes: list[str],
        max_risk_level: str,
    ) -> dict[str, Any]:
        item = {
            "PK": f"USER#{user_id}",
            "SK": "PREFERENCES",
            "entity": "preferences",
            "user_id": user_id,
            "preferred_sectors": preferred_sectors,
            "preferred_sizes": preferred_sizes,
            "max_risk_level": max_risk_level,
            "updated_at": _now(),
        }
        self.table.put_item(Item=item)
        return _strip_keys(item)

    def put_suggestion_history(
        self,
        user_id: str,
        suggestion_date: date,
        analysis_date: date,
        encrypted_data: str,
    ) -> dict[str, Any]:
        suggestion_date_key = _date_str(suggestion_date)
        item = {
            "PK": f"USER#{user_id}",
            "SK": f"SUGGESTIONS#DATE#{suggestion_date_key}",
            "GSI1PK": f"SUGGESTIONS#{suggestion_date_key}",
            "GSI1SK": user_id,
            "entity": "suggestion_history",
            "user_id": user_id,
            "suggestion_date": suggestion_date_key,
            "analysis_date": _date_str(analysis_date),
            "encrypted_data": encrypted_data,
            "created_at": _now(),
        }
        self.table.put_item(Item=item)
        return _strip_keys(item)

    def list_suggestion_history(
        self, user_id: str, start_date: date | None = None, end_date: date | None = None
    ) -> list[dict[str, Any]]:
        start = (
            f"SUGGESTIONS#DATE#{_date_str(start_date)}"
            if start_date
            else "SUGGESTIONS#DATE#0000-00-00"
        )
        end = (
            f"SUGGESTIONS#DATE#{_date_str(end_date)}"
            if end_date
            else "SUGGESTIONS#DATE#9999-99-99"
        )
        rows = self._query(
            KeyConditionExpression=Key("PK").eq(f"USER#{user_id}")
            & Key("SK").between(start, end)
        )
        return sorted((_strip_keys(row) for row in rows), key=lambda r: r["suggestion_date"])

    # Public top pick
    def put_top_pick(self, top_pick: dict[str, Any]) -> dict[str, Any]:
        pick_date = _date_str(top_pick["pick_date"])
        item = {
            "PK": "TOP_PICK",
            "SK": f"DATE#{pick_date}",
            "GSI1PK": "TOP_PICK",
            "GSI1SK": pick_date,
            "entity": "top_pick",
            "pick_date": pick_date,
            "ticker": top_pick["ticker"],
            "company_name": top_pick.get("company_name"),
            "reasoning": top_pick["reasoning"],
            "analysis_date": _date_str(top_pick["analysis_date"])
            if top_pick.get("analysis_date")
            else None,
            "generated_at": top_pick.get("generated_at", _now()),
        }
        self.table.put_item(Item=item)
        return _strip_keys(item)

    def top_pick_for_date(self, pick_date: date) -> dict[str, Any] | None:
        row = self.table.get_item(
            Key={"PK": "TOP_PICK", "SK": f"DATE#{_date_str(pick_date)}"}
        ).get("Item")
        return _strip_keys(row) if row else None

    def latest_top_pick(self) -> dict[str, Any] | None:
        rows = self._query(
            IndexName=GSI1,
            KeyConditionExpression=Key("GSI1PK").eq("TOP_PICK"),
            ScanIndexForward=False,
        )
        return _strip_keys(rows[0]) if rows else None

    # Demo accounts
    def create_demo_account(self, name: str, cash_balance: Decimal) -> dict[str, Any]:
        account_id = str(uuid4())
        item = {
            "PK": f"DEMO_ACCOUNT#{account_id}",
            "SK": "META",
            "GSI1PK": "DEMO_ACCOUNT",
            "GSI1SK": name,
            "GSI2PK": f"DEMO_ACCOUNT_NAME#{name}",
            "GSI2SK": "META",
            "entity": "demo_account",
            "id": account_id,
            "account_name": name,
            "cash_balance": _decimal(cash_balance),
            "created_at": _now(),
        }
        self.table.put_item(Item=item, ConditionExpression=Attr("PK").not_exists())
        return _strip_keys(item)

    def get_demo_account_by_name(self, name: str) -> dict[str, Any] | None:
        rows = self._query(
            IndexName=GSI2,
            KeyConditionExpression=Key("GSI2PK").eq(f"DEMO_ACCOUNT_NAME#{name}"),
        )
        return _strip_keys(rows[0]) if rows else None

    def list_demo_accounts(self) -> list[dict[str, Any]]:
        rows = self._scan(FilterExpression=Attr("entity").eq("demo_account"))
        return sorted((_strip_keys(row) for row in rows), key=lambda r: r["created_at"])

    def update_demo_cash(self, account_id: str, cash_balance: Decimal) -> None:
        self.table.update_item(
            Key={"PK": f"DEMO_ACCOUNT#{account_id}", "SK": "META"},
            UpdateExpression="SET cash_balance = :cash",
            ExpressionAttributeValues={":cash": _decimal(cash_balance)},
        )

    def list_demo_holdings(self, account_id: str) -> list[dict[str, Any]]:
        rows = self._query(KeyConditionExpression=Key("PK").eq(f"DEMO_HOLDING#{account_id}"))
        return sorted((_strip_keys(row) for row in rows), key=lambda r: r["ticker"])

    def upsert_demo_holding(
        self, account_id: str, ticker: str, quantity: int, purchase_price: Decimal
    ) -> None:
        existing = self.table.get_item(
            Key={"PK": f"DEMO_HOLDING#{account_id}", "SK": f"TICKER#{ticker}"}
        ).get("Item")
        new_quantity = int(quantity) + int(existing["quantity"]) if existing else int(quantity)
        self.table.put_item(
            Item={
                "PK": f"DEMO_HOLDING#{account_id}",
                "SK": f"TICKER#{ticker}",
                "entity": "demo_holding",
                "account_id": account_id,
                "ticker": ticker,
                "quantity": new_quantity,
                "purchase_price": _decimal(purchase_price),
                "purchased_at": existing.get("purchased_at") if existing else _now(),
            }
        )

    def delete_demo_holding(self, account_id: str, ticker: str) -> None:
        self.table.delete_item(
            Key={"PK": f"DEMO_HOLDING#{account_id}", "SK": f"TICKER#{ticker}"}
        )

    def put_demo_transaction(self, account_id: str, txn: dict[str, Any]) -> None:
        executed_at = txn.get("executed_at", _now())
        item = {
            "PK": f"DEMO_TXN#{account_id}",
            "SK": f"TS#{executed_at}#{uuid4()}",
            "entity": "demo_transaction",
            "id": str(uuid4()),
            "account_id": account_id,
            "ticker": txn["ticker"],
            "action": txn["action"],
            "quantity": int(txn["quantity"]),
            "price_per_share": _decimal(txn["price_per_share"]),
            "total_value": _decimal(txn["total_value"]),
            "commission_fee": _decimal(txn["commission_fee"]),
            "cash_after": _decimal(txn["cash_after"]),
            "executed_at": executed_at,
        }
        self.table.put_item(Item=item)

    def list_demo_transactions(self, account_id: str) -> list[dict[str, Any]]:
        rows = self._query(KeyConditionExpression=Key("PK").eq(f"DEMO_TXN#{account_id}"))
        result = []
        for row in rows:
            clean = _strip_keys(row)
            clean["executed_at"] = _parse_datetime(clean["executed_at"])
            result.append(clean)
        return sorted(result, key=lambda r: r["executed_at"], reverse=True)

    def put_demo_snapshot(
        self,
        account_id: str,
        snapshot_date: date,
        portfolio_value: Decimal,
        cash_balance: Decimal,
        holdings_value: Decimal,
    ) -> None:
        date_key = _date_str(snapshot_date)
        self.table.put_item(
            Item={
                "PK": f"DEMO_SNAPSHOT#{account_id}",
                "SK": f"DATE#{date_key}",
                "entity": "demo_snapshot",
                "account_id": account_id,
                "snapshot_date": date_key,
                "portfolio_value": _decimal(portfolio_value),
                "cash_balance": _decimal(cash_balance),
                "holdings_value": _decimal(holdings_value),
                "created_at": _now(),
            }
        )

    def list_demo_snapshots(self, account_id: str) -> list[dict[str, Any]]:
        rows = self._query(KeyConditionExpression=Key("PK").eq(f"DEMO_SNAPSHOT#{account_id}"))
        result = []
        for row in rows:
            clean = _strip_keys(row)
            clean["snapshot_date"] = _parse_date(clean["snapshot_date"])
            result.append(clean)
        return sorted(result, key=lambda r: r["snapshot_date"])


store = DynamoStore()
