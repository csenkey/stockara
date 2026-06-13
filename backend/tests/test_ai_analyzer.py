"""Tests for the AI Analyzer module."""

import json
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch, call

import pytest

from backend.src.analysis.ai_analyzer import (
    _build_prompt,
    _calculate_technical_indicators,
    _call_openai,
    _process_batch,
    _store_analysis,
    _validate_response,
    handler,
)


class TestCalculateTechnicalIndicators:
    """Tests for _calculate_technical_indicators."""

    def _make_ohlcv(self, prices: list[float]) -> list[dict]:
        """Helper to create OHLCV data from a list of close prices."""
        from datetime import timedelta

        base_date = date(2025, 1, 1)
        return [
            {
                "trading_date": base_date + timedelta(days=i),
                "open_price": Decimal(str(p - 0.5)),
                "high_price": Decimal(str(p + 1.0)),
                "low_price": Decimal(str(p - 1.0)),
                "close_price": Decimal(str(p)),
                "volume": 1000000,
            }
            for i, p in enumerate(prices)
        ]

    def test_sma_20_with_sufficient_data(self):
        """SMA-20 calculated correctly with 20+ data points."""
        prices = [100.0 + i for i in range(25)]
        data = self._make_ohlcv(prices)
        indicators = _calculate_technical_indicators(data)

        assert "sma_20" in indicators
        # SMA-20 of last 20 values: mean of 105..124 = 114.5
        assert indicators["sma_20"] == 114.5

    def test_sma_with_insufficient_data(self):
        """SMA falls back to mean when < 20 data points."""
        prices = [100.0, 102.0, 104.0, 103.0, 105.0]
        data = self._make_ohlcv(prices)
        indicators = _calculate_technical_indicators(data)

        assert "sma_20" in indicators
        expected = round(sum(prices) / len(prices), 4)
        assert indicators["sma_20"] == expected

    def test_rsi_all_gains(self):
        """RSI should be 100 when there are only gains (no losses)."""
        prices = [100.0 + i * 2 for i in range(15)]
        data = self._make_ohlcv(prices)
        indicators = _calculate_technical_indicators(data)

        assert indicators["rsi_14"] == 100.0

    def test_rsi_mixed(self):
        """RSI should be between 0 and 100 for mixed price movement."""
        prices = [100.0, 102.0, 101.0, 103.0, 100.5, 104.0, 102.0, 105.0,
                  103.0, 106.0, 104.0, 107.0, 105.0, 108.0, 106.0]
        data = self._make_ohlcv(prices)
        indicators = _calculate_technical_indicators(data)

        assert 0 < indicators["rsi_14"] < 100

    def test_macd_with_sufficient_data(self):
        """MACD calculated with 26+ data points."""
        prices = [100.0 + i * 0.5 for i in range(30)]
        data = self._make_ohlcv(prices)
        indicators = _calculate_technical_indicators(data)

        assert "macd" in indicators
        assert "macd_signal" in indicators
        assert "macd_histogram" in indicators

    def test_latest_close_and_price_change(self):
        """Latest close and price change percentage are calculated."""
        prices = [100.0, 110.0]
        data = self._make_ohlcv(prices)
        indicators = _calculate_technical_indicators(data)

        assert indicators["latest_close"] == 110.0
        assert indicators["price_change_pct"] == 10.0


class TestValidateResponse:
    """Tests for _validate_response."""

    def test_valid_response(self):
        """A valid response passes validation."""
        response = {
            "short_term_recommendation": "BUY",
            "long_term_recommendation": "HOLD",
            "risk_level": "MEDIUM",
            "confidence_score": 75,
            "reasoning": "Strong technicals with moderate risk.",
        }
        result = _validate_response(response, "AAPL")

        assert result is not None
        assert result["ticker"] == "AAPL"
        assert result["short_term_recommendation"] == "BUY"
        assert result["long_term_recommendation"] == "HOLD"
        assert result["risk_level"] == "MEDIUM"
        assert result["confidence_score"] == 75

    def test_missing_field(self):
        """Response missing a required field returns None."""
        response = {
            "short_term_recommendation": "BUY",
            "long_term_recommendation": "HOLD",
            "risk_level": "MEDIUM",
            # missing confidence_score and reasoning
        }
        result = _validate_response(response, "AAPL")
        assert result is None

    def test_invalid_recommendation(self):
        """Invalid recommendation value returns None."""
        response = {
            "short_term_recommendation": "STRONG_BUY",
            "long_term_recommendation": "HOLD",
            "risk_level": "MEDIUM",
            "confidence_score": 75,
            "reasoning": "Test.",
        }
        result = _validate_response(response, "AAPL")
        assert result is None

    def test_invalid_risk_level(self):
        """Invalid risk level returns None."""
        response = {
            "short_term_recommendation": "BUY",
            "long_term_recommendation": "HOLD",
            "risk_level": "EXTREME",
            "confidence_score": 75,
            "reasoning": "Test.",
        }
        result = _validate_response(response, "AAPL")
        assert result is None

    def test_confidence_score_out_of_range(self):
        """Confidence score outside 0-100 returns None."""
        response = {
            "short_term_recommendation": "BUY",
            "long_term_recommendation": "HOLD",
            "risk_level": "LOW",
            "confidence_score": 150,
            "reasoning": "Test.",
        }
        result = _validate_response(response, "AAPL")
        assert result is None

    def test_confidence_score_negative(self):
        """Negative confidence score returns None."""
        response = {
            "short_term_recommendation": "BUY",
            "long_term_recommendation": "HOLD",
            "risk_level": "LOW",
            "confidence_score": -5,
            "reasoning": "Test.",
        }
        result = _validate_response(response, "AAPL")
        assert result is None


class TestBuildPrompt:
    """Tests for _build_prompt."""

    def test_prompt_contains_ticker_info(self):
        """Prompt includes stock ticker, sector, and company size."""
        stock = {"ticker": "AAPL", "sector": "Technology", "company_size": "blue_chip"}
        ohlcv_data = [
            {
                "trading_date": date(2025, 1, 1),
                "open_price": Decimal("149.50"),
                "high_price": Decimal("151.00"),
                "low_price": Decimal("149.00"),
                "close_price": Decimal("150.00"),
                "volume": 5000000,
            }
        ]
        indicators = {
            "sma_20": 148.5,
            "rsi_14": 55.0,
            "macd": 0.5,
            "macd_signal": 0.3,
            "macd_histogram": 0.2,
            "latest_close": 150.0,
            "price_change_pct": 1.5,
        }
        news = [
            {
                "published_at": datetime(2025, 1, 1, 10, 0),
                "title": "Apple reports earnings",
                "source": "Reuters",
                "summary": "Strong Q4 results.",
            }
        ]

        prompt = _build_prompt(stock, ohlcv_data, indicators, news)

        assert "AAPL" in prompt
        assert "Technology" in prompt
        assert "blue_chip" in prompt
        assert "SMA(20): 148.5" in prompt
        assert "RSI(14): 55.0" in prompt
        assert "Apple reports earnings" in prompt

    def test_prompt_with_no_news(self):
        """Prompt handles empty news list gracefully."""
        stock = {"ticker": "TSLA", "sector": "Automotive", "company_size": "blue_chip"}
        ohlcv_data = [
            {
                "trading_date": date(2025, 1, 1),
                "open_price": Decimal("200.00"),
                "high_price": Decimal("205.00"),
                "low_price": Decimal("198.00"),
                "close_price": Decimal("202.00"),
                "volume": 3000000,
            }
        ]
        indicators = {
            "sma_20": 199.0,
            "rsi_14": 60.0,
            "macd": 1.0,
            "macd_signal": 0.8,
            "macd_histogram": 0.2,
            "latest_close": 202.0,
            "price_change_pct": 2.0,
        }

        prompt = _build_prompt(stock, ohlcv_data, indicators, [])

        assert "No recent news available" in prompt


class TestCalculateTechnicalIndicatorsEdgeCases:
    """Edge case tests for _calculate_technical_indicators."""

    def _make_ohlcv(self, prices: list[float]) -> list[dict]:
        """Helper to create OHLCV data from a list of close prices."""
        from datetime import timedelta

        base_date = date(2025, 1, 1)
        return [
            {
                "trading_date": base_date + timedelta(days=i),
                "open_price": Decimal(str(p - 0.5)),
                "high_price": Decimal(str(p + 1.0)),
                "low_price": Decimal(str(p - 1.0)),
                "close_price": Decimal(str(p)),
                "volume": 1000000,
            }
            for i, p in enumerate(prices)
        ]

    def test_single_data_point(self):
        """Single data point returns defaults for RSI and MACD."""
        data = self._make_ohlcv([100.0])
        indicators = _calculate_technical_indicators(data)

        assert indicators["sma_20"] == 100.0
        assert indicators["rsi_14"] == 50.0  # Neutral default
        assert indicators["macd"] == 0.0
        assert indicators["macd_signal"] == 0.0
        assert indicators["macd_histogram"] == 0.0
        assert indicators["latest_close"] == 100.0
        assert indicators["price_change_pct"] == 0.0

    def test_two_data_points_rsi_all_loss(self):
        """Two points with loss: RSI calculated from available data."""
        data = self._make_ohlcv([110.0, 100.0])
        indicators = _calculate_technical_indicators(data)

        # With only losses, avg_gain=0, so RSI = 0
        assert indicators["rsi_14"] == 0.0

    def test_constant_prices(self):
        """Constant prices: RSI is 100 (no losses, avg_loss=0)."""
        data = self._make_ohlcv([100.0] * 15)
        indicators = _calculate_technical_indicators(data)

        # delta is 0 for all, gains=0, losses=0, avg_loss=0 -> RSI=100
        assert indicators["rsi_14"] == 100.0

    def test_twelve_to_twenty_five_data_points_macd(self):
        """Between 12 and 25 points: simplified MACD is used."""
        prices = [100.0 + i for i in range(15)]
        data = self._make_ohlcv(prices)
        indicators = _calculate_technical_indicators(data)

        assert "macd" in indicators
        assert indicators["macd_signal"] == 0.0  # Simplified version


class TestCallOpenAI:
    """Tests for _call_openai with mocked OpenAI client."""

    def test_successful_call(self):
        """Successful OpenAI call returns parsed JSON response."""
        mock_client = MagicMock()
        expected = {
            "short_term_recommendation": "BUY",
            "long_term_recommendation": "HOLD",
            "risk_level": "MEDIUM",
            "confidence_score": 80,
            "reasoning": "Strong momentum.",
        }
        mock_message = MagicMock()
        mock_message.content = json.dumps(expected)
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        result = _call_openai(mock_client, "Analyze AAPL")

        assert result == expected
        mock_client.chat.completions.create.assert_called_once()

    def test_openai_returns_empty_content(self):
        """OpenAI returning empty content returns None."""
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = ""
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_client.chat.completions.create.return_value = mock_response

        result = _call_openai(mock_client, "Analyze AAPL")
        assert result is None

    def test_openai_raises_exception(self):
        """OpenAI exception returns None without raising."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        result = _call_openai(mock_client, "Analyze AAPL")
        assert result is None


class TestProcessBatch:
    """Tests for _process_batch batching and failure handling."""

    @patch("backend.src.analysis.ai_analyzer._store_analysis")
    @patch("backend.src.analysis.ai_analyzer._analyze_stock")
    @patch("backend.src.analysis.ai_analyzer.OpenAI")
    def test_batch_processes_all_stocks(self, mock_openai_cls, mock_analyze, mock_store):
        """All stocks in a batch are processed."""
        mock_analyze.return_value = {
            "ticker": "AAPL",
            "short_term_recommendation": "BUY",
            "long_term_recommendation": "HOLD",
            "risk_level": "LOW",
            "confidence_score": 85,
            "reasoning": "Good.",
        }

        stocks = [
            {"ticker": "AAPL", "sector": "Tech", "company_size": "blue_chip"},
            {"ticker": "GOOG", "sector": "Tech", "company_size": "blue_chip"},
            {"ticker": "TSLA", "sector": "Auto", "company_size": "blue_chip"},
        ]

        analyzed, failed = _process_batch(stocks)

        assert analyzed == 3
        assert failed == 0
        assert mock_analyze.call_count == 3
        assert mock_store.call_count == 3

    @patch("backend.src.analysis.ai_analyzer._store_analysis")
    @patch("backend.src.analysis.ai_analyzer._analyze_stock")
    @patch("backend.src.analysis.ai_analyzer.OpenAI")
    def test_individual_failure_doesnt_stop_batch(self, mock_openai_cls, mock_analyze, mock_store):
        """A single stock failure doesn't stop processing of remaining stocks.

        Validates: Requirements 4.7
        """
        # First stock raises, second returns None, third succeeds
        mock_analyze.side_effect = [
            Exception("OpenAI timeout"),
            None,
            {
                "ticker": "TSLA",
                "short_term_recommendation": "SELL",
                "long_term_recommendation": "SELL",
                "risk_level": "HIGH",
                "confidence_score": 60,
                "reasoning": "Bearish.",
            },
        ]

        stocks = [
            {"ticker": "AAPL", "sector": "Tech", "company_size": "blue_chip"},
            {"ticker": "GOOG", "sector": "Tech", "company_size": "blue_chip"},
            {"ticker": "TSLA", "sector": "Auto", "company_size": "blue_chip"},
        ]

        analyzed, failed = _process_batch(stocks)

        assert analyzed == 1
        assert failed == 2
        assert mock_store.call_count == 1


class TestStoreAnalysis:
    """Tests for _store_analysis with mocked DynamoDB store."""

    @patch("backend.src.analysis.ai_analyzer.store")
    def test_store_analysis_success(self, mock_store):
        """Analysis is stored via DynamoDB upsert."""
        result = {
            "ticker": "AAPL",
            "short_term_recommendation": "BUY",
            "long_term_recommendation": "HOLD",
            "risk_level": "MEDIUM",
            "confidence_score": 75,
            "reasoning": "Strong technicals.",
        }

        _store_analysis(result, date(2025, 1, 15))

        mock_store.put_analysis.assert_called_once_with(result, date(2025, 1, 15))

    @patch("backend.src.analysis.ai_analyzer.store")
    def test_store_analysis_db_error_raises(self, mock_store):
        """Database error during store is re-raised."""
        mock_store.put_analysis.side_effect = Exception("DynamoDB write failed")
        result = {
            "ticker": "AAPL",
            "short_term_recommendation": "BUY",
            "long_term_recommendation": "HOLD",
            "risk_level": "MEDIUM",
            "confidence_score": 75,
            "reasoning": "Test.",
        }

        with pytest.raises(Exception, match="DynamoDB write failed"):
            _store_analysis(result, date(2025, 1, 15))


class TestHandler:
    """Integration tests for the handler() function."""

    @patch("backend.src.analysis.ai_analyzer._emit_metric")
    @patch("backend.src.analysis.ai_analyzer._process_batch")
    @patch("backend.src.analysis.ai_analyzer._fetch_active_tickers")
    @patch("backend.src.analysis.ai_analyzer.DatabasePool")
    def test_handler_success(self, mock_db_pool, mock_fetch, mock_process, mock_metric):
        """Handler processes all tickers and returns success."""
        mock_fetch.return_value = [
            {"ticker": "AAPL", "sector": "Tech", "company_size": "blue_chip"},
            {"ticker": "GOOG", "sector": "Tech", "company_size": "blue_chip"},
        ]
        mock_process.return_value = (2, 0)

        result = handler({}, None)

        assert result["statusCode"] == 200
        assert "2" in result["body"]
        mock_db_pool.initialize.assert_called_once()
        mock_db_pool.close.assert_called_once()

    @patch("backend.src.analysis.ai_analyzer._emit_metric")
    @patch("backend.src.analysis.ai_analyzer._fetch_active_tickers")
    @patch("backend.src.analysis.ai_analyzer.DatabasePool")
    def test_handler_no_tickers(self, mock_db_pool, mock_fetch, mock_metric):
        """Handler returns early when no active tickers exist."""
        mock_fetch.return_value = []

        result = handler({}, None)

        assert result["statusCode"] == 200
        assert "No active tickers" in result["body"]

    @patch("backend.src.analysis.ai_analyzer._emit_metric")
    @patch("backend.src.analysis.ai_analyzer._process_batch")
    @patch("backend.src.analysis.ai_analyzer._fetch_active_tickers")
    @patch("backend.src.analysis.ai_analyzer.DatabasePool")
    def test_handler_batches_tickers(self, mock_db_pool, mock_fetch, mock_process, mock_metric):
        """Handler splits tickers into batches of 50.

        Validates: Requirements 4.5
        """
        # 120 tickers -> 3 batches (50, 50, 20)
        tickers = [
            {"ticker": f"T{i:03d}", "sector": "Tech", "company_size": "blue_chip"}
            for i in range(120)
        ]
        mock_fetch.return_value = tickers
        mock_process.return_value = (50, 0)

        result = handler({}, None)

        assert mock_process.call_count == 3
        # Check batch sizes
        batch_sizes = [len(c[0][0]) for c in mock_process.call_args_list]
        assert batch_sizes == [50, 50, 20]

    @patch("backend.src.analysis.ai_analyzer._emit_metric")
    @patch("backend.src.analysis.ai_analyzer._fetch_active_tickers")
    @patch("backend.src.analysis.ai_analyzer.DatabasePool")
    def test_handler_exception_still_closes_pool(self, mock_db_pool, mock_fetch, mock_metric):
        """Handler closes database pool even on exception."""
        mock_fetch.side_effect = Exception("DB unavailable")

        with pytest.raises(Exception, match="DB unavailable"):
            handler({}, None)

        mock_db_pool.close.assert_called_once()
