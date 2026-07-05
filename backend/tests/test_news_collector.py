"""Unit tests for the news collector Lambda handler.

Tests cover deduplication logic, summary generation, error handling,
and mocked external API responses (NewsAPI, Finnhub, OpenAI).

Validates: Requirements 2.4, 2.5, 2.6, 2.7
"""

import hashlib
import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.src.collectors.news_collector import (
    build_news_collection_summary,
    compute_title_source_hash,
    emit_news_collection_summary_metrics,
    fetch_alpha_vantage_articles,
    fetch_newsapi_articles,
    fetch_finnhub_articles,
    get_existing_hashes,
    generate_summary,
    merge_provider_classification,
    store_article,
    collect_news,
    handler,
)
from src.collectors.collection_distributor import build_manifest
from src.models.schemas import CollectionTaskStatus, CollectionTaskType
from backend.src.services.secrets import get_provider_api_key


def setup_function():
    get_provider_api_key.cache_clear()


def teardown_function():
    get_provider_api_key.cache_clear()


# --- Tests for compute_title_source_hash (Requirement 2.5) ---


class TestComputeTitleSourceHash:
    """Tests for SHA-256 deduplication hash computation."""

    def test_basic_hash(self):
        """Test that hash is computed from title + source concatenation."""
        title = "Apple Reports Record Earnings"
        source = "Reuters"
        expected = hashlib.sha256(f"{title}{source}".encode("utf-8")).hexdigest()
        assert compute_title_source_hash(title, source) == expected

    def test_same_inputs_same_hash(self):
        """Test deterministic output for identical inputs."""
        h1 = compute_title_source_hash("Title", "Source")
        h2 = compute_title_source_hash("Title", "Source")
        assert h1 == h2

    def test_different_inputs_different_hash(self):
        """Test different inputs produce different hashes."""
        h1 = compute_title_source_hash("Title A", "Source A")
        h2 = compute_title_source_hash("Title B", "Source B")
        assert h1 != h2

    def test_order_matters(self):
        """Test that title and source order matters in hash."""
        h1 = compute_title_source_hash("Reuters", "Apple Reports")
        h2 = compute_title_source_hash("Apple Reports", "Reuters")
        assert h1 != h2

    def test_empty_strings(self):
        """Test hash with empty strings."""
        result = compute_title_source_hash("", "")
        expected = hashlib.sha256("".encode("utf-8")).hexdigest()
        assert result == expected

    def test_returns_hex_string(self):
        """Test that result is a valid hex string of expected length."""
        result = compute_title_source_hash("Test", "Source")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)


# --- Tests for fetch_newsapi_articles (Requirement 2.4) ---


class TestFetchNewsapiArticles:
    """Tests for NewsAPI article fetching with mocked HTTP responses."""

    @pytest.fixture(autouse=True)
    def _newsapi_key(self, monkeypatch):
        monkeypatch.setenv("NEWSAPI_KEY", "test-newsapi-key")
        get_provider_api_key.cache_clear()

    @patch("requests.get")
    def test_successful_fetch(self, mock_get):
        """Test parsing a valid NewsAPI response."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "articles": [
                {
                    "title": "Stock Market Rally",
                    "source": {"name": "CNBC"},
                    "publishedAt": "2025-01-15T10:00:00Z",
                    "description": "Markets rallied today.",
                },
                {
                    "title": "Tech Earnings Beat",
                    "source": {"name": "Bloomberg"},
                    "publishedAt": "2025-01-15T11:00:00Z",
                    "description": "Tech companies beat estimates.",
                },
            ]
        }
        mock_get.return_value = mock_response

        articles = fetch_newsapi_articles()
        assert len(articles) == 2
        assert articles[0]["title"] == "Stock Market Rally"
        assert articles[0]["source"] == "CNBC"
        assert articles[0]["published_at"] == "2025-01-15T10:00:00Z"
        assert articles[0]["content"] == "Markets rallied today."

    @patch("requests.get")
    def test_skips_articles_missing_title(self, mock_get):
        """Test articles without title are skipped."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "articles": [
                {
                    "title": "",
                    "source": {"name": "CNBC"},
                    "publishedAt": "2025-01-15T10:00:00Z",
                    "description": "No title article.",
                },
                {
                    "title": "Valid Article",
                    "source": {"name": "Reuters"},
                    "publishedAt": "2025-01-15T11:00:00Z",
                    "description": "Has a title.",
                },
            ]
        }
        mock_get.return_value = mock_response

        articles = fetch_newsapi_articles()
        assert len(articles) == 1
        assert articles[0]["title"] == "Valid Article"

    @patch("requests.get")
    def test_skips_articles_missing_source(self, mock_get):
        """Test articles without source name are skipped."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "articles": [
                {
                    "title": "No Source",
                    "source": {"name": ""},
                    "publishedAt": "2025-01-15T10:00:00Z",
                    "description": "Missing source.",
                },
            ]
        }
        mock_get.return_value = mock_response

        articles = fetch_newsapi_articles()
        assert len(articles) == 0

    @patch("requests.get")
    def test_skips_articles_missing_published_at(self, mock_get):
        """Test articles without publishedAt are skipped."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "articles": [
                {
                    "title": "No Date",
                    "source": {"name": "CNBC"},
                    "publishedAt": None,
                    "description": "Missing date.",
                },
            ]
        }
        mock_get.return_value = mock_response

        articles = fetch_newsapi_articles()
        assert len(articles) == 0

    @patch("requests.get")
    def test_returns_empty_on_http_error(self, mock_get):
        """Requirement 2.4: Returns empty list if source unavailable."""
        mock_get.side_effect = Exception("Connection timeout")

        articles = fetch_newsapi_articles()
        assert articles == []

    @patch("requests.get")
    def test_fallback_content_from_description_or_content(self, mock_get):
        """Test content falls back to empty string when both are None."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "articles": [
                {
                    "title": "Article",
                    "source": {"name": "Source"},
                    "publishedAt": "2025-01-15T10:00:00Z",
                    "description": None,
                    "content": None,
                },
            ]
        }
        mock_get.return_value = mock_response

        articles = fetch_newsapi_articles()
        assert len(articles) == 1
        assert articles[0]["content"] == ""

    @patch("requests.get")
    def test_ticker_query_includes_chunk_tickers(self, mock_get):
        """Manifest chunks add ticker symbols to the NewsAPI query."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"articles": []}
        mock_get.return_value = mock_response

        articles = fetch_newsapi_articles(tickers=["AAPL", "MSFT"])

        assert articles == []
        query = mock_get.call_args.kwargs["params"]["q"]
        assert "AAPL" in query
        assert "MSFT" in query


# --- Tests for fetch_finnhub_articles (Requirement 2.4) ---


class TestFetchFinnhubArticles:
    """Tests for Finnhub article fetching with mocked HTTP responses."""

    @pytest.fixture(autouse=True)
    def _finnhub_key(self, monkeypatch):
        monkeypatch.setenv("FINNHUB_KEY", "test-finnhub-key")
        get_provider_api_key.cache_clear()

    @patch("requests.get")
    def test_successful_fetch(self, mock_get):
        """Test parsing a valid Finnhub response with Unix timestamps."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = [
            {
                "headline": "Fed Raises Rates",
                "source": "MarketWatch",
                "datetime": 1736935200,  # 2025-01-15T10:00:00Z
                "summary": "The Federal Reserve raised interest rates.",
            },
        ]
        mock_get.return_value = mock_response

        articles = fetch_finnhub_articles()
        assert len(articles) == 1
        assert articles[0]["title"] == "Fed Raises Rates"
        assert articles[0]["source"] == "MarketWatch"
        assert articles[0]["content"] == "The Federal Reserve raised interest rates."
        # Check published_at is ISO format
        assert "2025-01-15" in articles[0]["published_at"]

    @patch("requests.get")
    def test_skips_articles_missing_headline(self, mock_get):
        """Test articles without headline are skipped."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = [
            {
                "headline": "",
                "source": "MarketWatch",
                "datetime": 1736935200,
                "summary": "Missing headline.",
            },
        ]
        mock_get.return_value = mock_response

        articles = fetch_finnhub_articles()
        assert len(articles) == 0

    @patch("requests.get")
    def test_skips_articles_missing_datetime(self, mock_get):
        """Test articles without datetime are skipped."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = [
            {
                "headline": "Article",
                "source": "Source",
                "datetime": None,
                "summary": "No datetime.",
            },
        ]
        mock_get.return_value = mock_response

        articles = fetch_finnhub_articles()
        assert len(articles) == 0

    @patch("requests.get")
    def test_returns_empty_on_http_error(self, mock_get):
        """Requirement 2.4: Returns empty list if source unavailable."""
        mock_get.side_effect = Exception("API rate limit exceeded")

        articles = fetch_finnhub_articles()
        assert articles == []

    @patch("requests.get")
    def test_ticker_fetch_uses_company_news_endpoint(self, mock_get):
        """Ticker chunks use Finnhub company-news instead of general news."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = [
            {
                "headline": "Apple supplier update",
                "source": "Reuters",
                "datetime": 1736935200,
                "summary": "Apple supplier news.",
            },
        ]
        mock_get.return_value = mock_response

        articles = fetch_finnhub_articles(tickers=["AAPL"])

        assert len(articles) == 1
        assert "company-news" in mock_get.call_args.args[0]
        assert mock_get.call_args.kwargs["params"]["symbol"] == "AAPL"


class TestFetchAlphaVantageArticles:
    """Tests for ticker-aware Alpha Vantage NEWS_SENTIMENT fetching."""

    @pytest.fixture(autouse=True)
    def _alpha_vantage_key(self, monkeypatch):
        monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-alpha-key")
        get_provider_api_key.cache_clear()

    @patch("requests.get")
    def test_successful_ticker_fetch_preserves_provider_tickers_and_sentiment(self, mock_get):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "feed": [
                {
                    "title": "Apple shares rise after AI update",
                    "source": "Reuters",
                    "time_published": "20250630T130000",
                    "summary": "Apple shares rose after a product update.",
                    "overall_sentiment_label": "Bullish",
                    "url": "https://example.com/aapl",
                    "ticker_sentiment": [
                        {"ticker": "AAPL", "ticker_sentiment_label": "Bullish"},
                        {"ticker": "MSFT", "ticker_sentiment_label": "Neutral"},
                    ],
                }
            ]
        }
        mock_get.return_value = mock_response

        articles = fetch_alpha_vantage_articles(tickers=["AAPL"])

        assert len(articles) == 1
        assert articles[0]["provider"] == "alpha_vantage"
        assert articles[0]["provider_tickers"] == ["AAPL"]
        assert articles[0]["provider_sentiment"] == "positive"
        assert articles[0]["published_at"] == "2025-06-30T13:00:00+00:00"
        assert mock_get.call_args.kwargs["params"]["tickers"] == "AAPL"

    @patch("requests.get")
    def test_returns_empty_on_http_error(self, mock_get):
        mock_get.side_effect = Exception("rate limited")

        assert fetch_alpha_vantage_articles(tickers=["AAPL"]) == []


# --- Tests for get_existing_hashes (Requirement 2.5) ---


class TestGetExistingHashes:
    """Tests for DynamoDB hash deduplication lookup."""

    @patch("backend.src.collectors.news_collector.store")
    def test_returns_existing_hashes(self, mock_store):
        """Test that existing hashes are returned as a set."""
        mock_conn = MagicMock()
        mock_store.existing_news_hashes.return_value = {"hash1", "hash2"}

        result = get_existing_hashes(mock_conn, ["hash1", "hash2", "hash3"])

        assert result == {"hash1", "hash2"}
        mock_store.existing_news_hashes.assert_called_once_with(
            ["hash1", "hash2", "hash3"]
        )

    def test_returns_empty_set_for_empty_input(self):
        """Test that empty input returns empty set without DB query."""
        mock_conn = MagicMock()
        result = get_existing_hashes(mock_conn, [])
        assert result == set()

    @patch("backend.src.collectors.news_collector.store")
    def test_returns_empty_set_when_none_exist(self, mock_store):
        """Test that non-existing hashes return empty set."""
        mock_conn = MagicMock()
        mock_store.existing_news_hashes.return_value = set()

        result = get_existing_hashes(mock_conn, ["newhash1", "newhash2"])

        assert result == set()


# --- Tests for generate_summary (Requirements 2.2, 2.7) ---


class TestGenerateSummary:
    """Tests for OpenAI GPT-4o-mini summary generation."""

    def test_successful_summary(self):
        """Test that a valid OpenAI response is parsed correctly."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "summary": "Apple reported record Q4 earnings driven by iPhone sales.",
            "tickers": ["AAPL"],
        })
        mock_client.chat.completions.create.return_value = mock_response

        result = generate_summary(mock_client, "Apple Earnings", "Apple beat estimates.")
        assert result["summary"] == "Apple reported record Q4 earnings driven by iPhone sales."
        assert result["tickers"] == ["AAPL"]

    def test_summary_truncated_to_500_chars(self):
        """Requirement 2.2: Summary capped at 500 characters."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        long_summary = "A" * 600
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "summary": long_summary,
            "tickers": [],
        })
        mock_client.chat.completions.create.return_value = mock_response

        result = generate_summary(mock_client, "Title", "Content")
        assert len(result["summary"]) == 500

    def test_invalid_tickers_filtered(self):
        """Test that invalid ticker formats are filtered out."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "summary": "Summary text.",
            "tickers": ["AAPL", "", "TOOLONGTICKER1", "MSFT", 123],
        })
        mock_client.chat.completions.create.return_value = mock_response

        result = generate_summary(mock_client, "Title", "Content")
        assert "AAPL" in result["tickers"]
        assert "MSFT" in result["tickers"]
        assert "" not in result["tickers"]
        # Invalid tickers should be excluded
        assert len(result["tickers"]) == 2

    def test_fallback_on_openai_error(self):
        """Test fallback to title[:500] when OpenAI fails."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        result = generate_summary(mock_client, "Test Title", "Content")
        assert result["summary"] == "Test Title"
        assert result["tickers"] == []

    def test_no_tickers_returns_empty_list(self):
        """Requirement 2.7: Articles without tickers get empty ticker list."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "summary": "General market overview.",
            "tickers": [],
        })
        mock_client.chat.completions.create.return_value = mock_response

        result = generate_summary(mock_client, "Market Overview", "General content.")
        assert result["tickers"] == []

    def test_ai_tickers_are_filtered_to_active_universe(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "summary": "Apple and a stale ticker were mentioned.",
            "tickers": ["AAPL", "DELISTED"],
        })
        mock_client.chat.completions.create.return_value = mock_response

        result = generate_summary(
            mock_client,
            "Apple earnings",
            "Apple reported results.",
            active_tickers={"AAPL"},
        )

        assert result["tickers"] == ["AAPL"]
        assert result["classification_confidence"] > 0
        assert result["ticker_classifications"][0]["sources"] == ["ai"]

    def test_fallback_ticker_matching_uses_word_boundaries(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        result = generate_summary(
            mock_client,
            "Snapdragon demand lifts handset suppliers",
            "AAPL supplier commentary was positive.",
            active_tickers={"AAPL", "SNAP"},
        )

        assert result["tickers"] == ["AAPL"]
        assert result["ticker_classifications"][0]["matched_by"] == "ai_and_text_boundary"

    def test_common_word_short_ticker_requires_provider_tag(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "summary": "The word on appears in normal prose.",
            "tickers": ["ON"],
        })
        mock_client.chat.completions.create.return_value = mock_response

        result = generate_summary(
            mock_client,
            "Market update on chips",
            "Analysts commented on semiconductor demand.",
            active_tickers={"ON"},
        )

        assert result["tickers"] == []


# --- Tests for store_article (Requirement 2.7) ---


class TestStoreArticle:
    """Tests for storing articles in DynamoDB."""

    @patch("backend.src.collectors.news_collector.store")
    def test_stores_article_with_tickers(self, mock_store):
        """Test article with tickers is stored as classified."""
        mock_conn = MagicMock()
        article = {
            "title": "Apple Earnings",
            "source": "Reuters",
            "published_at": "2025-01-15T10:00:00Z",
        }
        summary_data = {"summary": "Strong earnings.", "tickers": ["AAPL"]}
        title_source_hash = "abc123"

        store_article(mock_conn, article, summary_data, title_source_hash)

        mock_store.put_news_summary.assert_called_once_with(
            article, summary_data, title_source_hash
        )

    @patch("backend.src.collectors.news_collector.store")
    def test_stores_article_without_tickers_as_unclassified(self, mock_store):
        """Requirement 2.7: Article without tickers marked as unclassified."""
        mock_conn = MagicMock()
        article = {
            "title": "Market Overview",
            "source": "CNN",
            "published_at": "2025-01-15T10:00:00Z",
        }
        summary_data = {"summary": "General market news.", "tickers": []}
        title_source_hash = "def456"

        store_article(mock_conn, article, summary_data, title_source_hash)

        mock_store.put_news_summary.assert_called_once_with(
            article, summary_data, title_source_hash
        )

    @patch("backend.src.collectors.news_collector.store")
    def test_store_delegates_long_title_to_store(self, mock_store):
        """Store layer owns DynamoDB item normalization such as title truncation."""
        mock_conn = MagicMock()
        long_title = "A" * 600
        article = {
            "title": long_title,
            "source": "Source",
            "published_at": "2025-01-15T10:00:00Z",
        }
        summary_data = {"summary": "Summary.", "tickers": []}
        title_source_hash = "ghi789"

        store_article(mock_conn, article, summary_data, title_source_hash)

        mock_store.put_news_summary.assert_called_once_with(
            article, summary_data, title_source_hash
        )


class TestProviderClassification:
    """Tests provider supplied ticker/sentiment preservation."""

    def test_provider_tickers_merge_with_ai_tickers(self):
        article = {
            "provider_tickers": ["MSFT", "AAPL"],
            "provider_sentiment": "positive",
        }
        summary_data = {"summary": "Summary", "tickers": ["AAPL"], "sentiment": "neutral"}

        merged = merge_provider_classification(article, summary_data)

        assert merged["tickers"] == ["AAPL", "MSFT"]
        assert merged["sentiment"] == "positive"

    def test_ai_non_neutral_sentiment_wins_over_provider(self):
        article = {"provider_tickers": ["AAPL"], "provider_sentiment": "negative"}
        summary_data = {"summary": "Summary", "tickers": [], "sentiment": "positive"}

        merged = merge_provider_classification(article, summary_data)

        assert merged["tickers"] == ["AAPL"]
        assert merged["sentiment"] == "positive"

    def test_provider_ticker_preserves_common_word_symbol_when_active(self):
        article = {
            "provider_tickers": ["ON"],
            "provider_sentiment": "positive",
            "title": "ON Semiconductor reports earnings",
            "content": "Provider explicitly tagged the ticker.",
        }
        summary_data = {"summary": "Summary", "tickers": [], "sentiment": "neutral"}

        merged = merge_provider_classification(article, summary_data, active_tickers={"ON"})

        assert merged["tickers"] == ["ON"]
        assert merged["classification_confidence"] == 1.0
        assert merged["ticker_classifications"][0]["sources"] == ["provider"]


class TestNewsCollectionSummary:
    """Tests news completeness/failure summary shape."""

    def test_success_summary(self):
        summary = build_news_collection_summary(
            status="success",
            articles_processed=3,
            sources_available=3,
            total_fetched=5,
            duplicates_skipped=2,
        )

        assert summary["status"] == "success"
        assert summary["sources_total"] == 3
        assert summary["sources_failed"] == 0
        assert summary["completeness_ratio"] == 1.0

    def test_partial_summary_names_failed_sources(self):
        summary = build_news_collection_summary(
            status="partial",
            articles_processed=1,
            sources_available=1,
            total_fetched=1,
            failed_sources=["finnhub"],
            article_failures=1,
        )

        assert summary["status"] == "partial"
        assert summary["sources_failed"] == 2
        assert summary["failed_sources"] == ["finnhub"]
        assert summary["article_failures"] == 1
        assert summary["completeness_ratio"] == 0.3333

    @patch("backend.src.collectors.news_collector.cloudwatch")
    def test_summary_metrics_are_emitted(self, mock_cloudwatch):
        summary = build_news_collection_summary(
            status="partial",
            articles_processed=1,
            sources_available=1,
            total_fetched=1,
            failed_sources=["finnhub"],
            article_failures=1,
        )

        emit_news_collection_summary_metrics(summary)

        metric_data = mock_cloudwatch.put_metric_data.call_args.kwargs["MetricData"]
        metric_names = {metric["MetricName"] for metric in metric_data}
        assert "news_collection_completeness_percent" in metric_names
        assert "news_sources_failed" in metric_names
        assert "news_article_failures" in metric_names
        assert any(
            metric["MetricName"] == "news_collection_partial_runs"
            and metric["Value"] == 1
            for metric in metric_data
        )


# --- Tests for collect_news (Requirements 2.4, 2.5, 2.6) ---


class TestCollectNews:
    """Tests for the main news collection orchestration."""

    @patch("backend.src.collectors.news_collector.emit_metrics")
    @patch("backend.src.collectors.news_collector.raise_all_sources_failed_alert")
    @patch("backend.src.collectors.news_collector.fetch_alpha_vantage_articles")
    @patch("backend.src.collectors.news_collector.fetch_finnhub_articles")
    @patch("backend.src.collectors.news_collector.fetch_newsapi_articles")
    def test_all_sources_failed_raises_alert(
        self, mock_newsapi, mock_finnhub, mock_alpha, mock_alert, mock_metrics
    ):
        """Requirement 2.6: Alert raised when all sources fail."""
        mock_newsapi.return_value = []
        mock_finnhub.return_value = []
        mock_alpha.return_value = []

        result = collect_news()

        mock_alert.assert_called_once()
        assert result["status"] == "error"
        assert result["sources_available"] == 0

    @patch("backend.src.collectors.news_collector.emit_metrics")
    @patch("backend.src.collectors.news_collector.OpenAI")
    @patch("backend.src.collectors.news_collector.DatabasePool")
    @patch("backend.src.collectors.news_collector.get_existing_hashes")
    @patch("backend.src.collectors.news_collector.fetch_alpha_vantage_articles")
    @patch("backend.src.collectors.news_collector.fetch_finnhub_articles")
    @patch("backend.src.collectors.news_collector.fetch_newsapi_articles")
    def test_deduplication_skips_existing_articles(
        self, mock_newsapi, mock_finnhub, mock_alpha, mock_get_hashes,
        mock_db_pool, mock_openai, mock_metrics
    ):
        """Requirement 2.5: Duplicate articles are discarded."""
        mock_newsapi.return_value = [
            {"title": "Existing Article", "source": "Reuters", "published_at": "2025-01-15T10:00:00Z", "content": "Content"},
        ]
        mock_finnhub.return_value = []
        mock_alpha.return_value = []

        # Compute the hash that would be generated
        existing_hash = compute_title_source_hash("Existing Article", "Reuters")
        mock_get_hashes.return_value = {existing_hash}

        # Setup DB pool mock
        mock_conn = MagicMock()
        mock_db_pool.initialize.return_value = None
        mock_db_pool._pool.getconn.return_value = mock_conn

        # Setup OpenAI mock
        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        result = collect_news()

        assert result["status"] == "partial"
        assert result["articles_processed"] == 0
        assert result["duplicates_skipped"] == 1

    @patch("backend.src.collectors.news_collector.emit_metrics")
    @patch("backend.src.collectors.news_collector.store_article")
    @patch("backend.src.collectors.news_collector.generate_summary")
    @patch("backend.src.collectors.news_collector.OpenAI")
    @patch("backend.src.collectors.news_collector.DatabasePool")
    @patch("backend.src.collectors.news_collector.get_existing_hashes")
    @patch("backend.src.collectors.news_collector.fetch_alpha_vantage_articles")
    @patch("backend.src.collectors.news_collector.fetch_finnhub_articles")
    @patch("backend.src.collectors.news_collector.fetch_newsapi_articles")
    def test_new_articles_are_summarized_and_stored(
        self, mock_newsapi, mock_finnhub, mock_alpha, mock_get_hashes, mock_db_pool,
        mock_openai, mock_generate, mock_store, mock_metrics
    ):
        """Test that new articles go through summary + store pipeline."""
        mock_newsapi.return_value = [
            {"title": "New Article", "source": "CNBC", "published_at": "2025-01-15T10:00:00Z", "content": "Breaking news."},
        ]
        mock_finnhub.return_value = []
        mock_alpha.return_value = []
        mock_get_hashes.return_value = set()

        mock_conn = MagicMock()
        mock_db_pool.initialize.return_value = None
        mock_db_pool._pool.getconn.return_value = mock_conn

        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        mock_generate.return_value = {"summary": "AI Summary", "tickers": ["AAPL"]}

        result = collect_news()

        assert result["status"] == "partial"
        assert result["articles_processed"] == 1
        mock_generate.assert_called_once()
        mock_store.assert_called_once()

    @patch("backend.src.collectors.news_collector.emit_metrics")
    @patch("backend.src.collectors.news_collector.fetch_alpha_vantage_articles")
    @patch("backend.src.collectors.news_collector.fetch_finnhub_articles")
    @patch("backend.src.collectors.news_collector.fetch_newsapi_articles")
    def test_partial_source_failure_continues(
        self, mock_newsapi, mock_finnhub, mock_alpha, mock_metrics
    ):
        """Requirement 2.4: Continues collecting from remaining sources when one fails."""
        mock_newsapi.return_value = [
            {"title": "Article", "source": "CNBC", "published_at": "2025-01-15T10:00:00Z", "content": "Content"},
        ]
        mock_finnhub.return_value = []  # Finnhub failed
        mock_alpha.return_value = []

        with patch("backend.src.collectors.news_collector.DatabasePool") as mock_db_pool, \
             patch("backend.src.collectors.news_collector.get_existing_hashes") as mock_get_hashes, \
             patch("backend.src.collectors.news_collector.OpenAI") as mock_openai, \
             patch("backend.src.collectors.news_collector.generate_summary") as mock_generate, \
             patch("backend.src.collectors.news_collector.store_article"):

            mock_conn = MagicMock()
            mock_db_pool.initialize.return_value = None
            mock_db_pool._pool.getconn.return_value = mock_conn
            mock_get_hashes.return_value = set()
            mock_openai.return_value = MagicMock()
            mock_generate.return_value = {"summary": "Summary", "tickers": []}

            result = collect_news()

            assert result["status"] == "partial"
            assert result["sources_available"] == 1


# --- Tests for handler ---


class TestHandler:
    """Tests for the Lambda handler function."""

    @patch("backend.src.collectors.news_collector.DatabasePool")
    @patch("backend.src.collectors.news_collector.collect_news")
    def test_invocation_logs_event_payload_without_structlog_conflict(self, mock_collect, mock_db_pool):
        """Regression: structlog reserves the keyword argument name 'event'."""
        mock_collect.return_value = {
            "status": "success",
            "articles_processed": 0,
        }

        result = handler({"source": "aws.events"}, None)

        assert result["statusCode"] == 200
        mock_db_pool.close.assert_called_once()

    @patch("backend.src.collectors.news_collector.logger")
    @patch("backend.src.collectors.news_collector.DatabasePool")
    @patch("backend.src.collectors.news_collector.collect_news")
    def test_successful_invocation(self, mock_collect, mock_db_pool, mock_logger):
        """Test handler returns 200 on success."""
        mock_collect.return_value = {
            "status": "success",
            "articles_processed": 5,
        }

        result = handler({}, None)
        assert result["statusCode"] == 200
        assert result["body"]["articles_processed"] == 5
        mock_db_pool.close.assert_called_once()

    @patch("backend.src.collectors.news_collector.logger")
    @patch("backend.src.collectors.news_collector.DatabasePool")
    @patch("backend.src.collectors.news_collector.collect_news")
    def test_handler_returns_500_on_error(self, mock_collect, mock_db_pool, mock_logger):
        """Test handler returns 500 when collect_news raises."""
        mock_collect.side_effect = Exception("Database connection failed")

        result = handler({}, None)
        assert result["statusCode"] == 500
        assert "error" in result["body"]["status"]
        mock_db_pool.close.assert_called_once()

    @patch("backend.src.collectors.news_collector.write_manifest")
    @patch("backend.src.collectors.news_collector.load_manifest")
    @patch("backend.src.collectors.news_collector.collect_news")
    def test_handler_processes_manifest_news_task(
        self,
        mock_collect,
        mock_load_manifest,
        mock_write_manifest,
    ):
        """Manifest task mode scopes news collection to the chunk tickers."""
        manifest = build_manifest(
            [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
            manifest_date=date(2026, 6, 20),
            generated_at=datetime(2026, 6, 20, tzinfo=timezone.utc),
        )
        task = next(
            candidate
            for candidate in manifest.tasks
            if candidate.task_type == CollectionTaskType.NEWS
        )
        mock_load_manifest.return_value = manifest
        mock_collect.return_value = {
            "status": "success",
            "articles_processed": 2,
            "total_fetched": 3,
            "tickers": task.tickers,
            "collection_summary": {
                "articles_fetched": 3,
                "articles_processed": 2,
                "duplicates_skipped": 1,
                "article_failures": 0,
            },
        }

        context = MagicMock(aws_request_id="request-1")
        result = handler(
            {
                "mode": "manifest_task",
                "manifest_bucket": "bucket",
                "manifest_key": manifest.s3_key,
                "task_id": task.task_id,
            },
            context,
        )

        assert result["statusCode"] == 200
        mock_collect.assert_called_once_with(tickers=task.tickers)
        assert mock_write_manifest.call_count == 2
        assert task.status == CollectionTaskStatus.SUCCEEDED
        assert task.output_counts.records_fetched == 3
        assert task.output_counts.records_written == 2
        assert task.output_counts.duplicate_records == 1
        assert manifest.summary.succeeded_tasks == 1
