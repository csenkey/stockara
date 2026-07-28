"""News collection Lambda handler.

Polls NewsAPI and Finnhub for stock-related news articles, deduplicates them,
generates AI summaries via OpenAI, and stores results in the database.

Triggered by EventBridge a few times per day (configurable).
"""

import hashlib
import os
import re
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
from src.services.static_artifacts import safe_publish_json_artifact

logger = structlog.get_logger(__name__)

# Configuration from environment variables
POLL_INTERVAL_MINUTES = int(os.environ.get("NEWS_POLL_INTERVAL_MINUTES", "15"))
OPENAI_NEWS_MODEL = os.environ.get("OPENAI_NEWS_MODEL", "gpt-5.4-mini")
COLLECTION_MANIFEST_BUCKET = os.environ.get(
    "COLLECTION_MANIFEST_BUCKET",
    os.environ.get("STOCKARA_ARTIFACT_BUCKET", ""),
)
ARTIFACT_BUCKET = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "")
FINNHUB_TICKER_NEWS_MAX_TICKERS = int(
    os.environ.get("FINNHUB_TICKER_NEWS_MAX_TICKERS", "10")
)
ALPHA_VANTAGE_NEWS_MAX_TICKERS = int(
    os.environ.get("ALPHA_VANTAGE_NEWS_MAX_TICKERS", "25")
)
NEWS_ARTIFACT_LOOKBACK_DAYS = int(os.environ.get("NEWS_ARTIFACT_LOOKBACK_DAYS", "30"))
NEWS_ARTIFACT_MAX_TICKERS = int(os.environ.get("NEWS_ARTIFACT_MAX_TICKERS", "250"))
NEWS_ARTIFACT_MAX_ARTICLES_PER_TICKER = int(
    os.environ.get("NEWS_ARTIFACT_MAX_ARTICLES_PER_TICKER", "10")
)
COMMON_WORD_SHORT_TICKERS = {
    "A",
    "AI",
    "ALL",
    "ARE",
    "AS",
    "AT",
    "BE",
    "BY",
    "CAN",
    "FOR",
    "GO",
    "HAS",
    "HE",
    "I",
    "IN",
    "IT",
    "NEW",
    "NO",
    "NOW",
    "ON",
    "OR",
    "OUT",
    "SEE",
    "SO",
    "TO",
    "TV",
    "US",
    "USA",
    "WE",
}

# CloudWatch metrics client
cloudwatch = boto3.client("cloudwatch")


@dataclass
class ManifestTaskRun:
    bucket: str
    key: str
    task_id: str


@dataclass
class NewsSourceResult:
    source: str
    articles: list[dict[str, Any]]
    status: str = "success"
    reason: str | None = None

    @property
    def is_available(self) -> bool:
        return self.status in {"success", "partial"}


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


def _alpha_vantage_key() -> str | None:
    return get_provider_api_key(
        "alpha_vantage",
        "ALPHA_VANTAGE_API_KEY",
        "ALPHA_VANTAGE_API_KEY_SECRET_NAME",
        supported_json_keys=("ALPHA_VANTAGE_API_KEY", "alpha_vantage_api_key", "api_key"),
    )


def _safe_provider_error(exc: Exception | str) -> str:
    text = str(exc)
    return re.sub(
        r"([?&](?:apiKey|apikey|api_key|token)=)[^&\s]+",
        r"\1<redacted>",
        text,
        flags=re.IGNORECASE,
    )


def fetch_newsapi_source(tickers: list[str] | None = None) -> NewsSourceResult:
    """Fetch recent stock-related articles from NewsAPI.

    Returns:
        Source status and article dicts with keys: title, source, published_at, url.
    """
    import requests

    api_key = _newsapi_key()
    if not api_key:
        logger.error("NewsAPI fetch skipped", error="NEWSAPI key is not configured")
        return NewsSourceResult("newsapi", [], "skipped", "api_key_not_configured")

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
                "provider": "newsapi",
            })

        logger.info("NewsAPI articles fetched", count=len(articles), tickers=tickers or [])
        return NewsSourceResult("newsapi", articles)

    except Exception as e:
        error = _safe_provider_error(e)
        logger.error("NewsAPI fetch failed", error=error)
        return NewsSourceResult("newsapi", [], "failed", error)


def fetch_newsapi_articles(tickers: list[str] | None = None) -> list[dict[str, Any]]:
    return fetch_newsapi_source(tickers=tickers).articles


def fetch_finnhub_source(tickers: list[str] | None = None) -> NewsSourceResult:
    """Fetch recent market news from Finnhub.

    Returns:
        Source status and article dicts with keys: title, source, published_at, content.
    """
    import requests

    api_key = _finnhub_key()
    if not api_key:
        logger.error("Finnhub fetch skipped", error="Finnhub key is not configured")
        return NewsSourceResult("finnhub", [], "skipped", "api_key_not_configured")

    if tickers:
        return _fetch_finnhub_company_news_source(tickers, api_key)

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
                "provider": "finnhub",
            })

        logger.info("Finnhub articles fetched", count=len(articles))
        return NewsSourceResult("finnhub", articles)

    except Exception as e:
        error = _safe_provider_error(e)
        logger.error("Finnhub fetch failed", error=error)
        return NewsSourceResult("finnhub", [], "failed", error)


def fetch_finnhub_articles(tickers: list[str] | None = None) -> list[dict[str, Any]]:
    return fetch_finnhub_source(tickers=tickers).articles


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
    return _fetch_finnhub_company_news_source(tickers, api_key).articles


def _fetch_finnhub_company_news_source(
    tickers: list[str],
    api_key: str,
) -> NewsSourceResult:
    import requests

    articles: list[dict[str, Any]] = []
    attempted = 0
    failures = 0
    to_date = date.today()
    from_date = to_date - timedelta(days=7)
    for ticker in tickers[:FINNHUB_TICKER_NEWS_MAX_TICKERS]:
        attempted += 1
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
                        "provider": "finnhub",
                        "provider_tickers": [str(ticker).upper()],
                    }
                )
        except Exception as e:
            failures += 1
            error = _safe_provider_error(e)
            logger.error(
                "Finnhub company news fetch failed",
                ticker=ticker,
                error=error,
            )
            continue
    logger.info("Finnhub company news fetched", count=len(articles), tickers=tickers)
    if attempted and failures == attempted:
        return NewsSourceResult("finnhub", articles, "failed", "all_ticker_requests_failed")
    if failures:
        return NewsSourceResult("finnhub", articles, "partial", f"{failures}_ticker_requests_failed")
    return NewsSourceResult("finnhub", articles)


def fetch_alpha_vantage_source(tickers: list[str] | None = None) -> NewsSourceResult:
    """Fetch ticker-aware news from Alpha Vantage NEWS_SENTIMENT."""
    import requests

    api_key = _alpha_vantage_key()
    if not api_key:
        logger.error(
            "Alpha Vantage news fetch skipped",
            error="Alpha Vantage key is not configured",
        )
        return NewsSourceResult("alpha_vantage", [], "skipped", "api_key_not_configured")

    params = {
        "function": "NEWS_SENTIMENT",
        "apikey": api_key,
        "limit": "50",
    }
    if tickers:
        params["tickers"] = ",".join(tickers[:ALPHA_VANTAGE_NEWS_MAX_TICKERS])
    else:
        params["topics"] = "earnings,financial_markets"

    try:
        response = requests.get(
            "https://www.alphavantage.co/query",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        error = _safe_provider_error(exc)
        logger.error("Alpha Vantage news fetch failed", error=error)
        return NewsSourceResult("alpha_vantage", [], "failed", error)

    provider_note = payload.get("Note") or payload.get("Information") or payload.get("Error Message")
    if provider_note:
        error = _safe_provider_error(provider_note)
        logger.error("Alpha Vantage news fetch failed", error=error)
        return NewsSourceResult("alpha_vantage", [], "failed", error)

    articles = []
    for article in payload.get("feed", []):
        title = str(article.get("title") or "").strip()
        source = str(article.get("source") or "Alpha Vantage").strip()
        published_at = _alpha_vantage_time_published(article.get("time_published"))
        if not title or not source or not published_at:
            continue
        articles.append(
            {
                "title": title,
                "source": source,
                "published_at": published_at,
                "content": article.get("summary", "") or "",
                "provider": "alpha_vantage",
                "provider_tickers": _alpha_vantage_article_tickers(article, tickers),
                "provider_sentiment": _alpha_vantage_sentiment(article),
                "url": article.get("url"),
            }
        )

    logger.info("Alpha Vantage news fetched", count=len(articles), tickers=tickers or [])
    return NewsSourceResult("alpha_vantage", articles)


def fetch_alpha_vantage_articles(tickers: list[str] | None = None) -> list[dict[str, Any]]:
    return fetch_alpha_vantage_source(tickers=tickers).articles


def _alpha_vantage_time_published(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc).isoformat()


def _alpha_vantage_article_tickers(
    article: dict[str, Any],
    requested_tickers: list[str] | None,
) -> list[str]:
    requested = {ticker.upper() for ticker in requested_tickers or []}
    result = []
    for row in article.get("ticker_sentiment", []) or []:
        ticker = str(row.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        if requested and ticker not in requested:
            continue
        result.append(ticker)
    return sorted(set(result))


def _alpha_vantage_sentiment(article: dict[str, Any]) -> str:
    label = str(article.get("overall_sentiment_label") or "").lower()
    if "bullish" in label:
        return "positive"
    if "bearish" in label:
        return "negative"
    return "neutral"


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


def generate_summary(
    client: OpenAI | None,
    title: str,
    content: str,
    active_tickers: set[str] | None = None,
) -> dict[str, Any]:
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
        classification = _classify_tickers(
            result.get("tickers", []),
            title,
            content,
            active_tickers=active_tickers,
            provider_tickers=[],
        )

        sentiment = str(result.get("sentiment", "neutral")).lower()
        if sentiment not in {"positive", "negative", "neutral"}:
            sentiment = "neutral"

        return {
            "summary": summary,
            "tickers": classification["tickers"],
            "sentiment": sentiment,
            "ticker_classifications": classification["ticker_classifications"],
            "classification_confidence": classification["classification_confidence"],
        }

    except Exception as e:
        logger.error("OpenAI summary generation failed", error=str(e), title=title)
        active_tickers = active_tickers if active_tickers is not None else _active_ticker_universe()
        classification = _classify_tickers(
            _fallback_tickers_from_text(title, content, active_tickers),
            title,
            content,
            active_tickers=active_tickers,
            provider_tickers=[],
        )
        return {
            "summary": title[:500],
            "tickers": classification["tickers"],
            "sentiment": "neutral",
            "ticker_classifications": classification["ticker_classifications"],
            "classification_confidence": classification["classification_confidence"],
        }


def merge_provider_classification(
    article: dict[str, Any],
    summary_data: dict[str, Any],
    active_tickers: set[str] | None = None,
) -> dict[str, Any]:
    """Preserve provider-supplied ticker/sentiment attribution."""
    provider_tickers = {
        str(ticker).strip().upper()
        for ticker in article.get("provider_tickers", [])
        if str(ticker).strip()
    }
    classification = _classify_tickers(
        summary_data.get("tickers", []),
        str(article.get("title", "")),
        str(article.get("content", "")),
        active_tickers=active_tickers,
        provider_tickers=provider_tickers,
    )
    sentiment = str(summary_data.get("sentiment") or "neutral").lower()
    provider_sentiment = str(article.get("provider_sentiment") or "").lower()
    if sentiment == "neutral" and provider_sentiment in {"positive", "negative"}:
        sentiment = provider_sentiment
    if sentiment not in {"positive", "negative", "neutral"}:
        sentiment = "neutral"
    return {
        **summary_data,
        "tickers": classification["tickers"],
        "sentiment": sentiment,
        "ticker_classifications": classification["ticker_classifications"],
        "classification_confidence": classification["classification_confidence"],
    }


def _active_ticker_universe() -> set[str]:
    try:
        return {str(ticker).strip().upper() for ticker in store.active_tickers()}
    except Exception as exc:
        logger.warning("news_active_ticker_universe_failed", error=str(exc))
        return set()


def _classify_tickers(
    ai_tickers: list[Any],
    title: str,
    content: str,
    active_tickers: set[str] | None,
    provider_tickers: set[str],
) -> dict[str, Any]:
    filter_to_active_universe = active_tickers is not None
    active_tickers = active_tickers or set()
    text = f"{title} {content}"
    rows: dict[str, dict[str, Any]] = {}
    for ticker in provider_tickers:
        normalized = _normalize_ticker(ticker)
        if not normalized:
            continue
        if filter_to_active_universe and normalized not in active_tickers:
            continue
        rows[normalized] = {
            "ticker": normalized,
            "confidence": 1.0,
            "sources": ["provider"],
            "matched_by": "provider_ticker",
        }

    for ticker in ai_tickers:
        normalized = _normalize_ticker(ticker)
        if not normalized:
            continue
        if filter_to_active_universe and normalized not in active_tickers:
            continue
        if _is_ambiguous_short_ticker(normalized) and normalized not in provider_tickers:
            if not _has_strong_short_ticker_context(text, normalized):
                continue
            confidence = 0.45
        else:
            confidence = 0.75 if _has_word_boundary_ticker(text, normalized) else 0.6
        existing = rows.get(normalized)
        if existing:
            existing["confidence"] = max(existing["confidence"], confidence)
            if "ai" not in existing["sources"]:
                existing["sources"].append("ai")
        else:
            rows[normalized] = {
                "ticker": normalized,
                "confidence": confidence,
                "sources": ["ai"],
                "matched_by": (
                    "ai_and_text_boundary"
                    if _has_word_boundary_ticker(text, normalized)
                    else "ai_model"
                ),
            }

    classifications = [rows[ticker] for ticker in sorted(rows)]
    confidence = (
        round(sum(row["confidence"] for row in classifications) / len(classifications), 2)
        if classifications
        else 0.0
    )
    return {
        "tickers": [row["ticker"] for row in classifications],
        "ticker_classifications": classifications,
        "classification_confidence": confidence,
    }


def _normalize_ticker(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    ticker = value.strip().upper()
    if not ticker or len(ticker) > 10:
        return None
    if not re.fullmatch(r"[A-Z][A-Z0-9.:-]{0,9}", ticker):
        return None
    return ticker


def _is_ambiguous_short_ticker(ticker: str) -> bool:
    return ticker in COMMON_WORD_SHORT_TICKERS or (len(ticker) <= 2 and ticker.isalpha())


def _has_word_boundary_ticker(text: str, ticker: str) -> bool:
    return bool(re.search(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", text.upper()))


def _has_strong_short_ticker_context(text: str, ticker: str) -> bool:
    upper = text.upper()
    patterns = [
        rf"\${re.escape(ticker)}(?![A-Z0-9])",
        rf"\b(?:NYSE|NASDAQ|AMEX):\s*{re.escape(ticker)}\b",
    ]
    if any(re.search(pattern, upper) for pattern in patterns):
        return True
    if ticker == "ON":
        return bool(re.search(r"\bON\s+Semiconductor\b", text))
    return False


def _fallback_tickers_from_text(
    title: str, content: str, active_tickers: set[str]
) -> list[str]:
    text = f"{title} {content}"
    return [
        ticker
        for ticker in sorted(active_tickers)
        if not _is_ambiguous_short_ticker(ticker) and _has_word_boundary_ticker(text, ticker)
    ]


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
    skipped_sources: list[str] | None = None,
    zero_article_sources: list[str] | None = None,
    source_statuses: list[dict[str, Any]] | None = None,
    article_failures: int = 0,
) -> dict[str, Any]:
    sources_total = 3
    skipped_sources = skipped_sources or []
    configured_total = sources_total - len(skipped_sources)
    failed_sources = failed_sources or []
    return {
        "status": status,
        "sources_total": sources_total,
        "sources_configured": configured_total,
        "sources_available": sources_available,
        "sources_failed": len(failed_sources),
        "sources_skipped": len(skipped_sources),
        "failed_sources": failed_sources,
        "skipped_sources": skipped_sources,
        "zero_article_sources": zero_article_sources or [],
        "source_statuses": source_statuses or [],
        "articles_fetched": total_fetched,
        "articles_processed": articles_processed,
        "duplicates_skipped": duplicates_skipped,
        "article_failures": article_failures,
        "completeness_ratio": (
            round(sources_available / configured_total, 4) if configured_total else 1.0
        ),
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

    # Fetch from all sources and track source health separately from article count.
    source_results = [
        fetch_newsapi_source(tickers=tickers),
        fetch_finnhub_source(tickers=tickers),
        fetch_alpha_vantage_source(tickers=tickers),
    ]
    sources_available = sum(1 for result in source_results if result.is_available)
    failed_sources = [
        result.source for result in source_results if result.status == "failed"
    ]
    skipped_sources = [
        result.source for result in source_results if result.status == "skipped"
    ]
    zero_article_sources = [
        result.source
        for result in source_results
        if result.is_available and not result.articles
    ]
    source_statuses = [
        {
            "source": result.source,
            "status": result.status,
            "articles_fetched": len(result.articles),
            **({"reason": result.reason} if result.reason else {}),
        }
        for result in source_results
    ]
    configured_sources = len(source_results) - len(skipped_sources)

    # Check if all sources failed
    if sources_available == 0:
        if configured_sources:
            raise_all_sources_failed_alert()
        emit_metrics(articles_processed=0, sources_available=0, sources_total=3)
        summary = build_news_collection_summary(
            status="failed" if configured_sources else "skipped",
            articles_processed=0,
            sources_available=0,
            total_fetched=0,
            failed_sources=failed_sources,
            skipped_sources=skipped_sources,
            zero_article_sources=zero_article_sources,
            source_statuses=source_statuses,
        )
        record_news_collection_summary(summary)
        emit_news_collection_summary_metrics(summary)
        result = {
            "status": "error" if configured_sources else "skipped",
            "message": (
                "All configured news sources unavailable"
                if configured_sources
                else "No news sources are configured"
            ),
            "articles_processed": 0,
            "sources_available": 0,
            "failed_sources": failed_sources,
            "skipped_sources": skipped_sources,
            "tickers": tickers or [],
            "collection_summary": summary,
        }
        _publish_news_dashboard_artifact(ARTIFACT_BUCKET, result)
        return result

    # Combine all articles
    all_articles = [
        article for result in source_results for article in result.articles
    ]

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
        active_tickers = _active_ticker_universe()
        articles_stored = 0
        article_failures = 0

        for article in new_articles:
            try:
                summary_data = generate_summary(
                    openai_client,
                    article["title"],
                    article.get("content", ""),
                    active_tickers=active_tickers,
                )
                summary_data = merge_provider_classification(
                    article,
                    summary_data,
                    active_tickers=active_tickers,
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
        sources_total=3,
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
        skipped_sources=skipped_sources,
        zero_article_sources=zero_article_sources,
        source_statuses=source_statuses,
        article_failures=article_failures,
    )
    record_news_collection_summary(summary)
    emit_news_collection_summary_metrics(summary)

    result = {
        "status": status,
        "articles_processed": articles_stored,
        "sources_available": sources_available,
        "failed_sources": failed_sources,
        "skipped_sources": skipped_sources,
        "zero_article_sources": zero_article_sources,
        "total_fetched": len(all_articles),
        "duplicates_skipped": len(all_articles) - len(new_articles),
        "tickers": tickers or [],
        "collection_summary": summary,
    }
    _publish_news_dashboard_artifact(ARTIFACT_BUCKET, result)
    return result


def _publish_news_dashboard_artifact(
    bucket: str,
    result: dict[str, Any],
) -> None:
    if not bucket:
        return
    generated_at = datetime.now(timezone.utc)
    end_date = generated_at.date()
    start_date = end_date - timedelta(days=NEWS_ARTIFACT_LOOKBACK_DAYS)
    by_ticker = _recent_news_by_ticker(start_date, end_date)
    summary = result.get("collection_summary") or {}
    total_recent_articles = sum(item["article_count"] for item in by_ticker)
    payload = {
        "generated_at": generated_at.isoformat(),
        "lookback_days": NEWS_ARTIFACT_LOOKBACK_DAYS,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "last_run": {
            "status": result.get("status"),
            "articles_fetched": summary.get(
                "articles_fetched", result.get("total_fetched", 0)
            ),
            "articles_processed": result.get(
                "articles_processed", summary.get("articles_processed", 0)
            ),
            "duplicates_skipped": result.get(
                "duplicates_skipped", summary.get("duplicates_skipped", 0)
            ),
            "sources_available": result.get(
                "sources_available", summary.get("sources_available", 0)
            ),
            "sources_total": summary.get("sources_total", 2),
            "sources_configured": summary.get("sources_configured", 0),
            "sources_skipped": summary.get("sources_skipped", 0),
            "failed_sources": summary.get("failed_sources", []),
            "skipped_sources": summary.get("skipped_sources", []),
            "zero_article_sources": summary.get("zero_article_sources", []),
            "source_statuses": summary.get("source_statuses", []),
            "tickers": result.get("tickers", []),
        },
        "recent_article_count": total_recent_articles,
        "ticker_count_with_news": len(by_ticker),
        "by_ticker": by_ticker,
    }
    safe_publish_json_artifact(bucket, "news/latest.json", payload)


def _recent_news_by_ticker(start_date: date, end_date: date) -> list[dict[str, Any]]:
    try:
        stocks = store.active_stock_metadata()
    except Exception as exc:
        logger.warning("news_dashboard_active_tickers_failed", error=str(exc))
        return []

    rows: list[dict[str, Any]] = []
    for stock in stocks[:NEWS_ARTIFACT_MAX_TICKERS]:
        ticker = str(stock.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        try:
            articles = store.news_for_ticker(ticker, start_date, end_date)
        except Exception as exc:
            logger.warning("news_dashboard_ticker_query_failed", ticker=ticker, error=str(exc))
            continue
        if not articles:
            continue
        rows.append(
            {
                "ticker": ticker,
                "company_name": stock.get("name") or stock.get("company_name") or ticker,
                "article_count": len(articles),
                "articles": [
                    _news_article_row(article)
                    for article in articles[:NEWS_ARTIFACT_MAX_ARTICLES_PER_TICKER]
                ],
            }
        )
    return sorted(rows, key=lambda item: item["article_count"], reverse=True)


def _news_article_row(article: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": article.get("title", ""),
        "source": article.get("source", ""),
        "published_at": article.get("published_at"),
        "summary": article.get("summary", ""),
        "sentiment": article.get("sentiment", "neutral"),
        "url": article.get("url"),
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
