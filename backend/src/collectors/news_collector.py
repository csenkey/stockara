"""News collection Lambda handler.

Polls NewsAPI and Finnhub for stock-related news articles, deduplicates them,
generates AI summaries via OpenAI GPT-4o-mini, and stores results in the database.

Triggered by EventBridge every 15 minutes (configurable).
"""

import hashlib
import os
from datetime import datetime, timezone
from typing import Any

import boto3
import structlog
from openai import OpenAI

from backend.src.db.connection import DatabasePool, store

logger = structlog.get_logger(__name__)

# Configuration from environment variables
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
POLL_INTERVAL_MINUTES = int(os.environ.get("NEWS_POLL_INTERVAL_MINUTES", "15"))

# CloudWatch metrics client
cloudwatch = boto3.client("cloudwatch")


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


def fetch_newsapi_articles() -> list[dict[str, Any]]:
    """Fetch recent stock-related articles from NewsAPI.

    Returns:
        List of article dicts with keys: title, source, published_at, url.
        Returns empty list if the source is unavailable.
    """
    import requests

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": "stocks OR stock market OR earnings OR NYSE OR NASDAQ",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 50,
        "apiKey": NEWSAPI_KEY,
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

        logger.info("NewsAPI articles fetched", count=len(articles))
        return articles

    except Exception as e:
        logger.error("NewsAPI fetch failed", error=str(e))
        return []


def fetch_finnhub_articles() -> list[dict[str, Any]]:
    """Fetch recent market news from Finnhub.

    Returns:
        List of article dicts with keys: title, source, published_at, content.
        Returns empty list if the source is unavailable.
    """
    import requests

    url = "https://finnhub.io/api/v1/news"
    params = {
        "category": "general",
        "token": FINNHUB_KEY,
    }

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
    """Generate a structured summary using OpenAI GPT-4o-mini.

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
            raise RuntimeError("OPENAI_API_KEY is not configured")

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=300,
            temperature=0.3,
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


def collect_news() -> dict[str, Any]:
    """Main news collection logic.

    Polls all configured sources, deduplicates, summarizes, and stores articles.

    Returns:
        Dict with collection statistics.
    """
    logger.info("Starting news collection", poll_interval_minutes=POLL_INTERVAL_MINUTES)

    # Fetch from all sources
    newsapi_articles = fetch_newsapi_articles()
    finnhub_articles = fetch_finnhub_articles()

    # Track source availability
    sources_available = 0
    if newsapi_articles:
        sources_available += 1
    if finnhub_articles:
        sources_available += 1

    # Check if all sources failed
    if sources_available == 0:
        raise_all_sources_failed_alert()
        emit_metrics(articles_processed=0, sources_available=0, sources_total=2)
        return {
            "status": "error",
            "message": "All news sources unavailable",
            "articles_processed": 0,
            "sources_available": 0,
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
        openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
        articles_stored = 0

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

    return {
        "status": "success",
        "articles_processed": articles_stored,
        "sources_available": sources_available,
        "total_fetched": len(all_articles),
        "duplicates_skipped": len(all_articles) - len(new_articles),
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
    logger.info("News collector Lambda invoked", event=event)

    try:
        result = collect_news()
        logger.info("News collector completed", result=result)
        return {
            "statusCode": 200,
            "body": result,
        }
    except Exception as e:
        logger.error("News collector Lambda failed", error=str(e))
        return {
            "statusCode": 500,
            "body": {"status": "error", "message": str(e)},
        }
    finally:
        DatabasePool.close()
