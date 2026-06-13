"""AI Analysis Lambda handler.

Generates daily BUY/HOLD/SELL recommendations for all monitored stocks
using OpenAI GPT-4o-mini with technical indicators and news context.
Triggered by EventBridge daily at 22:00 UTC.

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7
"""

import json
import os
from datetime import date, datetime, timedelta
from typing import Any

import boto3
import pandas as pd
import structlog
from openai import OpenAI

from backend.src.db.connection import DatabasePool, store

logger = structlog.get_logger(__name__)

# Configuration
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
BATCH_SIZE = 50
HISTORY_DAYS = 30
NEWS_DAYS = 7
CLOUDWATCH_NAMESPACE = "StockMonitoring"

# Structured output schema for OpenAI
ANALYSIS_RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "stock_analysis",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "short_term_recommendation": {
                    "type": "string",
                    "enum": ["BUY", "HOLD", "SELL"],
                    "description": "Recommendation for 1-30 day timeframe",
                },
                "long_term_recommendation": {
                    "type": "string",
                    "enum": ["BUY", "HOLD", "SELL"],
                    "description": "Recommendation for 30+ day timeframe",
                },
                "risk_level": {
                    "type": "string",
                    "enum": ["LOW", "MEDIUM", "HIGH"],
                    "description": "Risk classification for this stock",
                },
                "confidence_score": {
                    "type": "integer",
                    "description": "Confidence level 0-100",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief reasoning for the recommendation",
                },
            },
            "required": [
                "short_term_recommendation",
                "long_term_recommendation",
                "risk_level",
                "confidence_score",
                "reasoning",
            ],
            "additionalProperties": False,
        },
    },
}


def handler(event: dict, context: Any) -> dict:
    """Lambda handler for AI stock analysis.

    Triggered by EventBridge daily at 22:00 UTC.
    Analyzes all active stocks in batches of 50.
    """
    log = logger.bind(event=event)
    log.info("ai_analyzer_started")

    try:
        DatabasePool.initialize()
        tickers = _fetch_active_tickers()

        if not tickers:
            log.warning("no_active_tickers_found")
            return {"statusCode": 200, "body": "No active tickers to analyze"}

        log.info("tickers_loaded", ticker_count=len(tickers))

        # Batch tickers in groups of 50 to handle Lambda timeout
        batches = [
            tickers[i : i + BATCH_SIZE]
            for i in range(0, len(tickers), BATCH_SIZE)
        ]

        analyzed_count = 0
        failed_count = 0

        for batch_idx, batch in enumerate(batches):
            log.info(
                "processing_batch",
                batch_index=batch_idx,
                batch_size=len(batch),
                total_batches=len(batches),
            )
            batch_analyzed, batch_failed = _process_batch(batch)
            analyzed_count += batch_analyzed
            failed_count += batch_failed

        _emit_metric("analysis_generated", analyzed_count)

        log.info(
            "ai_analyzer_completed",
            analyzed=analyzed_count,
            failed=failed_count,
            total_tickers=len(tickers),
        )

        return {
            "statusCode": 200,
            "body": f"Analyzed {analyzed_count} stocks, {failed_count} failed",
        }

    except Exception as e:
        log.error("ai_analyzer_failed", error=str(e), exc_info=True)
        _emit_metric("analysis_generated", 0)
        raise
    finally:
        DatabasePool.close()


def _fetch_active_tickers() -> list[dict]:
    """Fetch active tickers with sector and company size from the watchlist."""
    return store.active_stock_metadata()


def _process_batch(stocks: list[dict]) -> tuple[int, int]:
    """Process a batch of stocks for AI analysis.

    Returns:
        Tuple of (successfully analyzed count, failed count)
    """
    analyzed = 0
    failed = 0
    today = date.today()

    client = OpenAI(api_key=OPENAI_API_KEY)

    for stock in stocks:
        ticker = stock["ticker"]
        try:
            result = _analyze_stock(client, stock, today)
            if result:
                _store_analysis(result, today)
                analyzed += 1
            else:
                failed += 1
                logger.warning("analysis_returned_none", ticker=ticker)
        except Exception as e:
            failed += 1
            logger.error(
                "stock_analysis_failed",
                ticker=ticker,
                error=str(e),
                exc_info=True,
            )

    return analyzed, failed


def _analyze_stock(
    client: OpenAI, stock: dict, analysis_date: date
) -> dict | None:
    """Generate AI analysis for a single stock.

    Retrieves 30 days of OHLCV data and 7 days of news, calculates
    technical indicators, and calls OpenAI for a recommendation.

    Returns the analysis result dict or None on failure.
    """
    ticker = stock["ticker"]
    log = logger.bind(ticker=ticker)

    # Retrieve historical OHLCV data (30 days)
    ohlcv_data = _get_ohlcv_data(ticker, analysis_date)
    if ohlcv_data is None or len(ohlcv_data) < 5:
        log.warning(
            "insufficient_ohlcv_data",
            records=len(ohlcv_data) if ohlcv_data else 0,
        )
        return None

    # Retrieve news summaries (7 days)
    news_summaries = _get_news_summaries(ticker, analysis_date)

    # Calculate technical indicators
    indicators = _calculate_technical_indicators(ohlcv_data)

    # Construct prompt and call OpenAI
    prompt = _build_prompt(stock, ohlcv_data, indicators, news_summaries)
    response = _call_openai(client, prompt)

    if response is None:
        log.warning("openai_returned_none")
        return None

    # Validate and return result
    result = _validate_response(response, ticker)
    return result


def _get_ohlcv_data(ticker: str, as_of_date: date) -> list[dict] | None:
    """Retrieve last 30 days of OHLCV data for a ticker."""
    start_date = as_of_date - timedelta(days=HISTORY_DAYS)
    try:
        rows = store.get_stock_data(ticker, start_date, as_of_date)
        return rows if rows else None
    except Exception as e:
        logger.error("ohlcv_fetch_failed", ticker=ticker, error=str(e))
        return None


def _get_news_summaries(ticker: str, as_of_date: date) -> list[dict]:
    """Retrieve last 7 days of news summaries related to a ticker."""
    start_date = as_of_date - timedelta(days=NEWS_DAYS)

    try:
        return store.news_for_ticker(ticker, start_date, as_of_date)
    except Exception as e:
        logger.warning("news_fetch_failed", ticker=ticker, error=str(e))
        return []


def _calculate_technical_indicators(ohlcv_data: list[dict]) -> dict:
    """Calculate SMA-20, RSI-14, and MACD from OHLCV data using pandas.

    Returns a dict with the latest values for each indicator.
    """
    df = pd.DataFrame(ohlcv_data)
    df["close_price"] = df["close_price"].astype(float)

    indicators = {}

    # SMA-20 (Simple Moving Average over 20 periods)
    if len(df) >= 20:
        df["sma_20"] = df["close_price"].rolling(window=20).mean()
        indicators["sma_20"] = round(float(df["sma_20"].iloc[-1]), 4)
    else:
        # Use available data for shorter SMA
        sma = df["close_price"].mean()
        indicators["sma_20"] = round(float(sma), 4)

    # RSI-14 (Relative Strength Index over 14 periods)
    if len(df) >= 2:
        delta = df["close_price"].diff()
        gains = delta.where(delta > 0, 0.0)
        losses = (-delta).where(delta < 0, 0.0)

        if len(df) >= 14:
            avg_gain = gains.rolling(window=14).mean().iloc[-1]
            avg_loss = losses.rolling(window=14).mean().iloc[-1]
        else:
            avg_gain = gains.mean()
            avg_loss = losses.mean()

        if avg_loss == 0:
            indicators["rsi_14"] = 100.0
        else:
            rs = avg_gain / avg_loss
            indicators["rsi_14"] = round(100.0 - (100.0 / (1.0 + rs)), 2)
    else:
        indicators["rsi_14"] = 50.0  # Neutral default

    # MACD (12-period EMA - 26-period EMA, signal line: 9-period EMA of MACD)
    if len(df) >= 26:
        ema_12 = df["close_price"].ewm(span=12, adjust=False).mean()
        ema_26 = df["close_price"].ewm(span=26, adjust=False).mean()
        macd_line = ema_12 - ema_26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()

        indicators["macd"] = round(float(macd_line.iloc[-1]), 4)
        indicators["macd_signal"] = round(float(signal_line.iloc[-1]), 4)
        indicators["macd_histogram"] = round(
            float(macd_line.iloc[-1] - signal_line.iloc[-1]), 4
        )
    else:
        # Simplified MACD with available data
        if len(df) >= 12:
            ema_12 = df["close_price"].ewm(span=12, adjust=False).mean()
            ema_short = df["close_price"].ewm(
                span=min(len(df), 26), adjust=False
            ).mean()
            macd_val = float(ema_12.iloc[-1] - ema_short.iloc[-1])
            indicators["macd"] = round(macd_val, 4)
            indicators["macd_signal"] = 0.0
            indicators["macd_histogram"] = round(macd_val, 4)
        else:
            indicators["macd"] = 0.0
            indicators["macd_signal"] = 0.0
            indicators["macd_histogram"] = 0.0

    # Latest price for context
    indicators["latest_close"] = round(float(df["close_price"].iloc[-1]), 4)
    indicators["price_change_pct"] = round(
        float(
            (df["close_price"].iloc[-1] - df["close_price"].iloc[0])
            / df["close_price"].iloc[0]
            * 100
        ),
        2,
    )

    return indicators


def _build_prompt(
    stock: dict,
    ohlcv_data: list[dict],
    indicators: dict,
    news_summaries: list[dict],
) -> str:
    """Construct the analysis prompt for OpenAI."""
    ticker = stock["ticker"]
    sector = stock["sector"]
    company_size = stock["company_size"]

    # Format OHLCV summary (last 5 days detail + overall stats)
    recent_data = ohlcv_data[-5:]
    ohlcv_summary = "\n".join(
        f"  {r['trading_date']}: O={r['open_price']} H={r['high_price']} "
        f"L={r['low_price']} C={r['close_price']} V={r['volume']}"
        for r in recent_data
    )

    # Format indicators
    indicators_text = (
        f"  SMA(20): {indicators['sma_20']}\n"
        f"  RSI(14): {indicators['rsi_14']}\n"
        f"  MACD: {indicators['macd']} (Signal: {indicators['macd_signal']}, "
        f"Histogram: {indicators['macd_histogram']})\n"
        f"  Latest Close: {indicators['latest_close']}\n"
        f"  30-day Price Change: {indicators['price_change_pct']}%"
    )

    # Format news
    if news_summaries:
        news_text = "\n".join(
            f"  [{n['published_at']}] {n['title']} ({n['source']}): {n['summary']}"
            for n in news_summaries[:10]
        )
    else:
        news_text = "  No recent news available."

    prompt = f"""Given the following data for {ticker}:

Stock Info:
  Ticker: {ticker}
  Sector: {sector}
  Company Size: {company_size}

Recent OHLCV Data (last 5 trading days):
{ohlcv_summary}

Technical Indicators (based on {len(ohlcv_data)} trading days):
{indicators_text}

Recent News (last 7 days):
{news_text}

Based on this data, provide your analysis:
1. Classify this stock as BUY, HOLD, or SELL for the short-term (1-30 days)
2. Classify this stock as BUY, HOLD, or SELL for the long-term (30+ days)
3. Assign a risk level: LOW, MEDIUM, or HIGH
4. Provide a confidence score (0-100)
5. Provide brief reasoning (2-3 sentences max)"""

    return prompt


def _call_openai(client: OpenAI, prompt: str) -> dict | None:
    """Call OpenAI GPT-4o-mini with structured output schema.

    Returns parsed JSON response or None on failure.
    """
    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a financial analyst AI. Analyze stock data and "
                        "provide structured recommendations. Be objective and "
                        "consider both technical indicators and news sentiment."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format=ANALYSIS_RESPONSE_SCHEMA,
            temperature=0.3,
            max_tokens=500,
        )

        content = response.choices[0].message.content
        if content:
            return json.loads(content)
        return None

    except Exception as e:
        logger.error("openai_call_failed", error=str(e))
        return None


def _validate_response(response: dict, ticker: str) -> dict | None:
    """Validate the OpenAI response matches expected schema.

    Returns validated result dict with ticker or None if invalid.
    """
    required_fields = [
        "short_term_recommendation",
        "long_term_recommendation",
        "risk_level",
        "confidence_score",
        "reasoning",
    ]

    for field in required_fields:
        if field not in response:
            logger.warning(
                "response_missing_field", ticker=ticker, field=field
            )
            return None

    # Validate enum values
    if response["short_term_recommendation"] not in ("BUY", "HOLD", "SELL"):
        logger.warning(
            "invalid_short_term_recommendation",
            ticker=ticker,
            value=response["short_term_recommendation"],
        )
        return None

    if response["long_term_recommendation"] not in ("BUY", "HOLD", "SELL"):
        logger.warning(
            "invalid_long_term_recommendation",
            ticker=ticker,
            value=response["long_term_recommendation"],
        )
        return None

    if response["risk_level"] not in ("LOW", "MEDIUM", "HIGH"):
        logger.warning(
            "invalid_risk_level",
            ticker=ticker,
            value=response["risk_level"],
        )
        return None

    # Validate confidence score range
    confidence = response["confidence_score"]
    if not isinstance(confidence, int) or confidence < 0 or confidence > 100:
        logger.warning(
            "invalid_confidence_score",
            ticker=ticker,
            value=confidence,
        )
        return None

    return {
        "ticker": ticker,
        "short_term_recommendation": response["short_term_recommendation"],
        "long_term_recommendation": response["long_term_recommendation"],
        "risk_level": response["risk_level"],
        "confidence_score": confidence,
        "reasoning": response.get("reasoning", ""),
    }


def _store_analysis(result: dict, analysis_date: date) -> None:
    """Store or replace analysis result for a ticker/date."""
    try:
        store.put_analysis(result, analysis_date)
        logger.info("analysis_stored", ticker=result["ticker"])
    except Exception as e:
        logger.error(
            "analysis_store_failed",
            ticker=result["ticker"],
            error=str(e),
        )
        raise


def _emit_metric(metric_name: str, value: float) -> None:
    """Emit a custom CloudWatch metric."""
    try:
        client = boto3.client("cloudwatch")
        client.put_metric_data(
            Namespace=CLOUDWATCH_NAMESPACE,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": "Count",
                    "Timestamp": datetime.utcnow(),
                }
            ],
        )
        logger.info("cloudwatch_metric_emitted", metric=metric_name, value=value)
    except Exception as e:
        logger.warning("cloudwatch_metric_failed", metric=metric_name, error=str(e))
