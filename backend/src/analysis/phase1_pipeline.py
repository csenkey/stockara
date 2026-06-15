"""Phase 1 candidate scanning, AI analysis, ranking, and static publishing."""

import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3
from openai import OpenAI
import structlog
import yfinance as yf

from backend.src.db.connection import DatabasePool, store

logger = structlog.get_logger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
ARTIFACT_BUCKET = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "")
SHORTLIST_SIZE = int(os.environ.get("PHASE1_SHORTLIST_SIZE", "50"))
TOP_PICK_COUNT = int(os.environ.get("PHASE1_TOP_PICK_COUNT", "10"))
CLOUDWATCH_NAMESPACE = "StockaraPhase1"

SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Finance": "XLF",
    "Energy": "XLE",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
    "Telecommunications": "XLC",
}

NEGATIVE_KEYWORDS = (
    "downgrade",
    "guidance cut",
    "misses",
    "missed",
    "lawsuit",
    "investigation",
    "fraud",
    "sec",
    "dividend cut",
    "suspends dividend",
    "bankruptcy",
    "layoffs",
)

POSITIVE_KEYWORDS = (
    "upgrade",
    "beats",
    "beat",
    "raises guidance",
    "record revenue",
    "approval",
    "contract",
    "partnership",
    "buyback",
)


def run_phase1_pipeline(event: dict | None = None) -> dict[str, Any]:
    """Run the daily top-picks pipeline and publish static artifacts."""
    run_date = date.today()
    DatabasePool.initialize()
    try:
        stocks = store.active_stock_metadata()
        if not stocks:
            logger.warning("phase1_no_active_stocks")
            return {"statusCode": 200, "body": "No active stocks configured"}

        scores = score_candidates(stocks, run_date)
        shortlist = select_shortlist(scores)
        analyses = analyze_shortlist(shortlist, stocks, run_date)
        payload = build_publication_payload(analyses, scores, stocks, run_date)
        publish_payload(payload, run_date)

        _emit_metric("candidates_scored", len(scores))
        _emit_metric("ai_candidates_analyzed", len(analyses))
        _emit_metric("top_picks_published", len(payload["top_picks"]))
        _emit_metric("sell_alerts_published", len(payload["sell_alerts"]))

        return {
            "statusCode": 200,
            "body": (
                f"Published {len(payload['top_picks'])} top picks and "
                f"{len(payload['sell_alerts'])} sell alerts"
            ),
        }
    finally:
        DatabasePool.close()


def score_candidates(stocks: list[dict[str, Any]], run_date: date) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = []
    for stock in stocks:
        ticker = stock["ticker"]
        signals: list[dict[str, Any]] = []
        signals.extend(_price_volume_signals(ticker, run_date))
        signals.extend(_news_signals(ticker, run_date))
        signals.extend(_event_signals(ticker, run_date))
        signals.extend(_options_signals(ticker))
        signals.extend(_analyst_signals(ticker))
        signals.extend(_insider_signals(ticker))
        signals.extend(_institutional_signals(ticker))
        signals.extend(_sector_relative_signals(stock, run_date))

        opportunity_score = sum(max(0, signal["score"]) for signal in signals)
        negative_score = abs(sum(min(0, signal["score"]) for signal in signals))
        score = {
            "ticker": ticker,
            "score_date": run_date.isoformat(),
            "opportunity_score": opportunity_score,
            "negative_score": negative_score,
            "signals": signals,
            "created_at": datetime.utcnow().isoformat(),
        }
        store.put_candidate_score(score)
        scores.append(score)
    return scores


def select_shortlist(scores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sell_alert_tickers = set(store.sell_alert_tickers())
    ranked = sorted(
        scores,
        key=lambda row: max(
            row["opportunity_score"],
            row["negative_score"] + (100 if row["ticker"] in sell_alert_tickers else 0),
        ),
        reverse=True,
    )
    return ranked[:SHORTLIST_SIZE]


def analyze_shortlist(
    shortlist: list[dict[str, Any]], stocks: list[dict[str, Any]], run_date: date
) -> list[dict[str, Any]]:
    stock_map = {stock["ticker"]: stock for stock in stocks}
    client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
    analyses = []
    for score in shortlist:
        stock = stock_map[score["ticker"]]
        analysis = _analyze_candidate(client, stock, score, run_date)
        store.put_candidate_analysis(analysis)
        analyses.append(analysis)
    return analyses


def build_publication_payload(
    analyses: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    stocks: list[dict[str, Any]],
    run_date: date,
) -> dict[str, Any]:
    stock_map = {stock["ticker"]: stock for stock in stocks}
    sell_watch = set(store.sell_alert_tickers())

    buy_candidates = [
        row
        for row in analyses
        if row["recommendation"] == "BUY" and row["opportunity_score"] > 0
    ]
    buy_candidates.sort(
        key=lambda row: (
            row["confidence_score"],
            row["opportunity_score"],
            -_risk_weight(row["risk_level"]),
        ),
        reverse=True,
    )

    top_picks = []
    for rank, row in enumerate(buy_candidates[:TOP_PICK_COUNT], start=1):
        stock = stock_map[row["ticker"]]
        top_picks.append(
            {
                "rank": rank,
                "ticker": row["ticker"],
                "company_name": stock["company_name"],
                "sector": stock["sector"],
                "recommendation": row["recommendation"],
                "risk_level": row["risk_level"],
                "confidence_score": row["confidence_score"],
                "catalyst": row["catalyst"],
                "expected_timeframe": row["expected_timeframe"],
                "rationale": row["reasoning"],
                "invalidation_criteria": row["invalidation_criteria"],
                "supporting_evidence": _evidence(row["signals"]),
                "source_traceability": [signal["source"] for signal in row["signals"][:5]],
            }
        )

    sell_candidates = [
        row
        for row in analyses
        if row["negative_score"] >= 40
        and (row["ticker"] in sell_watch or row["recommendation"] == "SELL")
    ]
    sell_candidates.sort(
        key=lambda row: (row["negative_score"], row["confidence_score"]), reverse=True
    )

    sell_alerts = []
    for rank, row in enumerate(sell_candidates[:TOP_PICK_COUNT], start=1):
        stock = stock_map[row["ticker"]]
        sell_alerts.append(
            {
                "rank": rank,
                "ticker": row["ticker"],
                "company_name": stock["company_name"],
                "sector": stock["sector"],
                "severity": "critical" if row["negative_score"] >= 80 else "high",
                "risk_level": row["risk_level"],
                "confidence_score": row["confidence_score"],
                "negative_catalyst": row["catalyst"],
                "rationale": row["reasoning"],
                "supporting_evidence": _evidence(row["signals"]),
                "source_traceability": [signal["source"] for signal in row["signals"][:5]],
            }
        )

    data_warnings = _source_warnings(scores)
    return {
        "publication_date": run_date.isoformat(),
        "generated_at": datetime.utcnow().isoformat(),
        "top_picks": top_picks,
        "sell_alerts": sell_alerts,
        "candidate_count": len(scores),
        "analyzed_count": len(analyses),
        "data_warnings": data_warnings,
    }


def publish_payload(payload: dict[str, Any], run_date: date) -> None:
    if not ARTIFACT_BUCKET:
        logger.warning("artifact_bucket_not_configured")
        return

    s3 = boto3.client("s3")
    body = json.dumps(payload, indent=2, default=str).encode("utf-8")
    keys = [
        "top-picks/latest.json",
        f"top-picks/history/{run_date.isoformat()}.json",
    ]
    sell_body = json.dumps(
        {
            "publication_date": payload["publication_date"],
            "generated_at": payload["generated_at"],
            "sell_alerts": payload["sell_alerts"],
            "data_warnings": payload["data_warnings"],
        },
        indent=2,
        default=str,
    ).encode("utf-8")
    sell_keys = [
        "sell-alerts/latest.json",
        f"sell-alerts/history/{run_date.isoformat()}.json",
    ]

    for key in keys:
        s3.put_object(
            Bucket=ARTIFACT_BUCKET,
            Key=key,
            Body=body,
            ContentType="application/json",
            CacheControl="public, max-age=300",
        )
    for key in sell_keys:
        s3.put_object(
            Bucket=ARTIFACT_BUCKET,
            Key=key,
            Body=sell_body,
            ContentType="application/json",
            CacheControl="public, max-age=300",
        )
    store.put_publication_record(run_date, payload)


def _analyze_candidate(
    client: OpenAI | None, stock: dict[str, Any], score: dict[str, Any], run_date: date
) -> dict[str, Any]:
    if client is None:
        return _fallback_analysis(stock, score, run_date)

    prompt = _build_prompt(stock, score)
    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a stock catalyst analyst. Return concise JSON for "
                        "a near-term opportunity and risk assessment. This is not "
                        "financial advice."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.25,
            max_tokens=500,
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        return _normalize_ai_analysis(stock, score, parsed, run_date)
    except Exception as exc:
        logger.warning("candidate_ai_analysis_failed", ticker=stock["ticker"], error=str(exc))
        return _fallback_analysis(stock, score, run_date)


def _fallback_analysis(
    stock: dict[str, Any], score: dict[str, Any], run_date: date
) -> dict[str, Any]:
    recommendation = "HOLD"
    if score["negative_score"] >= 60:
        recommendation = "SELL"
    elif score["opportunity_score"] >= 40 and score["opportunity_score"] >= score["negative_score"]:
        recommendation = "BUY"

    risk_level = "HIGH" if score["negative_score"] >= 60 else "MEDIUM"
    if score["negative_score"] < 25 and score["opportunity_score"] >= 60:
        risk_level = "LOW"

    catalyst = _primary_signal(score["signals"])
    confidence = min(95, max(35, 45 + max(score["opportunity_score"], score["negative_score"]) // 2))
    return {
        "ticker": stock["ticker"],
        "analysis_date": run_date.isoformat(),
        "recommendation": recommendation,
        "risk_level": risk_level,
        "confidence_score": confidence,
        "catalyst": catalyst["title"] if catalyst else "No dominant catalyst",
        "expected_timeframe": "1-30 days",
        "reasoning": catalyst["summary"] if catalyst else "Candidate scored from available Phase 1 signals.",
        "invalidation_criteria": "Signal strength fades, news reverses, or price/volume confirmation fails.",
        "opportunity_score": int(score["opportunity_score"]),
        "negative_score": int(score["negative_score"]),
        "signals": score["signals"],
        "created_at": datetime.utcnow().isoformat(),
    }


def _normalize_ai_analysis(
    stock: dict[str, Any], score: dict[str, Any], parsed: dict[str, Any], run_date: date
) -> dict[str, Any]:
    recommendation = str(parsed.get("recommendation", "HOLD")).upper()
    if recommendation not in {"BUY", "HOLD", "SELL"}:
        recommendation = "HOLD"
    risk_level = str(parsed.get("risk_level", "MEDIUM")).upper()
    if risk_level not in {"LOW", "MEDIUM", "HIGH"}:
        risk_level = "MEDIUM"
    confidence = int(parsed.get("confidence_score", 50))
    return {
        "ticker": stock["ticker"],
        "analysis_date": run_date.isoformat(),
        "recommendation": recommendation,
        "risk_level": risk_level,
        "confidence_score": max(0, min(100, confidence)),
        "catalyst": str(parsed.get("catalyst", _primary_signal(score["signals"])["title"])),
        "expected_timeframe": str(parsed.get("expected_timeframe", "1-30 days")),
        "reasoning": str(parsed.get("reasoning", ""))[:1000],
        "invalidation_criteria": str(parsed.get("invalidation_criteria", ""))[:500],
        "opportunity_score": int(score["opportunity_score"]),
        "negative_score": int(score["negative_score"]),
        "signals": score["signals"],
        "created_at": datetime.utcnow().isoformat(),
    }


def _build_prompt(stock: dict[str, Any], score: dict[str, Any]) -> str:
    signals = "\n".join(
        f"- {s['signal_type']} {s['direction']} score={s['score']}: {s['summary']}"
        for s in score["signals"][:12]
    )
    return f"""Analyze {stock['ticker']} ({stock['company_name']}, {stock['sector']}).

Opportunity score: {score['opportunity_score']}
Negative score: {score['negative_score']}

Signals:
{signals}

Return JSON with keys:
recommendation: BUY, HOLD, or SELL
risk_level: LOW, MEDIUM, or HIGH
confidence_score: integer 0-100
catalyst: short phrase
expected_timeframe: e.g. 1-7 days, 1-30 days, 1-90 days
reasoning: 2-3 concise sentences
invalidation_criteria: concise condition that would invalidate the thesis
"""


def _price_volume_signals(ticker: str, run_date: date) -> list[dict[str, Any]]:
    rows = store.get_stock_data(ticker, run_date - timedelta(days=45), run_date)
    if len(rows) < 2:
        return []
    latest = rows[-1]
    previous = rows[-2]
    close = Decimal(str(latest["close_price"]))
    prev_close = Decimal(str(previous["close_price"]))
    if prev_close <= 0:
        return []
    price_change = float((close - prev_close) / prev_close * 100)
    avg_volume = sum(int(row["volume"]) for row in rows[:-1]) / max(1, len(rows) - 1)
    volume_ratio = int(latest["volume"]) / avg_volume if avg_volume else 1

    signals = []
    if abs(price_change) >= 3:
        signals.append(
            _signal(
                ticker,
                "price_move",
                "positive" if price_change > 0 else "negative",
                int(max(-45, min(45, price_change * 6))),
                "Large daily price move",
                f"{ticker} moved {price_change:.2f}% versus the prior close.",
                "yfinance",
            )
        )
    if volume_ratio >= 1.8:
        signals.append(
            _signal(
                ticker,
                "volume_move",
                "positive" if price_change >= 0 else "negative",
                25 if price_change >= 0 else -25,
                "Unusual volume",
                f"{ticker} traded at {volume_ratio:.1f}x its recent average volume.",
                "yfinance",
            )
        )
    return signals


def _news_signals(ticker: str, run_date: date) -> list[dict[str, Any]]:
    articles = store.news_for_ticker(ticker, run_date - timedelta(days=7), run_date)
    if not articles:
        return []
    score = min(30, len(articles) * 5)
    text = " ".join((article.get("title", "") + " " + article.get("summary", "")).lower() for article in articles)
    negative_hits = sum(1 for keyword in NEGATIVE_KEYWORDS if keyword in text)
    positive_hits = sum(1 for keyword in POSITIVE_KEYWORDS if keyword in text)
    if negative_hits > positive_hits:
        direction = "negative"
        score = -max(score, 35 + negative_hits * 10)
        title = "Negative news catalyst"
    elif positive_hits > 0:
        direction = "positive"
        score = max(score, 30 + positive_hits * 8)
        title = "Positive news catalyst"
    else:
        direction = "neutral"
        title = "Elevated news momentum"
    return [
        _signal(
            ticker,
            "news",
            direction,
            score,
            title,
            f"{len(articles)} recent ticker-related article(s); latest: {articles[0]['title']}",
            "news",
            {"article_count": len(articles)},
        )
    ]


def _event_signals(ticker: str, run_date: date) -> list[dict[str, Any]]:
    signals = []
    try:
        info = yf.Ticker(ticker)
        calendar = getattr(info, "calendar", None)
        if calendar is not None and not getattr(calendar, "empty", True):
            text = str(calendar)
            signals.append(
                _signal(
                    ticker,
                    "earnings",
                    "positive",
                    20,
                    "Upcoming earnings calendar signal",
                    f"Earnings/calendar data is available for {ticker}.",
                    "yfinance",
                    {"calendar": text[:500]},
                )
            )
        dividends = getattr(info, "dividends", None)
        if dividends is not None and len(dividends) > 0:
            last_dividend = dividends.tail(1)
            signals.append(
                _signal(
                    ticker,
                    "dividend",
                    "positive",
                    10,
                    "Dividend history signal",
                    f"{ticker} has dividend data available; latest record {last_dividend.to_dict()}.",
                    "yfinance",
                )
            )
    except Exception as exc:
        logger.info("event_signal_provider_unavailable", ticker=ticker, error=str(exc))
    return signals


def _options_signals(ticker: str) -> list[dict[str, Any]]:
    try:
        yf_ticker = yf.Ticker(ticker)
        expirations = list(getattr(yf_ticker, "options", []) or [])
        if expirations:
            return [
                _signal(
                    ticker,
                    "options",
                    "neutral",
                    10,
                    "Options activity available",
                    f"{ticker} has listed options expirations; use as a volatility follow-up signal.",
                    "yfinance",
                    {"expiration_count": len(expirations)},
                )
            ]
    except Exception as exc:
        logger.info("options_signal_provider_unavailable", ticker=ticker, error=str(exc))
    return []


def _analyst_signals(ticker: str) -> list[dict[str, Any]]:
    try:
        recs = yf.Ticker(ticker).recommendations
        if recs is not None and len(recs) > 0:
            return [
                _signal(
                    ticker,
                    "analyst",
                    "neutral",
                    12,
                    "Analyst rating activity",
                    f"{ticker} has recent analyst recommendation data available.",
                    "yfinance",
                )
            ]
    except Exception as exc:
        logger.info("analyst_signal_provider_unavailable", ticker=ticker, error=str(exc))
    return []


def _insider_signals(ticker: str) -> list[dict[str, Any]]:
    try:
        txns = yf.Ticker(ticker).insider_transactions
        if txns is not None and len(txns) > 0:
            return [
                _signal(
                    ticker,
                    "insider",
                    "neutral",
                    10,
                    "Insider transaction activity",
                    f"{ticker} has insider transaction data available.",
                    "yfinance",
                )
            ]
    except Exception as exc:
        logger.info("insider_signal_provider_unavailable", ticker=ticker, error=str(exc))
    return []


def _institutional_signals(ticker: str) -> list[dict[str, Any]]:
    try:
        holders = yf.Ticker(ticker).institutional_holders
        if holders is not None and len(holders) > 0:
            return [
                _signal(
                    ticker,
                    "institutional",
                    "neutral",
                    8,
                    "Institutional holder data",
                    f"{ticker} has institutional holder data available.",
                    "yfinance",
                )
            ]
    except Exception as exc:
        logger.info("institutional_signal_provider_unavailable", ticker=ticker, error=str(exc))
    return []


def _sector_relative_signals(stock: dict[str, Any], run_date: date) -> list[dict[str, Any]]:
    ticker = stock["ticker"]
    sector_etf = SECTOR_ETFS.get(stock["sector"])
    if not sector_etf:
        return []
    stock_rows = store.get_stock_data(ticker, run_date - timedelta(days=7), run_date)
    if len(stock_rows) < 2:
        return []
    try:
        etf = yf.download(sector_etf, period="5d", progress=False, timeout=10)
        if etf is None or etf.empty:
            return []
        stock_move = _pct_move(stock_rows[-2]["close_price"], stock_rows[-1]["close_price"])
        etf_close = etf["Close"]
        etf_move = _pct_move(etf_close.iloc[-2], etf_close.iloc[-1])
        relative = stock_move - etf_move
        if abs(relative) < 2:
            return []
        return [
            _signal(
                ticker,
                "sector_relative",
                "positive" if relative > 0 else "negative",
                int(max(-30, min(30, relative * 5))),
                "Sector-relative movement",
                f"{ticker} moved {relative:.2f}% relative to {sector_etf}.",
                "yfinance",
                {"sector_etf": sector_etf},
            )
        ]
    except Exception as exc:
        logger.info("sector_signal_provider_unavailable", ticker=ticker, error=str(exc))
        return []


def _signal(
    ticker: str,
    signal_type: str,
    direction: str,
    score: int,
    title: str,
    summary: str,
    provider: str,
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "signal_type": signal_type,
        "direction": direction,
        "score": int(max(-100, min(100, score))),
        "title": title,
        "summary": summary,
        "source": {
            "provider": provider,
            "observed_at": datetime.utcnow().isoformat(),
            "raw": raw or {},
        },
    }


def _pct_move(previous: Any, current: Any) -> float:
    previous_dec = Decimal(str(previous))
    current_dec = Decimal(str(current))
    if previous_dec <= 0:
        return 0.0
    return float((current_dec - previous_dec) / previous_dec * 100)


def _primary_signal(signals: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not signals:
        return None
    return max(signals, key=lambda signal: abs(signal["score"]))


def _evidence(signals: list[dict[str, Any]]) -> list[str]:
    return [signal["summary"] for signal in sorted(signals, key=lambda s: abs(s["score"]), reverse=True)[:5]]


def _risk_weight(risk_level: str) -> int:
    return {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(risk_level, 2)


def _source_warnings(scores: list[dict[str, Any]]) -> list[str]:
    if not any(signal["signal_type"] == "earnings" for score in scores for signal in score["signals"]):
        return ["No earnings-calendar signals were available from configured providers."]
    return []


def _emit_metric(metric_name: str, value: float) -> None:
    try:
        boto3.client("cloudwatch").put_metric_data(
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
    except Exception as exc:
        logger.warning("metric_emit_failed", metric=metric_name, error=str(exc))
