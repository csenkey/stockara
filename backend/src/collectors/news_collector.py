"""News collection Lambda handler.

Polls NewsAPI and Finnhub for stock-related news articles, deduplicates them,
generates AI summaries via OpenAI, and stores results in the database.

Triggered by EventBridge every 15 minutes (configurable).
"""

import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import boto3
import structlog
from openai import OpenAI

from src.db.connection import DatabasePool, store
from src.models.schemas import (
    CollectionOutputCounts,
    CollectionTaskType,
)
from src.services.collection_manifest import (
    complete_task,
    find_task,
    load_manifest,
    mark_task_running,
    write_manifest,
)
from src.services.secrets import get_openai_api_key, get_provider_api_key

logger = structlog.get_logger(__name__)

# Configuration from environment variables
POLL_INTERVAL_MINUTES = int(os.environ.get("NEWS_POLL_INTERVAL_MINUTES", "15"))
OPENAI_NEWS_MODEL = os.environ.get("OPENAI_NEWS_MODEL", "gpt-5.4-mini")
COLLECTION_MANIFEST_BUCKET = os.environ.get(
    "COLLECTION_MANIFEST_BUCKET",
    os.environ.get("STOCKARA_ARTIFACT_BUCKET", ""),
)
FINNHUB_TICKER_NEWS_MAX_TICKERS = int(
    os.environ.get("FINNHUB_TICKER_NEWS_MAX_TICKERS", "10")
)

# CloudWatch metrics client
cloudwatch = boto3.client("cloudwatch")


@dataclass
class ManifestTaskRun:
    bucket: str
    key: str
    task_id: str


def compute_title_source_hash(title: str, source: str) -> str:
    """Compute SHA-256 hash of title + source for deduplication.

    Args:
        title: Article title.
        source: Article source name.

    Returns:
        Hex-encoded SHA-256 hash string.
    """
    content = f"{title}{source}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _newsapi_key() -> str | None:
    return get_provider_api_key(
        "newsapi",
        "NEWSAPI_KEY",
        "NEWSAPI_KEY_SECRET_NAME",
        supported_json_keys=("NEWSAPI_KEY", "newsapi_key", "api_key"),
    )


def _finnhub_key() -> str | None:
    return get_provider_api_key(
        "finnhub",
        "FINNHUB_KEY",
        "FINNHUB_KEY_SECRET_NAME",
        supported_json_keys=("FINNHUB_KEY", "finnhub_key", "api_key"),
    )


def fetch_newsapi_articles(tickers: list[str] | None = None) -> list[dict[str, Any]]:
    """Fetch recent stock-related articles from NewsAPI.

    Returns:
        List of article dicts with keys: title, source, published_at, url.
        Returns empty list if the source is unavailable.
    """
    import requests

    api_key = _newsapi_key()
    if not api_key:
        logger.error("NewsAPI fetch skipped", error="NEWSAPI key is not configured")
        return []

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": _newsapi_query(tickers),
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 50,
        "apiKey": api_key,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        articles = []
        for article in data.get("articles", []):
            title = article.get("title", "").strip()
            source = article.get("source", {}).get("name", "").strip()
            published_at = article.get("publishedAt")

            if not title or not source or not published_at:
                continue

            articles.append({
                "title": title,
                "source": source,
                "published_at": published_at,
                "content": article.get("description", "") or article.get("content", "") or "",
            })

        logger.info("NewsAPI articles fetched", count=len(articles), tickers=tickers or [])
        return articles

    except Exception as e:
        logger.error("NewsAPI fetch failed", error=str(e))
        return []


def fetch_finnhub_articles(tickers: list[str] | None = None) -> list[dict[str, Any]]:
    """Fetch recent market news from Finnhub.

    Returns:
        List of article dicts with keys: title, source, published_at, content.
        Returns empty list if the source is unavailable.
    """
    import requests

    api_key = _finnhub_key()
    if not api_key:
        logger.error("Finnhub fetch skipped", error="Finnhub key is not configured")
        return []

    if tickers:
        return _fetch_finnhub_company_news(tickers, api_key)

    url = "https://finnhub.io/api/v1/news"
    params = {"category": "general", "token": api_key}

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        articles = []
        for article in data:
            title = article.get("headline", "").strip()
            source = article.get("source", "").strip()
            published_at_ts = article.get("datetime")

            if not title or not source or not published_at_ts:
                continue

            # Finnhub uses Unix timestamps
            published_at = datetime.fromtimestamp(
                published_at_ts, tz=timezone.utc
            ).isoformat()

            articles.append({
                "title": title,
                "source": source,
                "published_at": published_at,
                "content": article.get("summary", "") or "",
            })

        logger.info("Finnhub articles fetched", count=len(articles))
        return articles

    except Exception as e:
        logger.error("Finnhub fetch failed", error=str(e))
        return []


def _newsapi_query(tickers: list[str] | None = None) -> str:
    base = "stocks OR stock market OR earnings OR NYSE OR NASDAQ"
    if not tickers:
        return base
    ticker_query = " OR ".join(tickers[:50])
    return f"({base}) OR ({ticker_query})"


def _fetch_finnhub_company_news(
    tickers: list[str],
    api_key: str,
) -> list[dict[str, Any]]:
    import requests

    articles: list[dict[str, Any]] = []
    to_date = date.today()
    from_date = to_date - timedelta(days=7)
    for ticker in tickers[:FINNHUB_TICKER_NEWS_MAX_TICKERS]:
        url = "https://finnhub.io/api/v1/company-news"
        params = {
            "symbol": ticker,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "token": api_key,
        }
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            for article in response.json():
                title = article.get("headline", "").strip()
                source = article.get("source", "").strip()
                published_at_ts = article.get("datetime")
                if not title or not source or not published_at_ts:
                    continue
                published_at = datetime.fromtimestamp(
                    published_at_ts, tz=timezone.utc
                ).isoformat()
                articles.append(
                    {
                        "title": title,
                        "source": source,
                        "published_at": published_at,
                        "content": article.get("summary", "") or "",
                    }
                )
        except Exception as e:
            logger.error(
                "Finnhub company news fetch failed",
                ticker=ticker,
                error=str(e),
            )
            continue
    logger.info("Finnhub company news fetched", count=len(articles), tickers=tickers)
    return articles


def get_existing_hashes(conn, hashes: list[str]) -> set[str]:
    """Check which title_source_hash values already exist in the database.

    Args:
        conn: Database connection.
        hashes: List of hash strings to check.

    Returns:
        Set of hashes that already exist in the database.
    """
    if not hashes:
        return set()

    return store.existing_news_hashes(hashes)


def generate_summary(client: OpenAI | None, title: str, content: str) -> dict[str, Any]:
    """Generate a structured summary using OpenAI.

    Args:
        client: OpenAI client instance.
        title: Article title.
        content: Article content/description.

    Returns:
        Dict with keys: summary (str, ≤500 chars), tickers (list[str]).
    """
    prompt = (
        "You are a financial news analyst. Analyze the following news article and provide:\n"
        "1. A concise summary (maximum 500 characters) capturing the key financial information.\n"
        "2. A list of stock ticker symbols mentioned or clearly related to this article "
        "(use standard US exchange tickers like AAPL, MSFT, etc.).\n\n"
        "3. Sentiment direction for the likely stock impact: positive, negative, or neutral.\n\n"
        f"Title: {title}\n"
        f"Content: {content}\n\n"
        "Respond in JSON format:\n"
        '{"summary": "...", "tickers": ["TICKER1", "TICKER2"], "sentiment": "neutral"}\n'
        "If no specific tickers can be identified, return an empty tickers list."
    )

    try:
        if client is None:
            raise RuntimeError("OpenAI API key is not configured")

        response = client.chat.completions.create(
            model=OPENAI_NEWS_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            **_chat_completion_options(
                OPENAI_NEWS_MODEL,
                max_tokens=300,
                temperature=0.3,
            ),
        )

        import json
        result = json.loads(response.choices[0].message.content)

        summary = result.get("summary", "")[:500]
        tickers = result.get("tickers", [])

        # Validate tickers: uppercase, alphanumeric, max 10 chars
        valid_tickers = [
            t.upper().strip()
            for t in tickers
            if isinstance(t, str) and t.strip() and len(t.strip()) <= 10
        ]

        sentiment = str(result.get("sentiment", "neutral")).lower()
        if sentiment not in {"positive", "negative", "neutral"}:
            sentiment = "neutral"

        return {"summary": summary, "tickers": valid_tickers, "sentiment": sentiment}

    except Exception as e:
        logger.error("OpenAI summary generation failed", error=str(e), title=title)
        try:
            active_tickers = set(store.active_tickers())
        except Exception:
            active_tickers = set()
        text = f"{title} {content}".upper()
        tickers = [ticker for ticker in active_tickers if ticker in text]
        return {"summary": title[:500], "tickers": tickers, "sentiment": "neutral"}


def _chat_completion_options(
    model: str, max_tokens: int, temperature: float
) -> dict[str, Any]:
    if model.startswith("gpt-5"):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens, "temperature": temperature}


def store_article(conn, article: dict[str, Any], summary_data: dict[str, Any], title_source_hash: str) -> None:
    """Store a news article summary in the database.

    Args:
        conn: Database connection.
        article: Raw article data with title, source, published_at.
        summary_data: Generated summary with summary text and tickers.
        title_source_hash: Deduplication hash.
    """
    store.put_news_summary(article, summary_data, title_source_hash)


def emit_metrics(articles_processed: int, sources_available: int, sources_total: int) -> None:
    """Emit CloudWatch custom metrics for news collection.

    Args:
        articles_processed: Number of new articles stored.
        sources_available: Number of sources that responded successfully.
        sources_total: Total number of configured sources.
    """
    try:
        cloudwatch.put_metric_data(
            Namespace="StockMonitoring",
            MetricData=[
                {
                    "MetricName": "news_articles_processed",
                    "Value": articles_processed,
                    "Unit": "Count",
                },
                {
                    "MetricName": "news_sources_available",
                    "Value": sources_available,
                    "Unit": "Count",
                },
            ],
        )
    except Exception as e:
        logger.error("Failed to emit CloudWatch metrics", error=str(e))


def raise_all_sources_failed_alert() -> None:
    """Raise a CloudWatch alarm when all news sources are unavailable."""
    try:
        cloudwatch.put_metric_data(
            Namespace="StockMonitoring",
            MetricData=[
                {
                    "MetricName": "news_all_sources_failed",
                    "Value": 1,
                    "Unit": "Count",
                },
            ],
        )
        logger.critical("All news sources are unavailable - alert raised")
    except Exception as e:
        logger.error("Failed to raise all-sources-failed alert", error=str(e))


def build_news_collection_summary(
    status: str,
    articles_processed: int,
    sources_available: int,
    total_fetched: int,
    duplicates_skipped: int = 0,
    failed_sources: list[str] | None = None,
    article_failures: int = 0,
) -> dict[str, Any]:
    sources_total = 2
    return {
        "status": status,
        "sources_total": sources_total,
        "sources_available": sources_available,
        "sources_failed": sources_total - sources_available,
        "failed_sources": failed_sources or [],
        "articles_fetched": total_fetched,
        "articles_processed": articles_processed,
        "duplicates_skipped": duplicates_skipped,
        "article_failures": article_failures,
        "completeness_ratio": round(sources_available / sources_total, 4),
    }


def record_news_collection_summary(summary: dict[str, Any]) -> None:
    try:
        store.put_collection_summary("NEWS_COLLECTION", summary)
    except Exception as e:
        logger.warning("news_collection_summary_write_failed", error=str(e))


def emit_news_collection_summary_metrics(summary: dict[str, Any]) -> None:
    status = str(summary.get("status", "unknown"))
    try:
        cloudwatch.put_metric_data(
            Namespace="StockMonitoring",
            MetricData=[
                {
                    "MetricName": "news_collection_completeness_percent",
                    "Value": float(summary.get("completeness_ratio", 0)) * 100,
                    "Unit": "Percent",
                },
                {
                    "MetricName": "news_sources_failed",
                    "Value": int(summary.get("sources_failed", 0)),
                    "Unit": "Count",
                },
                {
                    "MetricName": "news_article_failures",
                    "Value": int(summary.get("article_failures", 0)),
                    "Unit": "Count",
                },
                {
                    "MetricName": "news_collection_partial_runs",
                    "Value": 1 if status == "partial" else 0,
                    "Unit": "Count",
                },
                {
                    "MetricName": "news_collection_failed_runs",
                    "Value": 1 if status == "failed" else 0,
                    "Unit": "Count",
                },
            ],
        )
    except Exception as e:
        logger.warning("news_collection_summary_metrics_failed", error=str(e))


def collect_news(tickers: list[str] | None = None) -> dict[str, Any]:
    """Main news collection logic.

    Polls all configured sources, deduplicates, summarizes, and stores articles.

    Returns:
        Dict with collection statistics.
    """
    logger.info(
        "Starting news collection",
        poll_interval_minutes=POLL_INTERVAL_MINUTES,
        tickers=tickers or [],
    )

    # Fetch from all sources
    newsapi_articles = fetch_newsapi_articles(tickers=tickers)
    finnhub_articles = fetch_finnhub_articles(tickers=tickers)

    # Track source availability
    sources_available = 0
    if newsapi_articles:
        sources_available += 1
    if finnhub_articles:
        sources_available += 1
    failed_sources = []
    if not newsapi_articles:
        failed_sources.append("newsapi")
    if not finnhub_articles:
        failed_sources.append("finnhub")

    # Check if all sources failed
    if sources_available == 0:
        raise_all_sources_failed_alert()
        emit_metrics(articles_processed=0, sources_available=0, sources_total=2)
        summary = build_news_collection_summary(
            status="failed",
            articles_processed=0,
            sources_available=0,
            total_fetched=0,
            failed_sources=failed_sources,
        )
        record_news_collection_summary(summary)
        emit_news_collection_summary_metrics(summary)
        return {
            "status": "error",
            "message": "All news sources unavailable",
            "articles_processed": 0,
            "sources_available": 0,
            "tickers": tickers or [],
            "collection_summary": summary,
        }

    # Combine all articles
    all_articles = newsapi_articles + finnhub_articles

    # Compute hashes for deduplication
    article_hashes = []
    for article in all_articles:
        h = compute_title_source_hash(article["title"], article["source"])
        article["_hash"] = h
        article_hashes.append(h)

    # Check existing articles in DB
    DatabasePool.initialize()
    conn = DatabasePool.table()
    try:
        existing_hashes = get_existing_hashes(conn, article_hashes)

        # Filter to new articles only
        new_articles = [a for a in all_articles if a["_hash"] not in existing_hashes]
        logger.info(
            "Deduplication complete",
            total_fetched=len(all_articles),
            already_exists=len(existing_hashes),
            new_articles=len(new_articles),
        )

        # Generate summaries and store. If no OpenAI key is configured, the
        # summary function falls back to title-based summaries and ticker matching.
        openai_api_key = get_openai_api_key()
        openai_client = OpenAI(api_key=openai_api_key) if openai_api_key else None
        articles_stored = 0
        article_failures = 0

        for article in new_articles:
            try:
                summary_data = generate_summary(
                    openai_client,
                    article["title"],
                    article.get("content", ""),
                )
                store_article(conn, article, summary_data, article["_hash"])
                articles_stored += 1
            except Exception as e:
                logger.error(
                    "Failed to process article",
                    error=str(e),
                    title=article["title"],
                )
                article_failures += 1
                continue

        logger.info("News collection complete", articles_stored=articles_stored)

    except Exception as e:
        logger.error("News collection failed", error=str(e))
        raise

    emit_metrics(
        articles_processed=articles_stored,
        sources_available=sources_available,
        sources_total=2,
    )

    status = "success"
    if failed_sources or article_failures:
        status = "partial"
    summary = build_news_collection_summary(
        status=status,
        articles_processed=articles_stored,
        sources_available=sources_available,
        total_fetched=len(all_articles),
        duplicates_skipped=len(all_articles) - len(new_articles),
        failed_sources=failed_sources,
        article_failures=article_failures,
    )
    record_news_collection_summary(summary)
    emit_news_collection_summary_metrics(summary)

    return {
        "status": "success",
        "articles_processed": articles_stored,
        "sources_available": sources_available,
        "total_fetched": len(all_articles),
        "duplicates_skipped": len(all_articles) - len(new_articles),
        "tickers": tickers or [],
        "collection_summary": summary,
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for news collection.

    Triggered by EventBridge on a schedule (default every 15 minutes).

    Args:
        event: EventBridge event payload.
        context: Lambda context object.

    Returns:
        Dict with execution results.
    """
    event = event or {}
    logger.info("News collector Lambda invoked", lambda_event=event)
    manifest_task_run: ManifestTaskRun | None = None

    try:
        manifest_task_run, tickers = _prepare_manifest_task_run(event, context)
        if tickers is None:
            tickers = _event_tickers(event)
        result = collect_news(tickers=tickers)
        if manifest_task_run:
            _complete_manifest_task_run(
                manifest_task_run,
                result,
                failed=str(result.get("status", "")).lower() != "success",
            )
        logger.info("News collector completed", result=result)
        return {
            "statusCode": 200,
            "body": result,
        }
    except Exception as e:
        logger.error("News collector Lambda failed", error=str(e))
        if manifest_task_run:
            _fail_manifest_task_run(manifest_task_run, str(e))
        return {
            "statusCode": 500,
            "body": {"status": "error", "message": str(e)},
        }
    finally:
        DatabasePool.close()


def _event_tickers(event: dict[str, Any]) -> list[str] | None:
    tickers = event.get("tickers")
    if not tickers:
        return None
    return [str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()]


def _prepare_manifest_task_run(
    event: dict[str, Any],
    context: Any,
) -> tuple[ManifestTaskRun | None, list[str] | None]:
    if event.get("mode") != "manifest_task":
        return None, None
    bucket = str(event.get("manifest_bucket") or COLLECTION_MANIFEST_BUCKET).strip()
    key = str(event.get("manifest_key") or "").strip()
    task_id = str(event.get("task_id") or "").strip()
    if not bucket or not key or not task_id:
        raise ValueError("manifest_bucket, manifest_key, and task_id are required")

    manifest = load_manifest(bucket, key)
    task = find_task(manifest, task_id)
    if task.task_type != CollectionTaskType.NEWS:
        raise ValueError(f"Task {task_id} is not a news collection task")
    lease_owner = getattr(context, "aws_request_id", None) if context else None
    mark_task_running(manifest, task_id, lease_owner=lease_owner)
    write_manifest(bucket, key, manifest)
    return ManifestTaskRun(bucket=bucket, key=key, task_id=task_id), task.tickers


def _complete_manifest_task_run(
    task_run: ManifestTaskRun,
    result: dict[str, Any],
    failed: bool = False,
) -> None:
    try:
        manifest = load_manifest(task_run.bucket, task_run.key)
        complete_task(
            manifest,
            task_run.task_id,
            _news_output_counts(result),
            failed=failed,
            failure_reason=None if not failed else str(result.get("status")),
        )
        write_manifest(task_run.bucket, task_run.key, manifest)
    except Exception as exc:
        logger.warning(
            "news_manifest_task_completion_failed",
            task_id=task_run.task_id,
            error=str(exc),
        )


def _fail_manifest_task_run(task_run: ManifestTaskRun, reason: str) -> None:
    try:
        manifest = load_manifest(task_run.bucket, task_run.key)
        complete_task(
            manifest,
            task_run.task_id,
            CollectionOutputCounts(),
            failed=True,
            failure_reason=reason,
        )
        write_manifest(task_run.bucket, task_run.key, manifest)
    except Exception as exc:
        logger.warning(
            "news_manifest_task_failure_write_failed",
            task_id=task_run.task_id,
            error=str(exc),
        )


def _news_output_counts(result: dict[str, Any]) -> CollectionOutputCounts:
    summary = result.get("collection_summary") or {}
    articles_fetched = int(
        result.get("total_fetched")
        or summary.get("articles_fetched")
        or 0
    )
    articles_processed = int(
        result.get("articles_processed")
        or summary.get("articles_processed")
        or 0
    )
    return CollectionOutputCounts(
        records_fetched=articles_fetched,
        records_written=articles_processed,
        duplicate_records=int(summary.get("duplicates_skipped", 0) or 0),
        failed_records=int(summary.get("article_failures", 0) or 0),
        successful_tickers=len(result.get("tickers") or []),
        failed_tickers=(
            0
            if str(result.get("status", "")).lower() == "success"
            else len(result.get("tickers") or [])
        ),
    )
