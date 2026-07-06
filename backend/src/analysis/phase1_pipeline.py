"""Phase 1 candidate scanning, AI analysis, ranking, and static publishing."""

import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
from openai import OpenAI
import structlog
import yfinance as yf

from src.db.connection import DatabasePool, store
from src.models.schemas import CollectionManifest, collection_manifest_s3_key
from src.services.secrets import get_openai_api_key

logger = structlog.get_logger(__name__)

OPENAI_ANALYSIS_MODEL = os.environ.get(
    "OPENAI_ANALYSIS_MODEL",
    os.environ.get("OPENAI_MODEL", "gpt-5.4-mini"),
)
OPENAI_REVIEW_MODEL = os.environ.get("OPENAI_REVIEW_MODEL", "gpt-5.4")
ARTIFACT_BUCKET = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "")
SHORTLIST_SIZE = int(os.environ.get("PHASE1_SHORTLIST_SIZE", "50"))
TOP_PICK_COUNT = int(os.environ.get("PHASE1_TOP_PICK_COUNT", "10"))
ANALYSIS_BATCH_SIZE = int(os.environ.get("PHASE1_ANALYSIS_BATCH_SIZE", "5"))
PRICE_CHART_LOOKBACK_DAYS = int(os.environ.get("PHASE1_PRICE_CHART_LOOKBACK_DAYS", "60"))
PRICE_CHART_MAX_ROWS = int(os.environ.get("PHASE1_PRICE_CHART_MAX_ROWS", "45"))
PRICE_CHART_SMA_WINDOW = 20
RELATED_NEWS_LOOKBACK_DAYS = int(os.environ.get("PHASE1_RELATED_NEWS_LOOKBACK_DAYS", "7"))
RELATED_NEWS_MAX_ARTICLES = int(os.environ.get("PHASE1_RELATED_NEWS_MAX_ARTICLES", "5"))
STOCK_FRESHNESS_MAX_AGE_DAYS = int(os.environ.get("PHASE1_STOCK_FRESHNESS_MAX_AGE_DAYS", "3"))
MIN_HISTORY_CALENDAR_DAYS = int(os.environ.get("PHASE1_MIN_HISTORY_CALENDAR_DAYS", "30"))
MIN_HISTORY_ROWS = int(os.environ.get("PHASE1_MIN_HISTORY_ROWS", "20"))
NEWS_FRESHNESS_MAX_HOURS = int(os.environ.get("PHASE1_NEWS_FRESHNESS_MAX_HOURS", "26"))
EARNINGS_LOOKAHEAD_DAYS = int(os.environ.get("PHASE1_EARNINGS_LOOKAHEAD_DAYS", "45"))
EARNINGS_HISTORY_DAYS = int(os.environ.get("PHASE1_EARNINGS_HISTORY_DAYS", "730"))
DIVIDEND_LOOKAHEAD_DAYS = int(os.environ.get("PHASE1_DIVIDEND_LOOKAHEAD_DAYS", "60"))
DIVIDEND_HISTORY_DAYS = int(os.environ.get("PHASE1_DIVIDEND_HISTORY_DAYS", "730"))
CLOUDWATCH_NAMESPACE = "StockaraPhase1"
FALLBACK_CONFIDENCE_CAP = int(os.environ.get("PHASE1_FALLBACK_CONFIDENCE_CAP", "55"))
ALLOW_FALLBACK_ACTIONABLE_RECOMMENDATIONS = (
    os.environ.get("PHASE1_ALLOW_FALLBACK_ACTIONABLE_RECOMMENDATIONS", "false").lower()
    == "true"
)
ENABLE_LIVE_SCORING_PROVIDER_SIGNALS = (
    os.environ.get("PHASE1_ENABLE_LIVE_SCORING_PROVIDER_SIGNALS", "false").lower()
    == "true"
)
DECISION_GRADE_REQUIRED_METADATA_FIELDS = (
    "ticker",
    "company_name",
    "sector",
    "industry",
    "company_size",
    "source",
    "metadata_source",
    "metadata_source_url",
    "metadata_as_of",
)

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

STORED_SIGNAL_TYPES = {
    "price_move",
    "volume_move",
    "sec_filing",
    "analyst_action",
    "analyst_rating",
    "price_target",
    "earnings_release",
    "earnings_transcript",
    "sector_context",
    "macro_context",
}


def run_phase1_pipeline(event: dict | None = None) -> dict[str, Any]:
    """Run the daily top-picks pipeline and publish static artifacts."""
    event = event or {}
    run_date = date.today()
    DatabasePool.initialize()
    try:
        mode = str(event.get("mode", "full"))
        if mode == "score":
            return _run_score_phase(event, run_date)
        if mode == "analyze_batch":
            return _run_analyze_batch_phase(event, run_date)
        if mode == "publish":
            return _run_publish_phase(run_date)
        if mode == "daily":
            return _run_daily_orchestration_phase(event, run_date)
        if mode != "full":
            return {"statusCode": 400, "body": f"Unsupported Phase 1 mode: {mode}"}
        return _run_full_phase(run_date)
    finally:
        DatabasePool.close()


def _run_full_phase(run_date: date) -> dict[str, Any]:
    gate_response = _collection_gate_response(run_date, publish_status_artifact=True)
    if gate_response:
        return gate_response

    context = _eligible_context(run_date)
    if context.get("response"):
        _publish_suppressed_context_if_possible(run_date, context)
        return context["response"]

    eligible_stocks = context["eligible_stocks"]
    freshness = context["freshness"]
    scores = score_candidates(eligible_stocks, run_date)
    shortlist = select_shortlist(scores)
    analyses = analyze_shortlist(shortlist, eligible_stocks, run_date)
    payload = build_publication_payload(
        analyses,
        scores,
        eligible_stocks,
        run_date,
        data_quality=_with_collection_manifest_quality(
            publication_data_quality(freshness),
            run_date,
        ),
        upcoming_earnings=upcoming_earnings_summary(run_date),
        upcoming_dividends=upcoming_dividends_summary(run_date),
    )
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


def _run_score_phase(event: dict[str, Any], run_date: date) -> dict[str, Any]:
    gate_response = _collection_gate_response(run_date)
    if gate_response:
        return gate_response

    context = _eligible_context(run_date)
    if context.get("response"):
        return context["response"]

    eligible_stocks = context["eligible_stocks"]
    batch_index = event.get("batch_index")
    batch_size = event.get("batch_size")
    if batch_index is not None or batch_size is not None:
        batch_index = int(batch_index or 0)
        batch_size = int(batch_size or 100)
        if batch_index < 0 or batch_size < 1:
            return {"statusCode": 400, "body": "batch_index must be >= 0 and batch_size must be >= 1"}
        start = batch_index * batch_size
        stocks_to_score = eligible_stocks[start : start + batch_size]
    else:
        batch_index = None
        batch_size = None
        start = 0
        stocks_to_score = eligible_stocks

    scores = score_candidates(stocks_to_score, run_date)
    shortlist = select_shortlist(scores)
    _emit_metric("candidates_scored", len(scores))
    body: dict[str, Any] = {
        "mode": "score",
        "candidate_count": len(scores),
        "eligible_count": len(eligible_stocks),
        "shortlist_count": len(shortlist),
        "shortlisted_tickers": [score["ticker"] for score in shortlist],
    }
    if batch_index is not None and batch_size is not None:
        body.update(
            {
                "batch_index": batch_index,
                "batch_size": batch_size,
                "batch_start": start,
                "batch_end": min(start + batch_size, len(eligible_stocks)),
            }
        )
    return {
        "statusCode": 200,
        "body": body,
    }


def _run_analyze_batch_phase(event: dict[str, Any], run_date: date) -> dict[str, Any]:
    batch_index = int(event.get("batch_index", 0))
    batch_size = int(event.get("batch_size", 5))
    if batch_index < 0 or batch_size < 1:
        return {"statusCode": 400, "body": "batch_index must be >= 0 and batch_size must be >= 1"}

    scores = store.candidate_scores_for_date(run_date)
    if not scores:
        return {"statusCode": 200, "body": "No candidate scores available for analysis"}

    shortlist = select_shortlist(scores)
    start = batch_index * batch_size
    batch = shortlist[start : start + batch_size]
    if not batch:
        return {
            "statusCode": 200,
            "body": {
                "mode": "analyze_batch",
                "batch_index": batch_index,
                "batch_size": batch_size,
                "analyzed_count": 0,
                "shortlist_count": len(shortlist),
            },
        }

    stocks = store.active_stock_metadata()
    if not stocks:
        logger.warning("phase1_no_active_stocks")
        return {"statusCode": 200, "body": "No active stocks configured"}

    analyses = analyze_shortlist(batch, stocks, run_date)
    _emit_metric("ai_candidates_analyzed", len(analyses))
    return {
        "statusCode": 200,
        "body": {
            "mode": "analyze_batch",
            "batch_index": batch_index,
            "batch_size": batch_size,
            "analyzed_count": len(analyses),
            "shortlist_count": len(shortlist),
            "analyzed_tickers": [analysis["ticker"] for analysis in analyses],
        },
    }


def _run_daily_orchestration_phase(
    event: dict[str, Any],
    run_date: date,
) -> dict[str, Any]:
    """Advance the daily pipeline once collection gates are open.

    This mode is safe for frequent EventBridge invocation: it gates on the
    manifest, scores once, analyzes only missing shortlisted candidates in
    bounded batches, publishes once, then no-ops for the rest of the day.
    """
    gate_response = _collection_gate_response(run_date, publish_status_artifact=True)
    if gate_response:
        return gate_response

    if _publication_exists_for_date(run_date):
        return {
            "statusCode": 200,
            "body": {
                "mode": "daily",
                "stage": "already_published",
                "publication_date": run_date.isoformat(),
            },
        }

    context = _eligible_context(run_date)
    if context.get("response"):
        _publish_suppressed_context_if_possible(run_date, context)
        return context["response"]

    scores = store.candidate_scores_for_date(run_date)
    if not scores:
        scores = score_candidates(context["eligible_stocks"], run_date)
        shortlist = select_shortlist(scores)
        _emit_metric("candidates_scored", len(scores))
        return {
            "statusCode": 200,
            "body": {
                "mode": "daily",
                "stage": "scored",
                "candidate_count": len(scores),
                "shortlist_count": len(shortlist),
                "shortlisted_tickers": [score["ticker"] for score in shortlist],
            },
        }

    shortlist = select_shortlist(scores)
    existing_analyses = store.candidate_analysis_for_date(run_date)
    remaining = _remaining_shortlist_scores(shortlist, existing_analyses)
    if remaining:
        batch_size = int(event.get("batch_size", ANALYSIS_BATCH_SIZE))
        batch = remaining[: max(batch_size, 1)]
        analyses = analyze_shortlist(batch, context["eligible_stocks"], run_date)
        _emit_metric("ai_candidates_analyzed", len(analyses))
        return {
            "statusCode": 200,
            "body": {
                "mode": "daily",
                "stage": "analyzed_batch",
                "batch_size": len(batch),
                "analyzed_count": len(analyses),
                "remaining_shortlist_count": max(len(remaining) - len(batch), 0),
                "analyzed_tickers": [analysis["ticker"] for analysis in analyses],
            },
        }

    return _publish_from_stored_state(run_date, context, scores, existing_analyses)


def _run_publish_phase(run_date: date) -> dict[str, Any]:
    gate_response = _collection_gate_response(run_date, publish_status_artifact=True)
    if gate_response:
        return gate_response

    context = _eligible_context(run_date)
    if context.get("response"):
        _publish_suppressed_context_if_possible(run_date, context)
        return context["response"]

    scores = store.candidate_scores_for_date(run_date)
    analyses = store.candidate_analysis_for_date(run_date)
    return _publish_from_stored_state(run_date, context, scores, analyses)


def _publish_from_stored_state(
    run_date: date,
    context: dict[str, Any],
    scores: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
) -> dict[str, Any]:
    shortlist_tickers = {score["ticker"] for score in select_shortlist(scores)}
    analyses = [
        analysis for analysis in analyses if analysis.get("ticker") in shortlist_tickers
    ]
    if not analyses:
        logger.warning("phase1_publication_suppressed_no_candidate_analyses")
        _emit_metric("publication_suppressed", 1)
        _publish_suppressed_publication(
            run_date,
            reason="no_candidate_analyses",
            warnings=["Publication suppressed: no candidate analyses available."],
            data_quality=_with_collection_manifest_quality(
                publication_data_quality(context["freshness"]),
                run_date,
            ),
            candidate_count=len(scores),
        )
        return {
            "statusCode": 200,
            "body": "Publication suppressed: no candidate analyses available",
        }

    freshness = context["freshness"]
    payload = build_publication_payload(
        analyses,
        scores,
        context["eligible_stocks"],
        run_date,
        data_quality=_with_collection_manifest_quality(
            publication_data_quality(freshness),
            run_date,
        ),
        upcoming_earnings=upcoming_earnings_summary(run_date),
        upcoming_dividends=upcoming_dividends_summary(run_date),
    )
    publish_payload(payload, run_date)
    _emit_metric("top_picks_published", len(payload["top_picks"]))
    _emit_metric("sell_alerts_published", len(payload["sell_alerts"]))

    return {
        "statusCode": 200,
        "body": (
            f"Published {len(payload['top_picks'])} top picks and "
            f"{len(payload['sell_alerts'])} sell alerts"
        ),
    }


def _remaining_shortlist_scores(
    shortlist: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    analyzed_tickers = {analysis.get("ticker") for analysis in analyses}
    return [score for score in shortlist if score.get("ticker") not in analyzed_tickers]


def _publication_exists_for_date(run_date: date) -> bool:
    last_publication = _parse_datetime(store.last_publication())
    return bool(last_publication and last_publication.date() == run_date)


def _collection_gate_response(
    run_date: date,
    publish_status_artifact: bool = False,
) -> dict[str, Any] | None:
    """Log collection coverage diagnostics without blocking best-available picks."""
    if not ARTIFACT_BUCKET:
        return None
    manifest = _load_collection_manifest(run_date)
    if manifest is None:
        logger.warning("phase1_collection_manifest_missing", run_date=run_date.isoformat())
        return None
    failed_gates = [gate for gate in manifest.summary.coverage_gates if not gate.passed]
    if not failed_gates:
        return None
    logger.warning(
        "phase1_collection_coverage_targets_below_threshold",
        failed_gates=[gate.name for gate in failed_gates],
        manifest_key=manifest.s3_key,
    )
    _emit_metric("collection_coverage_targets_below_threshold", len(failed_gates))
    return None


def _load_collection_manifest(run_date: date) -> CollectionManifest | None:
    key = collection_manifest_s3_key(run_date)
    try:
        response = boto3.client("s3").get_object(Bucket=ARTIFACT_BUCKET, Key=key)
        payload = json.loads(response["Body"].read().decode("utf-8"))
        return CollectionManifest.model_validate(payload)
    except Exception as exc:
        logger.warning("collection_manifest_load_failed", key=key, error=str(exc))
        return None


def _with_collection_manifest_quality(
    data_quality: dict[str, Any],
    run_date: date,
) -> dict[str, Any]:
    if not ARTIFACT_BUCKET:
        return data_quality
    manifest = _load_collection_manifest(run_date)
    if manifest is None:
        return data_quality
    return {
        **data_quality,
        "collection_manifest": {
            "manifest_key": manifest.s3_key,
            "manifest_date": manifest.manifest_date.isoformat(),
            "updated_at": manifest.updated_at.isoformat(),
            "active_ticker_count": manifest.active_ticker_count,
            "summary": manifest.summary.model_dump(mode="json"),
        },
    }


def _eligible_context(run_date: date) -> dict[str, Any]:
    stocks = store.active_stock_metadata()
    if not stocks:
        logger.warning("phase1_no_active_stocks")
        return {"response": {"statusCode": 200, "body": "No active stocks configured"}}

    freshness = evaluate_data_freshness(stocks, run_date)
    _emit_metric("eligible_tickers", freshness["eligible_ticker_count"])
    _emit_metric("excluded_tickers", freshness["excluded_ticker_count"])
    if freshness["active_ticker_count"]:
        _emit_metric(
            "eligible_ticker_coverage_percent",
            freshness["eligible_ticker_count"] / freshness["active_ticker_count"] * 100,
        )

    eligible_stocks = freshness["eligible_stocks"]
    if not eligible_stocks:
        logger.warning(
            "phase1_publication_suppressed_no_eligible_tickers",
            active_ticker_count=freshness["active_ticker_count"],
            excluded_ticker_count=freshness["excluded_ticker_count"],
        )
        _emit_metric("publication_suppressed", 1)
        return {
            "freshness": freshness,
            "response": {
                "statusCode": 200,
                "body": "Publication suppressed: no eligible tickers passed data freshness gates",
            },
        }
    return {"stocks": stocks, "freshness": freshness, "eligible_stocks": eligible_stocks}


def _publish_suppressed_context_if_possible(
    run_date: date,
    context: dict[str, Any],
) -> None:
    freshness = context.get("freshness")
    if not freshness:
        return
    _publish_suppressed_publication(
        run_date,
        reason="no_eligible_tickers",
        warnings=[
            "Publication suppressed: no eligible tickers passed data freshness gates.",
            *freshness.get("warnings", []),
        ],
        data_quality=_with_collection_manifest_quality(
            publication_data_quality(freshness),
            run_date,
        ),
    )


def _publish_suppressed_publication(
    run_date: date,
    reason: str,
    warnings: list[str],
    data_quality: dict[str, Any] | None = None,
    candidate_count: int = 0,
) -> None:
    if not ARTIFACT_BUCKET:
        return
    payload = {
        "publication_date": run_date.isoformat(),
        "generated_at": datetime.utcnow().isoformat(),
        "publication_status": "suppressed",
        "suppression_reason": reason,
        "publication_scope": "top_opportunities_among_eligible_tickers",
        "fallback_policy": {},
        "review_policy": {},
        "review_rejections": [],
        "top_picks": [],
        "sell_alerts": [],
        "upcoming_earnings": [],
        "upcoming_dividends": [],
        "candidate_count": candidate_count,
        "analyzed_count": 0,
        "data_quality": data_quality or {},
        "data_warnings": warnings,
    }
    publish_payload(payload, run_date)


def score_candidates(stocks: list[dict[str, Any]], run_date: date) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = []
    for stock in stocks:
        ticker = stock["ticker"]
        signals: list[dict[str, Any]] = []
        signals.extend(_price_volume_signals(ticker, run_date))
        signals.extend(_news_signals(ticker, run_date))
        signals.extend(_event_signals(ticker, run_date))
        if ENABLE_LIVE_SCORING_PROVIDER_SIGNALS:
            signals.extend(_options_signals(ticker))
            signals.extend(_analyst_signals(ticker))
            signals.extend(_insider_signals(ticker))
            signals.extend(_institutional_signals(ticker))
            signals.extend(_fundamental_signals(ticker))
            signals.extend(_sector_relative_signals(stock, run_date))

        scored_signals = _scored_evidence_signals(signals)
        opportunity_score = sum(max(0, signal["score"]) for signal in scored_signals)
        negative_score = abs(sum(min(0, signal["score"]) for signal in scored_signals))
        score = {
            "ticker": ticker,
            "score_date": run_date.isoformat(),
            "opportunity_score": opportunity_score,
            "negative_score": negative_score,
            "signals": signals,
            "scored_signal_count": len(scored_signals),
            "context_signal_count": len(signals) - len(scored_signals),
            "created_at": datetime.utcnow().isoformat(),
        }
        store.put_candidate_score(score)
        scores.append(score)
    return scores


def evaluate_data_freshness(
    stocks: list[dict[str, Any]], run_date: date
) -> dict[str, Any]:
    """Evaluate ticker-level data freshness before scoring or publication.

    Phase 1 may publish from a partial universe, but each published ticker must
    have fresh stock data and enough historical context for decision-grade use.
    """
    eligible_stocks: list[dict[str, Any]] = []
    excluded_tickers: list[dict[str, Any]] = []
    warnings: list[str] = []
    freshness_cutoff = run_date - timedelta(days=STOCK_FRESHNESS_MAX_AGE_DAYS)
    history_cutoff = run_date - timedelta(days=MIN_HISTORY_CALENDAR_DAYS)

    for stock in stocks:
        ticker = stock["ticker"]
        reasons: list[str] = []
        rows: list[dict[str, Any]] = []
        missing_metadata_fields = _decision_grade_metadata_gaps(stock)
        latest_data_date = _parse_date(stock.get("latest_stock_data_date"))
        history_start_date: date | None = None

        if missing_metadata_fields:
            excluded_tickers.append(
                {
                    "ticker": ticker,
                    "reasons": ["unresolved_watchlist_metadata"],
                    "missing_metadata_fields": missing_metadata_fields,
                    "latest_stock_data_date": latest_data_date.isoformat()
                    if latest_data_date
                    else None,
                    "history_start_date": None,
                    "history_row_count": 0,
                }
            )
            continue

        try:
            rows = store.get_stock_data(
                ticker,
                run_date - timedelta(days=max(MIN_HISTORY_CALENDAR_DAYS + 15, 45)),
                run_date,
            )
        except Exception as exc:
            reasons.append("stock_data_lookup_failed")
            logger.warning("stock_freshness_lookup_failed", ticker=ticker, error=str(exc))

        row_dates = [
            parsed
            for parsed in (_parse_date(row.get("trading_date")) for row in rows)
            if parsed is not None
        ]
        latest_row: dict[str, Any] | None = None
        if row_dates:
            latest_data_date = max(latest_data_date or row_dates[-1], max(row_dates))
            history_start_date = min(row_dates)
            latest_row = max(
                rows,
                key=lambda row: _parse_date(row.get("trading_date")) or date.min,
            )

        if latest_data_date is None:
            reasons.append("missing_stock_data")
        elif latest_data_date < freshness_cutoff:
            reasons.append("stale_stock_data")

        if not row_dates:
            reasons.append("missing_stock_history")
        else:
            if latest_row and not _has_market_data_provenance(latest_row):
                reasons.append("missing_market_data_provenance")
            if len(row_dates) < MIN_HISTORY_ROWS:
                reasons.append("insufficient_stock_history_rows")
            if history_start_date is None or history_start_date > history_cutoff:
                reasons.append("insufficient_stock_history_span")

        if reasons:
            excluded_tickers.append(
                {
                    "ticker": ticker,
                    "reasons": sorted(set(reasons)),
                    "latest_stock_data_date": latest_data_date.isoformat()
                    if latest_data_date
                    else None,
                    "history_start_date": history_start_date.isoformat()
                    if history_start_date
                    else None,
                    "history_row_count": len(row_dates),
                }
            )
        else:
            enriched = {
                **stock,
                "latest_stock_data_date": latest_data_date.isoformat(),
                "stock_history_start_date": history_start_date.isoformat()
                if history_start_date
                else None,
                "stock_history_row_count": len(row_dates),
            }
            eligible_stocks.append(enriched)

    last_news_collection = store.last_news_collection()
    news_collected_at = _parse_datetime(last_news_collection)
    news_stale = False
    if news_collected_at is None:
        warnings.append("News freshness is unknown; no collection timestamp is available.")
        news_stale = True
    else:
        run_datetime = datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
        max_age = timedelta(hours=NEWS_FRESHNESS_MAX_HOURS)
        if news_collected_at < run_datetime - max_age:
            warnings.append("News collection is stale for the publication window.")
            news_stale = True

    coverage_status = "complete"
    if excluded_tickers:
        coverage_status = "partial"
    if not eligible_stocks:
        coverage_status = "none"

    if excluded_tickers:
        warnings.append(
            f"{len(excluded_tickers)} active ticker(s) were excluded by decision-grade eligibility gates."
        )

    return {
        "run_date": run_date.isoformat(),
        "coverage_status": coverage_status,
        "active_ticker_count": len(stocks),
        "eligible_ticker_count": len(eligible_stocks),
        "excluded_ticker_count": len(excluded_tickers),
        "eligible_stocks": eligible_stocks,
        "excluded_tickers": excluded_tickers,
        "stock_freshness_max_age_days": STOCK_FRESHNESS_MAX_AGE_DAYS,
        "min_history_calendar_days": MIN_HISTORY_CALENDAR_DAYS,
        "min_history_rows": MIN_HISTORY_ROWS,
        "last_news_collection": last_news_collection,
        "news_stale": news_stale,
        "warnings": warnings,
    }


def publication_data_quality(freshness: dict[str, Any]) -> dict[str, Any]:
    """Return the public data-quality subset for publication artifacts."""
    excluded = freshness["excluded_tickers"]
    exclusion_reason_counts: dict[str, int] = {}
    for row in excluded:
        for reason in row.get("reasons", []):
            exclusion_reason_counts[reason] = exclusion_reason_counts.get(reason, 0) + 1
    return {
        "coverage_status": freshness["coverage_status"],
        "active_ticker_count": freshness["active_ticker_count"],
        "eligible_ticker_count": freshness["eligible_ticker_count"],
        "excluded_ticker_count": freshness["excluded_ticker_count"],
        "exclusion_reason_counts": dict(sorted(exclusion_reason_counts.items())),
        "excluded_ticker_examples": excluded[:20],
        "stock_freshness_max_age_days": freshness["stock_freshness_max_age_days"],
        "min_history_calendar_days": freshness["min_history_calendar_days"],
        "min_history_rows": freshness["min_history_rows"],
        "last_news_collection": freshness["last_news_collection"],
        "news_stale": freshness["news_stale"],
        "warnings": freshness["warnings"],
    }


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
    client = _build_openai_client()
    analyses = []
    for score in shortlist:
        stock = stock_map[score["ticker"]]
        analysis = _analyze_candidate(client, stock, score, run_date)
        if analysis["analysis_method"] == "fallback_heuristic":
            _emit_metric("fallback_analyses", 1)
            logger.warning(
                "candidate_fallback_analysis_used",
                ticker=stock["ticker"],
                recommendation=analysis["recommendation"],
                confidence_score=analysis["confidence_score"],
                publication_allowed=analysis["publication_allowed"],
            )
        elif _requires_review(analysis):
            analysis = _review_candidate_analysis(client, stock, analysis)
        store.put_candidate_analysis(analysis)
        analyses.append(analysis)
    return analyses


def _build_openai_client() -> OpenAI | None:
    openai_api_key = get_openai_api_key()
    if not openai_api_key:
        return None
    try:
        return OpenAI(api_key=openai_api_key)
    except Exception as exc:
        logger.warning("openai_client_initialization_failed", error=str(exc))
        _emit_metric("openai_client_initialization_failures", 1)
        return None


def build_publication_payload(
    analyses: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    stocks: list[dict[str, Any]],
    run_date: date,
    data_quality: dict[str, Any] | None = None,
    upcoming_earnings: list[dict[str, Any]] | None = None,
    upcoming_dividends: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    stock_map = {stock["ticker"]: stock for stock in stocks}
    sell_watch = set(store.sell_alert_tickers())

    buy_candidates = [
        row
        for row in analyses
        if row["recommendation"] == "BUY" and row["opportunity_score"] > 0
        and _is_publication_allowed(row)
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
            _with_recommendation_context(
                {
                    "rank": rank,
                    "ticker": row["ticker"],
                    "company_name": stock["company_name"],
                    "sector": stock["sector"],
                    "logo_url": _stock_logo_url(stock),
                    "company_info": _company_info(stock),
                    "analysis_method": row.get("analysis_method", "ai"),
                    "recommendation": row["recommendation"],
                    "risk_level": row["risk_level"],
                    "confidence_score": row["confidence_score"],
                    "catalyst": row["catalyst"],
                    "expected_timeframe": row["expected_timeframe"],
                    "rationale": row["reasoning"],
                    "invalidation_criteria": row["invalidation_criteria"],
                    "ai_review": row.get("ai_review"),
                    "supporting_evidence": _evidence(row["signals"]),
                    "source_traceability": [signal["source"] for signal in row["signals"][:5]],
                },
                row["ticker"],
                run_date,
            )
        )

    sell_candidates = [
        row
        for row in analyses
        if row["negative_score"] >= 40
        and (row["ticker"] in sell_watch or row["recommendation"] == "SELL")
        and _is_publication_allowed(row)
    ]
    sell_candidates.sort(
        key=lambda row: (row["negative_score"], row["confidence_score"]), reverse=True
    )

    sell_alerts = []
    for rank, row in enumerate(sell_candidates[:TOP_PICK_COUNT], start=1):
        stock = stock_map[row["ticker"]]
        sell_alerts.append(
            _with_recommendation_context(
                {
                    "rank": rank,
                    "ticker": row["ticker"],
                    "company_name": stock["company_name"],
                    "sector": stock["sector"],
                    "logo_url": _stock_logo_url(stock),
                    "company_info": _company_info(stock),
                    "analysis_method": row.get("analysis_method", "ai"),
                    "severity": "critical" if row["negative_score"] >= 80 else "high",
                    "risk_level": row["risk_level"],
                    "confidence_score": row["confidence_score"],
                    "negative_catalyst": row["catalyst"],
                    "rationale": row["reasoning"],
                    "ai_review": row.get("ai_review"),
                    "supporting_evidence": _evidence(row["signals"]),
                    "source_traceability": [signal["source"] for signal in row["signals"][:5]],
                },
                row["ticker"],
                run_date,
            )
        )

    data_warnings = _source_warnings(scores)
    if data_quality:
        data_warnings.extend(data_quality.get("warnings", []))
    fallback_policy = _fallback_policy_summary(analyses)
    if fallback_policy["fallback_analysis_count"]:
        data_warnings.append(
            f"{fallback_policy['fallback_analysis_count']} candidate analysis result(s) used "
            "heuristic fallback because AI analysis was unavailable."
        )
    if fallback_policy["suppressed_fallback_count"]:
        data_warnings.append(
            f"{fallback_policy['suppressed_fallback_count']} fallback-generated actionable "
            "recommendation(s) were withheld from public publication."
        )
        _emit_metric(
            "fallback_publication_suppressed",
            fallback_policy["suppressed_fallback_count"],
        )
    review_policy = _review_policy_summary(analyses)
    if review_policy["review_suppressed_count"]:
        data_warnings.append(
            f"{review_policy['review_suppressed_count']} AI-generated actionable "
            "recommendation(s) were withheld by the review model."
        )
        _emit_metric(
            "review_publication_suppressed",
            review_policy["review_suppressed_count"],
        )
    return {
        "publication_date": run_date.isoformat(),
        "generated_at": datetime.utcnow().isoformat(),
        "publication_scope": "top_opportunities_among_eligible_tickers",
        "fallback_policy": fallback_policy,
        "review_policy": review_policy,
        "review_rejections": _review_rejection_audit(analyses, stock_map, run_date),
        "top_picks": top_picks,
        "sell_alerts": sell_alerts,
        "upcoming_earnings": upcoming_earnings or [],
        "upcoming_dividends": upcoming_dividends or [],
        "candidate_count": len(scores),
        "analyzed_count": len(analyses),
        "data_quality": data_quality or {},
        "data_warnings": data_warnings,
    }


def publish_payload(payload: dict[str, Any], run_date: date) -> None:
    if not ARTIFACT_BUCKET:
        logger.warning("artifact_bucket_not_configured")
        _emit_metric("artifact_publish_failures", 1)
        raise RuntimeError("Artifact bucket is not configured")

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
            "upcoming_earnings": payload.get("upcoming_earnings", []),
            "upcoming_dividends": payload.get("upcoming_dividends", []),
            "data_quality": payload["data_quality"],
            "data_warnings": payload["data_warnings"],
        },
        indent=2,
        default=str,
    ).encode("utf-8")
    sell_keys = [
        "sell-alerts/latest.json",
        f"sell-alerts/history/{run_date.isoformat()}.json",
    ]

    try:
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
    except Exception as exc:
        logger.error("artifact_publish_failed", bucket=ARTIFACT_BUCKET, error=str(exc))
        _emit_metric("artifact_publish_failures", 1)
        raise
    _emit_metric("artifact_publish_failures", 0)
    store.put_publication_record(run_date, payload)


def _analyze_candidate(
    client: OpenAI | None, stock: dict[str, Any], score: dict[str, Any], run_date: date
) -> dict[str, Any]:
    if client is None:
        logger.warning("candidate_ai_analysis_unavailable", ticker=stock["ticker"])
        return _fallback_analysis(stock, score, run_date, "openai_client_unavailable")

    prompt = _build_prompt(stock, score)
    try:
        response = client.chat.completions.create(
            model=OPENAI_ANALYSIS_MODEL,
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
            **_chat_completion_options(
                OPENAI_ANALYSIS_MODEL,
                max_tokens=500,
                temperature=0.25,
            ),
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        return _normalize_ai_analysis(stock, score, parsed, run_date)
    except Exception as exc:
        logger.warning("candidate_ai_analysis_failed", ticker=stock["ticker"], error=str(exc))
        return _fallback_analysis(stock, score, run_date, "openai_error")


def _fallback_analysis(
    stock: dict[str, Any], score: dict[str, Any], run_date: date, fallback_reason: str
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
    invalidation_checks = _invalidation_checks(score["signals"], recommendation)
    confidence = min(
        FALLBACK_CONFIDENCE_CAP,
        max(25, 40 + max(score["opportunity_score"], score["negative_score"]) // 4),
    )
    publication_allowed = (
        ALLOW_FALLBACK_ACTIONABLE_RECOMMENDATIONS or recommendation == "HOLD"
    )
    return {
        "ticker": stock["ticker"],
        "analysis_date": run_date.isoformat(),
        "analysis_method": "fallback_heuristic",
        "fallback_reason": fallback_reason,
        "publication_allowed": publication_allowed,
        "recommendation": recommendation,
        "risk_level": risk_level,
        "confidence_score": confidence,
        "catalyst": catalyst["title"] if catalyst else "No dominant catalyst",
        "expected_timeframe": "1-30 days",
        "reasoning": catalyst["summary"] if catalyst else "Candidate scored from available Phase 1 signals.",
        "invalidation_criteria": _default_invalidation_criteria(invalidation_checks),
        "invalidation_checks": invalidation_checks,
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
    primary_signal = _primary_signal(score["signals"])
    invalidation_checks = _invalidation_checks(score["signals"], recommendation)
    invalidation_criteria = str(parsed.get("invalidation_criteria", "")).strip()[:500]
    if not invalidation_criteria:
        invalidation_criteria = _default_invalidation_criteria(invalidation_checks)
    return {
        "ticker": stock["ticker"],
        "analysis_date": run_date.isoformat(),
        "analysis_method": "ai",
        "analysis_model": OPENAI_ANALYSIS_MODEL,
        "publication_allowed": True,
        "recommendation": recommendation,
        "risk_level": risk_level,
        "confidence_score": max(0, min(100, confidence)),
        "catalyst": str(
            parsed.get(
                "catalyst",
                primary_signal["title"] if primary_signal else "No dominant catalyst",
            )
        ),
        "expected_timeframe": str(parsed.get("expected_timeframe", "1-30 days")),
        "reasoning": str(parsed.get("reasoning", ""))[:1000],
        "invalidation_criteria": invalidation_criteria,
        "invalidation_checks": invalidation_checks,
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
    buy_checks = "\n".join(
        f"- {check}" for check in _invalidation_checks(score["signals"], "BUY")
    )
    sell_checks = "\n".join(
        f"- {check}" for check in _invalidation_checks(score["signals"], "SELL")
    )
    return f"""Analyze {stock['ticker']} ({stock['company_name']}, {stock['sector']}).

Opportunity score: {score['opportunity_score']}
Negative score: {score['negative_score']}

Signals:
{signals}

Use multi-day derived OHLCV context, sector/news/event evidence, and source
details to decide whether the setup is durable. Treat isolated one-session
price or volume moves as insufficient for BUY or SELL unless other evidence
confirms direction and risk/reward.

Use these candidate-specific invalidation checks when deciding whether a BUY or
SELL thesis is publication-grade. If the recommendation is actionable, the
invalidation_criteria must include at least one concrete signal failure, price
level, event outcome, or time-boxed condition instead of generic wording.

BUY invalidation checks:
{buy_checks}

SELL invalidation checks:
{sell_checks}

Return JSON with keys:
recommendation: BUY, HOLD, or SELL
risk_level: LOW, MEDIUM, or HIGH
confidence_score: integer 0-100
catalyst: short phrase
expected_timeframe: e.g. 1-7 days, 1-30 days, 1-90 days
reasoning: 2-3 concise sentences
invalidation_criteria: concise condition that would invalidate the thesis
"""


def _requires_review(analysis: dict[str, Any]) -> bool:
    return (
        analysis.get("analysis_method") == "ai"
        and analysis.get("recommendation") in {"BUY", "SELL"}
        and bool(analysis.get("publication_allowed", True))
    )


def _review_candidate_analysis(
    client: OpenAI | None, stock: dict[str, Any], analysis: dict[str, Any]
) -> dict[str, Any]:
    """Use a stronger model to block weak actionable AI recommendations."""
    if client is None:
        logger.warning("candidate_ai_review_unavailable", ticker=stock["ticker"])
        _emit_metric("ai_review_unavailable", 1)
        return {
            **analysis,
            "publication_allowed": False,
            "ai_review": {
                "status": "unavailable",
                "model": OPENAI_REVIEW_MODEL,
                "approved": False,
                "rationale": "OpenAI review client was unavailable.",
                "concerns": ["review_model_unavailable"],
            },
        }

    try:
        response = client.chat.completions.create(
            model=OPENAI_REVIEW_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a skeptical investment-risk reviewer. "
                        "Your job is to reject unsupported BUY or SELL stock "
                        "recommendations. This is not financial advice."
                    ),
                },
                {"role": "user", "content": _build_review_prompt(stock, analysis)},
            ],
            response_format={"type": "json_object"},
            **_chat_completion_options(
                OPENAI_REVIEW_MODEL,
                max_tokens=400,
                temperature=0.1,
            ),
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        approved = bool(parsed.get("approved", False))
        confidence_adjustment = int(parsed.get("confidence_adjustment", 0) or 0)
        confidence_score = max(
            0,
            min(100, int(analysis["confidence_score"]) + confidence_adjustment),
        )
        concerns = parsed.get("concerns", [])
        if not isinstance(concerns, list):
            concerns = [str(concerns)]
        updated = {
            **analysis,
            "confidence_score": confidence_score,
            "publication_allowed": approved,
            "ai_review": {
                "status": "approved" if approved else "rejected",
                "model": OPENAI_REVIEW_MODEL,
                "approved": approved,
                "rationale": str(parsed.get("rationale", ""))[:750],
                "concerns": [str(concern)[:250] for concern in concerns[:5]],
                "rejection_category": str(parsed.get("rejection_category", ""))[:120]
                or None,
                "what_would_make_approvable": str(
                    parsed.get("what_would_make_approvable", "")
                )[:500]
                or None,
            },
        }
        _emit_metric("ai_reviews_completed", 1)
        if not approved:
            _emit_metric("ai_reviews_rejected", 1)
            logger.warning(
                "candidate_ai_review_rejected",
                ticker=stock["ticker"],
                recommendation=analysis["recommendation"],
                rationale=updated["ai_review"]["rationale"],
            )
        return updated
    except Exception as exc:
        logger.warning("candidate_ai_review_failed", ticker=stock["ticker"], error=str(exc))
        _emit_metric("ai_review_failures", 1)
        return {
            **analysis,
            "publication_allowed": False,
            "ai_review": {
                "status": "error",
                "model": OPENAI_REVIEW_MODEL,
                "approved": False,
                "rationale": "Review model call failed.",
                "concerns": ["review_model_error"],
            },
        }


def _build_review_prompt(stock: dict[str, Any], analysis: dict[str, Any]) -> str:
    evidence = "\n".join(_evidence(analysis.get("signals", []))[:8])
    invalidation_checks = "\n".join(
        f"- {check}" for check in analysis.get("invalidation_checks", [])[:6]
    )
    return f"""Review this Stockara recommendation for publication.

Ticker: {stock['ticker']}
Company: {stock['company_name']}
Sector: {stock['sector']}
Recommendation: {analysis['recommendation']}
Risk level: {analysis['risk_level']}
Confidence score: {analysis['confidence_score']}
Opportunity score: {analysis['opportunity_score']}
Negative score: {analysis['negative_score']}
Catalyst: {analysis['catalyst']}
Reasoning: {analysis['reasoning']}
Invalidation criteria: {analysis['invalidation_criteria']}

Evidence:
{evidence}

Candidate-specific invalidation checks:
{invalidation_checks}

Approve only if the recommendation is well-supported by the evidence, the risk
is clearly stated, and the thesis is specific enough to be useful. For BUY or
SELL recommendations, reject if invalidation criteria are generic, not tied to
the evidence, missing a signal failure/price/event/time condition, too
speculative, stale, contradicted, or insufficiently supported.

Return JSON with keys:
approved: boolean
rationale: concise explanation
concerns: list of short strings
confidence_adjustment: integer from -20 to 10
rejection_category: short category when not approved, otherwise empty string
what_would_make_approvable: concise missing evidence or prompt issue when not approved
"""


def _chat_completion_options(
    model: str, max_tokens: int, temperature: float
) -> dict[str, Any]:
    if model.startswith("gpt-5"):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens, "temperature": temperature}


def _price_volume_signals(ticker: str, run_date: date) -> list[dict[str, Any]]:
    stored_signals = _stored_market_signals(ticker, run_date)
    try:
        rows = store.get_stock_data(ticker, run_date - timedelta(days=60), run_date)
    except Exception as exc:
        logger.warning("stock_market_context_lookup_failed", ticker=ticker, error=str(exc))
        return stored_signals
    derived_signals = _derived_market_context_signals(ticker, rows)
    if stored_signals:
        return stored_signals + derived_signals
    if len(rows) < 2:
        return derived_signals
    ordered = _ordered_stock_rows(rows)
    latest = ordered[-1]
    previous = ordered[-2]
    close = _analysis_close_price(latest)
    prev_close = _analysis_close_price(previous)
    if prev_close <= 0:
        return derived_signals
    price_change = float((close - prev_close) / prev_close * 100)
    avg_volume = sum(int(row["volume"]) for row in ordered[:-1]) / max(1, len(ordered) - 1)
    volume_ratio = int(latest["volume"]) / avg_volume if avg_volume else 1

    signals = derived_signals.copy()
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
                {
                    "price_change_percent": round(price_change, 2),
                    "requires_confirmation": True,
                },
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
                {
                    "volume_ratio": round(volume_ratio, 2),
                    "price_change_percent": round(price_change, 2),
                    "requires_confirmation": True,
                },
            )
        )
    return signals


def _derived_market_context_signals(
    ticker: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build multi-day technical context from stored OHLCV rows.

    These signals are intentionally derived from local data so the shortlist can
    carry trend durability and volume persistence before any external AI call.
    """
    if len(rows) < 6:
        return []
    ordered = _ordered_stock_rows(rows)
    closes = [_analysis_close_price(row) for row in ordered]
    volumes = [Decimal(str(row.get("volume", 0))) for row in ordered]
    latest_close = closes[-1]

    return_5d = _window_return_percent(closes, 5)
    return_20d = _window_return_percent(closes, 20)
    sma_20 = _average_decimal(closes[-20:]) if len(closes) >= 20 else _average_decimal(closes)
    close_vs_sma_20 = _pct_move(sma_20, latest_close) if sma_20 > 0 else 0.0
    recent_changes = [
        _pct_move(closes[index - 1], closes[index])
        for index in range(max(1, len(closes) - 10), len(closes))
    ]
    up_day_ratio = (
        sum(1 for change in recent_changes if change > 0) / len(recent_changes)
        if recent_changes
        else 0.0
    )
    drawdown_20d = _drawdown_percent(closes[-20:])

    trend_score = 0
    if return_5d >= 2:
        trend_score += 14
    elif return_5d <= -2:
        trend_score -= 14
    if return_20d >= 5:
        trend_score += 20
    elif return_20d <= -5:
        trend_score -= 20
    if close_vs_sma_20 >= 2:
        trend_score += 10
    elif close_vs_sma_20 <= -2:
        trend_score -= 10
    if up_day_ratio >= 0.65:
        trend_score += 8
    elif up_day_ratio <= 0.35:
        trend_score -= 8
    if drawdown_20d <= -12:
        trend_score -= min(12, int(abs(drawdown_20d) - 10))

    signals: list[dict[str, Any]] = []
    if abs(trend_score) >= 20:
        signals.append(
            _signal(
                ticker,
                "technical_trend",
                "positive" if trend_score > 0 else "negative",
                int(max(-55, min(55, trend_score))),
                "Multi-day technical trend",
                (
                    f"{ticker} has {return_5d:.2f}% 5-session return, "
                    f"{return_20d:.2f}% 20-session return, trades "
                    f"{close_vs_sma_20:.2f}% versus its 20-session average, "
                    f"and had {up_day_ratio:.0%} positive sessions recently."
                ),
                "derived_ohlcv",
                {
                    "return_5d_percent": round(return_5d, 2),
                    "return_20d_percent": round(return_20d, 2),
                    "close_vs_sma_20_percent": round(close_vs_sma_20, 2),
                    "up_day_ratio_10d": round(up_day_ratio, 2),
                    "drawdown_20d_percent": round(drawdown_20d, 2),
                    "history_row_count": len(ordered),
                },
            )
        )

    if len(volumes) >= 8:
        recent_volume = _average_decimal(volumes[-3:])
        baseline_volume = _average_decimal(volumes[-23:-3] or volumes[:-3])
        volume_persistence = (
            float(recent_volume / baseline_volume) if baseline_volume > 0 else 1.0
        )
        if volume_persistence >= 1.35:
            direction = "positive" if return_5d >= 0 else "negative"
            score = int(min(35, 12 + (volume_persistence - 1) * 12 + abs(return_5d)))
            if direction == "negative":
                score = -score
            signals.append(
                _signal(
                    ticker,
                    "volume_persistence",
                    direction,
                    score,
                    "Persistent elevated volume",
                    (
                        f"{ticker}'s last 3 sessions traded at "
                        f"{volume_persistence:.1f}x baseline volume while its "
                        f"5-session return was {return_5d:.2f}%."
                    ),
                    "derived_ohlcv",
                    {
                        "recent_3_session_volume_ratio": round(volume_persistence, 2),
                        "return_5d_percent": round(return_5d, 2),
                        "recent_average_volume": _jsonable_value(recent_volume),
                        "baseline_average_volume": _jsonable_value(baseline_volume),
                    },
                )
            )

    return signals


def _stored_market_signals(ticker: str, run_date: date) -> list[dict[str, Any]]:
    try:
        rows = store.market_signals_for_ticker(
            ticker,
            run_date - timedelta(days=3),
            run_date,
        )
    except AttributeError:
        return []
    except Exception as exc:
        logger.warning("market_signal_lookup_failed", ticker=ticker, error=str(exc))
        return []

    signals = []
    for row in rows:
        signal_type = row.get("signal_type")
        if signal_type not in STORED_SIGNAL_TYPES:
            continue
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        raw = source.get("raw") if isinstance(source.get("raw"), dict) else {}
        raw = {
            **raw,
            "signal_date": row.get("signal_date"),
            "price_change_percent": _jsonable_value(row.get("price_change_percent")),
            "volume_ratio": _jsonable_value(row.get("volume_ratio")),
            "close_price": _jsonable_value(row.get("close_price")),
            "previous_close_price": _jsonable_value(row.get("previous_close_price")),
            "volume": row.get("volume"),
            "average_volume": _jsonable_value(row.get("average_volume")),
        }
        signals.append(
            _signal(
                ticker,
                signal_type,
                row["direction"],
                int(row["score"]),
                row["title"],
                row["summary"],
                str(source.get("provider") or "stock_collector"),
                {key: value for key, value in raw.items() if value is not None},
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
        score = 0
        title = "Elevated news momentum"
    raw = {"article_count": len(articles)}
    if direction == "neutral":
        raw["context_only"] = True
    return [
        _signal(
            ticker,
            "news",
            direction,
            score,
            title,
            f"{len(articles)} recent ticker-related article(s); latest: {articles[0]['title']}",
            "news",
            raw,
        )
    ]


def _event_signals(ticker: str, run_date: date) -> list[dict[str, Any]]:
    signals = _earnings_signals(ticker, run_date)
    signals.extend(_dividend_signals(ticker, run_date))
    return signals


def _earnings_signals(ticker: str, run_date: date) -> list[dict[str, Any]]:
    upcoming = store.earnings_events_for_ticker(
        ticker,
        run_date,
        run_date + timedelta(days=EARNINGS_LOOKAHEAD_DAYS),
    )
    upcoming = [event for event in upcoming if event.get("is_upcoming")]
    if not upcoming:
        return []

    next_event = sorted(upcoming, key=lambda event: event["event_date"])[0]
    past_events = store.earnings_events_for_ticker(
        ticker,
        run_date - timedelta(days=EARNINGS_HISTORY_DAYS),
        run_date - timedelta(days=1),
    )
    past_events = [
        event
        for event in past_events
        if event.get("post_earnings_price_move_percent") is not None
    ][-10:]
    recent_news = store.news_for_ticker(ticker, run_date - timedelta(days=14), run_date)
    prediction = _earnings_prediction(next_event, past_events, recent_news)
    days_until = (
        _parse_date(next_event.get("event_date")) or run_date
    ) - run_date
    title = "Upcoming earnings event"
    summary = (
        f"{ticker} reports earnings in {days_until.days} day(s). "
        f"{prediction['summary']}"
    )
    return [
        _signal(
            ticker,
            "earnings",
            prediction["direction"],
            prediction["score"],
            title,
            summary,
            "earnings_calendar",
            {
                "next_event": _jsonable_earnings_event(next_event),
                "historical_event_count": len(past_events),
                "average_post_earnings_move_percent": prediction[
                    "average_post_earnings_move_percent"
                ],
                "news_article_count": len(recent_news),
                "prediction": prediction,
            },
        )
    ]


def upcoming_earnings_summary(run_date: date, limit: int = 20) -> list[dict[str, Any]]:
    events = store.upcoming_earnings(
        run_date,
        run_date + timedelta(days=EARNINGS_LOOKAHEAD_DAYS),
        limit=limit,
    )
    return [_jsonable_earnings_event(event) for event in events]


def _dividend_signals(ticker: str, run_date: date) -> list[dict[str, Any]]:
    upcoming = store.dividend_events_for_ticker(
        ticker,
        run_date,
        run_date + timedelta(days=DIVIDEND_LOOKAHEAD_DAYS),
    )
    upcoming = [event for event in upcoming if event.get("is_upcoming")]
    if not upcoming:
        return []

    next_event = sorted(upcoming, key=lambda event: event["ex_dividend_date"])[0]
    past_events = store.dividend_events_for_ticker(
        ticker,
        run_date - timedelta(days=DIVIDEND_HISTORY_DAYS),
        run_date - timedelta(days=1),
    )
    past_events = [
        event
        for event in past_events
        if event.get("post_ex_dividend_price_move_percent") is not None
    ][-10:]
    prediction = _dividend_prediction(next_event, past_events)
    days_until = (
        _parse_date(next_event.get("ex_dividend_date")) or run_date
    ) - run_date
    return [
        _signal(
            ticker,
            "dividend",
            prediction["direction"],
            prediction["score"],
            "Upcoming dividend event",
            (
                f"{ticker} goes ex-dividend in {days_until.days} day(s). "
                f"{prediction['summary']}"
            ),
            "dividend_calendar",
            {
                "next_event": _jsonable_dividend_event(next_event),
                "historical_event_count": len(past_events),
                "average_post_ex_dividend_move_percent": prediction[
                    "average_post_ex_dividend_move_percent"
                ],
                "prediction": prediction,
            },
        )
    ]


def upcoming_dividends_summary(run_date: date, limit: int = 20) -> list[dict[str, Any]]:
    events = store.upcoming_dividends(
        run_date,
        run_date + timedelta(days=DIVIDEND_LOOKAHEAD_DAYS),
        limit=limit,
    )
    return [_jsonable_dividend_event(event) for event in events]


def _dividend_prediction(
    next_event: dict[str, Any],
    past_events: list[dict[str, Any]],
) -> dict[str, Any]:
    moves = [
        float(event["post_ex_dividend_price_move_percent"])
        for event in past_events
        if event.get("post_ex_dividend_price_move_percent") is not None
    ]
    avg_move = sum(moves) / len(moves) if moves else 0.0
    avg_abs_move = sum(abs(move) for move in moves) / len(moves) if moves else 0.0
    dividend_yield = float(next_event.get("dividend_yield") or 0)
    has_reaction_history = len(moves) >= 3
    score = (
        int(max(-40, min(40, avg_move * 5 + min(dividend_yield, 8) * 2)))
        if has_reaction_history
        else 0
    )
    direction = "positive" if score > 6 else "negative" if score < -6 else "neutral"
    amount = next_event.get("dividend_amount")
    amount_text = f" Dividend amount is {amount}." if amount is not None else ""
    yield_text = f" Forward dividend yield is {dividend_yield:.2f}%." if dividend_yield else ""
    if moves:
        history = (
            f"Past {len(moves)} ex-dividend event(s) averaged {avg_move:.2f}% "
            f"next-session price move with {avg_abs_move:.2f}% average absolute volatility."
        )
    else:
        history = "No stored ex-dividend price reaction history is available yet."
    return {
        "direction": direction,
        "score": score,
        "summary": f"{history}{amount_text}{yield_text}",
        "average_post_ex_dividend_move_percent": round(avg_move, 2),
        "average_abs_post_ex_dividend_move_percent": round(avg_abs_move, 2),
        "dividend_yield": round(dividend_yield, 2),
        "reaction_history_sufficient": has_reaction_history,
        "context_only": not has_reaction_history,
    }


def _earnings_prediction(
    next_event: dict[str, Any],
    past_events: list[dict[str, Any]],
    recent_news: list[dict[str, Any]],
) -> dict[str, Any]:
    moves = [
        float(event["post_earnings_price_move_percent"])
        for event in past_events
        if event.get("post_earnings_price_move_percent") is not None
    ]
    avg_move = sum(moves) / len(moves) if moves else 0.0
    avg_abs_move = sum(abs(move) for move in moves) / len(moves) if moves else 0.0
    surprises = [
        float(event["surprise_percent"])
        for event in past_events
        if event.get("surprise_percent") is not None
    ]
    avg_surprise = sum(surprises) / len(surprises) if surprises else 0.0
    news_text = " ".join(
        f"{article.get('title', '')} {article.get('summary', '')}".lower()
        for article in recent_news
    )
    positive_hits = sum(1 for keyword in POSITIVE_KEYWORDS if keyword in news_text)
    negative_hits = sum(1 for keyword in NEGATIVE_KEYWORDS if keyword in news_text)
    has_reaction_history = len(moves) >= 3
    has_surprise_history = len(surprises) >= 3
    has_news_catalyst = positive_hits > 0 or negative_hits > 0
    score = int(
        max(
            -60,
            min(
                60,
                (avg_move * 6 if has_reaction_history else 0)
                + (avg_surprise * 0.25 if has_surprise_history else 0),
            ),
        )
    )
    if positive_hits > negative_hits:
        score += min(20, positive_hits * 5)
    elif negative_hits > positive_hits:
        score -= min(25, negative_hits * 7)
    score = int(max(-75, min(75, score)))
    if not (has_reaction_history or has_news_catalyst):
        score = 0
    direction = "positive" if score > 8 else "negative" if score < -8 else "neutral"
    if moves:
        history = (
            f"Past {len(moves)} event(s) averaged {avg_move:.2f}% post-earnings "
            f"price move with {avg_abs_move:.2f}% average absolute volatility."
        )
    else:
        history = "No stored post-earnings price reaction history is available yet."
    news = (
        f"Recent news tone has {positive_hits} positive and {negative_hits} negative "
        "earnings-relevant keyword hit(s)."
        if recent_news
        else "No recent ticker news is available for earnings context."
    )
    estimate = next_event.get("eps_estimate")
    estimate_text = f" Consensus EPS estimate is {estimate}." if estimate is not None else ""
    return {
        "direction": direction,
        "score": score,
        "summary": f"{history} {news}{estimate_text}",
        "average_post_earnings_move_percent": round(avg_move, 2),
        "average_abs_post_earnings_move_percent": round(avg_abs_move, 2),
        "average_surprise_percent": round(avg_surprise, 2),
        "positive_news_hits": positive_hits,
        "negative_news_hits": negative_hits,
        "reaction_history_sufficient": has_reaction_history,
        "surprise_history_sufficient": has_surprise_history,
        "news_catalyst_present": has_news_catalyst,
        "context_only": not (has_reaction_history or has_news_catalyst),
    }


def _jsonable_earnings_event(event: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "ticker",
        "company_name",
        "event_date",
        "eps_estimate",
        "reported_eps",
        "surprise_percent",
        "time_of_day",
        "is_upcoming",
        "price_before",
        "price_after",
        "post_earnings_price_move_percent",
        "provider",
        "source_url",
    )
    return {field: _jsonable_value(event[field]) for field in fields if field in event}


def _jsonable_dividend_event(event: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "ticker",
        "company_name",
        "ex_dividend_date",
        "pay_date",
        "dividend_amount",
        "dividend_yield",
        "is_upcoming",
        "price_before",
        "price_after",
        "post_ex_dividend_price_move_percent",
        "provider",
        "source_url",
    )
    return {field: _jsonable_value(event[field]) for field in fields if field in event}


def _options_signals(ticker: str) -> list[dict[str, Any]]:
    try:
        yf_ticker = yf.Ticker(ticker)
        expirations = list(getattr(yf_ticker, "options", []) or [])
        if not expirations:
            return []

        raw: dict[str, Any] = {"expiration_count": len(expirations)}
        direction = "neutral"
        score = 0
        summary = f"{ticker} has listed options expirations; use as volatility context."
        try:
            chain = yf_ticker.option_chain(expirations[0])
            call_open_interest = _option_table_open_interest(getattr(chain, "calls", None))
            put_open_interest = _option_table_open_interest(getattr(chain, "puts", None))
            total_open_interest = call_open_interest + put_open_interest
            raw.update(
                {
                    "nearest_expiration": expirations[0],
                    "call_open_interest": call_open_interest,
                    "put_open_interest": put_open_interest,
                    "total_open_interest": total_open_interest,
                }
            )
            if total_open_interest >= 500:
                put_call_ratio = put_open_interest / max(call_open_interest, 1)
                raw["put_call_open_interest_ratio"] = round(put_call_ratio, 2)
                if put_call_ratio <= 0.7:
                    direction = "positive"
                    score = min(18, 8 + int((0.7 - put_call_ratio) * 20))
                    summary = (
                        f"{ticker} nearest-expiration options skew is call-heavy "
                        f"with a {put_call_ratio:.2f} put/call open-interest ratio."
                    )
                elif put_call_ratio >= 1.4:
                    direction = "negative"
                    score = -min(18, 8 + int((put_call_ratio - 1.4) * 8))
                    summary = (
                        f"{ticker} nearest-expiration options skew is put-heavy "
                        f"with a {put_call_ratio:.2f} put/call open-interest ratio."
                    )
        except Exception as exc:
            raw["chain_error"] = str(exc)
        if direction == "neutral":
            raw["context_only"] = True
        return [
            _signal(
                ticker,
                "options",
                direction,
                score,
                "Options open-interest context",
                summary,
                "yfinance",
                raw,
            )
        ]
    except Exception as exc:
        logger.info("options_signal_provider_unavailable", ticker=ticker, error=str(exc))
    return []


def _analyst_signals(ticker: str) -> list[dict[str, Any]]:
    try:
        recs = yf.Ticker(ticker).recommendations
        rows = _table_records(recs)
        if not rows:
            return []
        latest = rows[-1]
        strong_buy = _numeric(latest.get("strongBuy") or latest.get("strong_buy"))
        buy = _numeric(latest.get("buy"))
        hold = _numeric(latest.get("hold"))
        sell = _numeric(latest.get("sell"))
        strong_sell = _numeric(latest.get("strongSell") or latest.get("strong_sell"))
        coverage = strong_buy + buy + hold + sell + strong_sell
        bullish = strong_buy + buy
        bearish = sell + strong_sell
        raw = {
            "coverage": coverage,
            "strong_buy": strong_buy,
            "buy": buy,
            "hold": hold,
            "sell": sell,
            "strong_sell": strong_sell,
        }
        if coverage < 5:
            raw["context_only"] = True
            return [
                _signal(
                    ticker,
                    "analyst",
                    "neutral",
                    0,
                    "Analyst rating context",
                    f"{ticker} has limited analyst recommendation coverage.",
                    "yfinance",
                    raw,
                )
            ]

        net_ratio = (bullish - bearish) / coverage
        raw["bullish_share"] = round(bullish / coverage, 2)
        raw["bearish_share"] = round(bearish / coverage, 2)
        raw["net_bullish_ratio"] = round(net_ratio, 2)
        if net_ratio >= 0.5:
            return [
                _signal(
                    ticker,
                    "analyst",
                    "positive",
                    min(24, 10 + int(net_ratio * 18)),
                    "Bullish analyst recommendation mix",
                    f"{ticker} analyst recommendations are meaningfully bullish.",
                    "yfinance",
                    raw,
                )
            ]
        if net_ratio <= -0.25:
            return [
                _signal(
                    ticker,
                    "analyst",
                    "negative",
                    -min(24, 10 + int(abs(net_ratio) * 18)),
                    "Bearish analyst recommendation mix",
                    f"{ticker} analyst recommendations are meaningfully bearish.",
                    "yfinance",
                    raw,
                )
            ]
        raw["context_only"] = True
        return [
            _signal(
                ticker,
                "analyst",
                "neutral",
                0,
                "Mixed analyst recommendation context",
                f"{ticker} analyst recommendations are mixed.",
                "yfinance",
                raw,
            )
        ]
    except Exception as exc:
        logger.info("analyst_signal_provider_unavailable", ticker=ticker, error=str(exc))
    return []


def _insider_signals(ticker: str) -> list[dict[str, Any]]:
    try:
        txns = yf.Ticker(ticker).insider_transactions
        rows = _table_records(txns)
        if not rows:
            return []
        purchase_shares = 0.0
        sale_shares = 0.0
        considered_rows = 0
        for row in rows[:20]:
            shares = abs(_numeric(row.get("Shares") or row.get("shares")))
            if shares <= 0:
                continue
            label = str(
                row.get("Transaction")
                or row.get("transaction")
                or row.get("Text")
                or row.get("text")
                or ""
            ).lower()
            considered_rows += 1
            if any(term in label for term in ["sale", "sell", "disposed"]):
                sale_shares += shares
            elif any(term in label for term in ["purchase", "buy", "acquired"]):
                purchase_shares += shares
        total_shares = purchase_shares + sale_shares
        raw = {
            "transaction_rows": len(rows),
            "considered_rows": considered_rows,
            "purchase_shares": round(purchase_shares, 2),
            "sale_shares": round(sale_shares, 2),
            "net_purchase_shares": round(purchase_shares - sale_shares, 2),
        }
        if total_shares <= 0 or considered_rows < 2:
            raw["context_only"] = True
            return [
                _signal(
                    ticker,
                    "insider",
                    "neutral",
                    0,
                    "Insider transaction context",
                    f"{ticker} has insider transaction rows but no clear directional aggregate.",
                    "yfinance",
                    raw,
                )
            ]
        net_ratio = (purchase_shares - sale_shares) / total_shares
        raw["net_purchase_ratio"] = round(net_ratio, 2)
        if net_ratio >= 0.5:
            return [
                _signal(
                    ticker,
                    "insider",
                    "positive",
                    min(18, 8 + int(net_ratio * 12)),
                    "Net insider buying",
                    f"{ticker} recent insider transactions show net buying.",
                    "yfinance",
                    raw,
                )
            ]
        if net_ratio <= -0.5:
            return [
                _signal(
                    ticker,
                    "insider",
                    "negative",
                    -min(18, 8 + int(abs(net_ratio) * 12)),
                    "Net insider selling",
                    f"{ticker} recent insider transactions show net selling.",
                    "yfinance",
                    raw,
                )
            ]
        raw["context_only"] = True
        return [
            _signal(
                ticker,
                "insider",
                "neutral",
                0,
                "Mixed insider transaction context",
                f"{ticker} recent insider transactions are mixed.",
                "yfinance",
                raw,
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
                    0,
                    "Institutional holder context",
                    f"{ticker} has institutional holder data available, but no ownership-change evidence.",
                    "yfinance",
                    {"holder_count": len(holders), "context_only": True},
                )
            ]
    except Exception as exc:
        logger.info("institutional_signal_provider_unavailable", ticker=ticker, error=str(exc))
    return []


def _fundamental_signals(ticker: str) -> list[dict[str, Any]]:
    try:
        yf_ticker = yf.Ticker(ticker)
        info_getter = getattr(yf_ticker, "get_info", None)
        info = info_getter() if callable(info_getter) else getattr(yf_ticker, "info", {})
        if not isinstance(info, dict) or not info:
            return []

        revenue_growth = _numeric(info.get("revenueGrowth"), default=None)
        profit_margin = _numeric(info.get("profitMargins"), default=None)
        debt_to_equity = _numeric(info.get("debtToEquity"), default=None)
        free_cashflow = _numeric(info.get("freeCashflow"), default=None)
        forward_pe = _numeric(info.get("forwardPE"), default=None)
        trailing_pe = _numeric(info.get("trailingPE"), default=None)
        raw = {
            "revenue_growth": revenue_growth,
            "profit_margin": profit_margin,
            "debt_to_equity": debt_to_equity,
            "free_cashflow": free_cashflow,
            "forward_pe": forward_pe,
            "trailing_pe": trailing_pe,
            "market_cap": _numeric(info.get("marketCap"), default=None),
        }
        known_values = [value for value in raw.values() if value is not None]
        if len(known_values) < 3:
            raw["context_only"] = True
            return [
                _signal(
                    ticker,
                    "fundamental_context",
                    "neutral",
                    0,
                    "Limited fundamental context",
                    f"{ticker} has limited fundamental fields available from yfinance.",
                    "yfinance",
                    raw,
                )
            ]

        positive_quality = (
            revenue_growth is not None
            and revenue_growth >= 0.08
            and profit_margin is not None
            and profit_margin >= 0.08
            and (debt_to_equity is None or debt_to_equity <= 150)
            and (free_cashflow is None or free_cashflow > 0)
        )
        negative_quality = (
            profit_margin is not None
            and profit_margin < 0
            and revenue_growth is not None
            and revenue_growth <= -0.05
        ) or (
            debt_to_equity is not None
            and debt_to_equity >= 300
            and (free_cashflow is None or free_cashflow < 0)
        )
        inexpensive_quality = (
            positive_quality
            and forward_pe is not None
            and 0 < forward_pe <= 18
        )
        expensive_weakness = (
            forward_pe is not None
            and forward_pe >= 80
            and (revenue_growth is None or revenue_growth < 0.08)
            and (profit_margin is None or profit_margin < 0.08)
        )

        if inexpensive_quality:
            return [
                _signal(
                    ticker,
                    "fundamental_context",
                    "positive",
                    22,
                    "Quality fundamentals at moderate valuation",
                    f"{ticker} combines growth, profitability, manageable leverage, and a moderate forward multiple.",
                    "yfinance",
                    raw,
                )
            ]
        if positive_quality:
            return [
                _signal(
                    ticker,
                    "fundamental_context",
                    "positive",
                    16,
                    "Quality fundamental profile",
                    f"{ticker} shows positive growth, profitability, and manageable leverage.",
                    "yfinance",
                    raw,
                )
            ]
        if expensive_weakness:
            return [
                _signal(
                    ticker,
                    "fundamental_context",
                    "negative",
                    -18,
                    "Stretched valuation without fundamental support",
                    f"{ticker} has a high forward multiple without matching growth or margin support.",
                    "yfinance",
                    raw,
                )
            ]
        if negative_quality:
            return [
                _signal(
                    ticker,
                    "fundamental_context",
                    "negative",
                    -16,
                    "Weak fundamental profile",
                    f"{ticker} shows weak profitability/growth or stressed leverage.",
                    "yfinance",
                    raw,
                )
            ]

        raw["context_only"] = True
        return [
            _signal(
                ticker,
                "fundamental_context",
                "neutral",
                0,
                "Fundamental context",
                f"{ticker} fundamental fields are available but do not form a clear directional signal.",
                "yfinance",
                raw,
            )
        ]
    except Exception as exc:
        logger.info("fundamental_signal_provider_unavailable", ticker=ticker, error=str(exc))
    return []


def _table_records(table: Any) -> list[dict[str, Any]]:
    if table is None:
        return []
    if hasattr(table, "to_dict"):
        try:
            return [
                {str(key): _jsonable_value(value) for key, value in row.items()}
                for row in table.to_dict("records")
            ]
        except Exception:
            return []
    if isinstance(table, list):
        return [row for row in table if isinstance(row, dict)]
    return []


def _numeric(value: Any, default: float | None = 0.0) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _option_table_open_interest(table: Any) -> float:
    rows = _table_records(table)
    return sum(_numeric(row.get("openInterest") or row.get("open_interest")) for row in rows)


def _sector_relative_signals(stock: dict[str, Any], run_date: date) -> list[dict[str, Any]]:
    ticker = stock["ticker"]
    sector_etf = SECTOR_ETFS.get(stock["sector"])
    if not sector_etf:
        return []
    stock_rows = store.get_stock_data(ticker, run_date - timedelta(days=45), run_date)
    if len(stock_rows) < 21:
        return []
    try:
        etf = yf.download(sector_etf, period="1mo", progress=False, timeout=10)
        if etf is None or etf.empty:
            return []
        stock_closes = [_analysis_close_price(row) for row in _ordered_stock_rows(stock_rows)]
        etf_closes = _close_values_from_download(etf)
        if len(etf_closes) < 21:
            return []

        stock_return_5d = _window_return_percent(stock_closes, 5)
        stock_return_20d = _window_return_percent(stock_closes, 20)
        etf_return_5d = _window_return_percent(etf_closes, 5)
        etf_return_20d = _window_return_percent(etf_closes, 20)
        relative_5d = stock_return_5d - etf_return_5d
        relative_20d = stock_return_20d - etf_return_20d
        weighted_relative = relative_20d * 0.7 + relative_5d * 0.3
        if abs(weighted_relative) < 3:
            return []
        score = int(max(-45, min(45, weighted_relative * 3)))
        return [
            _signal(
                ticker,
                "sector_relative",
                "positive" if weighted_relative > 0 else "negative",
                score,
                "Sector-relative strength",
                (
                    f"{ticker} has {relative_5d:.2f}% 5-session and "
                    f"{relative_20d:.2f}% 20-session relative return versus {sector_etf}."
                ),
                "yfinance",
                {
                    "sector_etf": sector_etf,
                    "stock_return_5d_percent": round(stock_return_5d, 2),
                    "stock_return_20d_percent": round(stock_return_20d, 2),
                    "sector_return_5d_percent": round(etf_return_5d, 2),
                    "sector_return_20d_percent": round(etf_return_20d, 2),
                    "relative_5d_percent": round(relative_5d, 2),
                    "relative_20d_percent": round(relative_20d, 2),
                    "weighted_relative_percent": round(weighted_relative, 2),
                    "stock_history_row_count": len(stock_closes),
                    "sector_history_row_count": len(etf_closes),
                },
            )
        ]
    except Exception as exc:
        logger.info("sector_signal_provider_unavailable", ticker=ticker, error=str(exc))
        return []


def _close_values_from_download(downloaded: Any) -> list[Decimal]:
    close_values = downloaded["Close"]
    if hasattr(close_values, "squeeze"):
        close_values = close_values.squeeze()
    if hasattr(close_values, "dropna"):
        close_values = close_values.dropna()
    if hasattr(close_values, "tolist"):
        values = close_values.tolist()
    else:
        values = list(close_values)

    closes: list[Decimal] = []
    for value in values:
        if hasattr(value, "item"):
            value = value.item()
        try:
            closes.append(Decimal(str(value)))
        except Exception:
            continue
    return closes


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


def _ordered_stock_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: str(row.get("trading_date", "")))


def _window_return_percent(closes: list[Decimal], sessions: int) -> float:
    if len(closes) <= sessions:
        return _pct_move(closes[0], closes[-1])
    return _pct_move(closes[-sessions - 1], closes[-1])


def _average_decimal(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _drawdown_percent(closes: list[Decimal]) -> float:
    if not closes:
        return 0.0
    peak = max(closes)
    if peak <= 0:
        return 0.0
    latest = closes[-1]
    return float((latest - peak) / peak * 100)


def _analysis_close_price(row: dict[str, Any]) -> Decimal:
    """Return the close value preferred for analysis.

    Adjusted close is preferred when a provider supplies it, because it better
    preserves comparability around splits and dividends. The raw close remains
    the backward-compatible storage field and fallback.
    """
    adjusted = row.get("adjusted_close_price")
    if adjusted is not None:
        return Decimal(str(adjusted))
    return Decimal(str(row["close_price"]))


def _has_market_data_provenance(row: dict[str, Any]) -> bool:
    return bool(row.get("data_provider")) and bool(row.get("price_adjustment"))


def _primary_signal(signals: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored_signals = _scored_evidence_signals(signals)
    if scored_signals:
        return max(scored_signals, key=lambda signal: abs(signal["score"]))
    if not signals:
        return None
    return max(signals, key=lambda signal: abs(signal["score"]))


def _evidence(signals: list[dict[str, Any]]) -> list[str]:
    evidence: list[str] = []
    seen: set[str] = set()
    scored_signals = _scored_evidence_signals(signals)
    evidence_signals = scored_signals or signals
    for signal in sorted(evidence_signals, key=lambda s: abs(s["score"]), reverse=True):
        summary = str(signal.get("summary", "")).strip()
        key = " ".join(summary.lower().split())
        if not summary or key in seen:
            continue
        seen.add(key)
        evidence.append(summary)
        if len(evidence) >= 5:
            break
    return evidence


def _invalidation_checks(
    signals: list[dict[str, Any]], recommendation: str
) -> list[str]:
    recommendation = recommendation.upper()
    target_direction = "positive" if recommendation == "BUY" else "negative"
    if recommendation not in {"BUY", "SELL"}:
        target_direction = ""

    checks: list[str] = []
    evidence_signals = _scored_evidence_signals(signals) or signals
    for signal in sorted(evidence_signals, key=lambda s: abs(s.get("score", 0)), reverse=True):
        if target_direction and str(signal.get("direction", "")).lower() != target_direction:
            continue
        check = _signal_invalidation_check(signal, recommendation)
        if check and check not in checks:
            checks.append(check)
        if len(checks) >= 5:
            break

    if not checks:
        checks.append(
            "Do not publish as actionable unless source-backed evidence identifies a concrete failure condition and review horizon."
        )
    checks.append(
        "Reassess after the stated timeframe; stale evidence without follow-through invalidates the setup."
    )
    return checks[:6]


def _signal_invalidation_check(signal: dict[str, Any], recommendation: str) -> str:
    signal_type = str(signal.get("signal_type", "signal"))
    direction = str(signal.get("direction", "neutral")).lower()
    source = signal.get("source")
    raw = source.get("raw") if isinstance(source, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    ticker = str(signal.get("ticker", "ticker"))
    opposite_direction = "negative" if direction == "positive" else "positive"

    if signal_type in {"technical_trend", "price_move", "volume_move", "volume_persistence"}:
        pieces = []
        if raw.get("return_5d_percent") is not None:
            pieces.append(f"5-session return no longer supports a {direction} thesis")
        if raw.get("close_vs_sma_20_percent") is not None:
            pieces.append("close loses its 20-session moving-average confirmation")
        if raw.get("volume_ratio") is not None or raw.get("recent_3_session_volume_ratio") is not None:
            pieces.append("volume confirmation fades toward normal levels")
        if raw.get("price_change_percent") is not None:
            pieces.append("the one-session move reverses without multi-day follow-through")
        return "; ".join(pieces) or (
            f"{ticker} market action flips {opposite_direction} or loses multi-session confirmation."
        )

    if signal_type in {"sector_relative", "sector_context"}:
        sector_etf = raw.get("sector_etf")
        sector_note = f" versus {sector_etf}" if sector_etf else ""
        return f"{ticker} relative performance{sector_note} stops confirming the {direction} setup."

    if signal_type in {"news", "sec_filing", "earnings_release", "earnings_transcript"}:
        return (
            "Company-specific news or filing evidence is corrected, contradicted, "
            "or fails to translate into measurable business/price follow-through."
        )

    if signal_type == "earnings":
        event_date = raw.get("event_date")
        event_note = f" around {event_date}" if event_date else ""
        return f"Earnings expectations or post-event reaction{event_note} fail to confirm the thesis."

    if signal_type == "dividend":
        event_date = raw.get("ex_dividend_date")
        event_note = f" around {event_date}" if event_date else ""
        return f"Dividend timing/yield or historical ex-dividend reaction{event_note} no longer supports the setup."

    if signal_type == "analyst":
        return "Analyst recommendation mix deteriorates or loses meaningful coverage support."

    if signal_type == "options":
        return "Options open-interest skew normalizes or flips against the thesis."

    if signal_type == "insider":
        return "Insider transaction aggregate no longer shows clear net buying/selling in the thesis direction."

    if signal_type == "fundamental_context":
        return "Growth, margin, leverage, cash-flow, or valuation fields no longer support the directional thesis."

    return f"{ticker} {signal_type.replace('_', ' ')} evidence no longer supports a {recommendation} thesis."


def _default_invalidation_criteria(checks: list[str]) -> str:
    first = checks[0] if checks else "Source-backed evidence stops supporting the thesis."
    second = checks[1] if len(checks) > 1 else "Reassess after the stated timeframe."
    return f"{first} {second}"[:500]


def _is_scored_evidence_signal(signal: dict[str, Any]) -> bool:
    """Return true when a signal should affect candidate ranking.

    Neutral/context-only rows are still useful prompt context, but they should
    not inflate opportunity or risk totals simply because data was present.
    """
    if str(signal.get("direction", "")).lower() == "neutral":
        return False
    source = signal.get("source")
    raw = source.get("raw") if isinstance(source, dict) else {}
    if isinstance(raw, dict) and raw.get("context_only"):
        return False
    return int(signal.get("score") or 0) != 0


def _scored_evidence_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base_scored = [signal for signal in signals if _is_scored_evidence_signal(signal)]
    confirmed_directions = {
        str(signal.get("direction", "")).lower()
        for signal in base_scored
        if not _requires_confirmation(signal)
    }
    return [
        signal
        for signal in base_scored
        if not _requires_confirmation(signal)
        or str(signal.get("direction", "")).lower() in confirmed_directions
    ]


def _requires_confirmation(signal: dict[str, Any]) -> bool:
    source = signal.get("source")
    raw = source.get("raw") if isinstance(source, dict) else {}
    return isinstance(raw, dict) and bool(raw.get("requires_confirmation"))


def _risk_weight(risk_level: str) -> int:
    return {"LOW": 1, "MEDIUM": 2, "HIGH": 3}.get(risk_level, 2)


def _source_warnings(scores: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if not any(signal["signal_type"] == "earnings" for score in scores for signal in score["signals"]):
        warnings.append("No earnings-calendar signals were available from configured providers.")
    if not any(signal["signal_type"] == "dividend" for score in scores for signal in score["signals"]):
        warnings.append("No dividend-calendar signals were available from configured providers.")
    return warnings


def _is_publication_allowed(analysis: dict[str, Any]) -> bool:
    if analysis.get("analysis_method", "ai") == "fallback_heuristic":
        return bool(analysis.get("publication_allowed", False))
    return bool(analysis.get("publication_allowed", True))


def _fallback_policy_summary(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    fallback_analyses = [
        analysis
        for analysis in analyses
        if analysis.get("analysis_method") == "fallback_heuristic"
    ]
    suppressed = [
        analysis
        for analysis in fallback_analyses
        if analysis.get("recommendation") in {"BUY", "SELL"}
        and not _is_publication_allowed(analysis)
    ]
    fallback_reason_counts: dict[str, int] = {}
    for analysis in fallback_analyses:
        reason = str(analysis.get("fallback_reason") or "unknown")
        fallback_reason_counts[reason] = fallback_reason_counts.get(reason, 0) + 1

    return {
        "fallback_actionable_recommendations_allowed": (
            ALLOW_FALLBACK_ACTIONABLE_RECOMMENDATIONS
        ),
        "fallback_confidence_cap": FALLBACK_CONFIDENCE_CAP,
        "fallback_analysis_count": len(fallback_analyses),
        "suppressed_fallback_count": len(suppressed),
        "fallback_reason_counts": fallback_reason_counts,
        "fallback_tickers": [analysis["ticker"] for analysis in fallback_analyses],
        "suppressed_fallback_tickers": [analysis["ticker"] for analysis in suppressed],
    }


def _review_policy_summary(analyses: list[dict[str, Any]]) -> dict[str, Any]:
    reviewed = [analysis for analysis in analyses if analysis.get("ai_review")]
    suppressed = [
        analysis
        for analysis in reviewed
        if analysis.get("recommendation") in {"BUY", "SELL"}
        and not _is_publication_allowed(analysis)
    ]
    status_counts: dict[str, int] = {}
    for analysis in reviewed:
        status = str(analysis.get("ai_review", {}).get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "analysis_model": OPENAI_ANALYSIS_MODEL,
        "review_model": OPENAI_REVIEW_MODEL,
        "reviewed_count": len(reviewed),
        "approved_count": status_counts.get("approved", 0),
        "rejected_count": status_counts.get("rejected", 0),
        "review_error_count": status_counts.get("error", 0),
        "review_unavailable_count": status_counts.get("unavailable", 0),
        "review_suppressed_count": len(suppressed),
        "review_status_counts": status_counts,
        "reviewed_tickers": [analysis["ticker"] for analysis in reviewed],
        "review_suppressed_tickers": [analysis["ticker"] for analysis in suppressed],
        "review_rejection_audit_count": len(suppressed),
    }


def _with_recommendation_context(
    row: dict[str, Any], ticker: str, run_date: date
) -> dict[str, Any]:
    enriched = {
        **row,
        "related_news": _related_news_for_ticker(ticker, run_date),
        "upcoming_events": _upcoming_events_for_ticker(ticker, run_date),
    }
    chart = _price_chart_for_ticker(ticker, run_date)
    if chart:
        enriched["price_chart"] = chart
    return enriched


def _stock_logo_url(stock: dict[str, Any]) -> str | None:
    value = stock.get("logo_icon_url") or stock.get("logo_url") or stock.get("logo")
    return str(value).strip() if value else None


def _company_info(stock: dict[str, Any]) -> dict[str, Any]:
    """Return compact source-backed company context for recommendation cards."""
    products = _metadata_list(stock.get("flagship_products"))
    revenue_segments = _metadata_list(stock.get("revenue_segments"))
    risks = _metadata_list(stock.get("key_static_risks"))
    info = {
        "description": _metadata_text(stock.get("business_description")),
        "top_products": products,
        "revenue_segments": revenue_segments,
        "industry": _metadata_text(stock.get("industry")),
        "exchange": _metadata_text(stock.get("exchange")),
        "currency": _metadata_text(stock.get("currency")),
        "country": _metadata_text(stock.get("country")),
        "website": _metadata_text(stock.get("website")),
        "founded_year": _jsonable_value(stock.get("founded_year")),
        "headquarters": _metadata_text(stock.get("headquarters")),
        "ipo_year": _jsonable_value(stock.get("ipo_year")),
        "market_cap": _jsonable_value(stock.get("market_cap")),
        "competitive_position": _metadata_text(stock.get("competitive_position")),
        "key_static_risks": risks,
        "metadata_source": _metadata_text(stock.get("metadata_source")),
        "metadata_source_url": _metadata_text(stock.get("metadata_source_url")),
        "metadata_as_of": _metadata_text(stock.get("metadata_as_of")),
        "logo_url": _metadata_text(stock.get("logo_url")),
        "logo_icon_url": _metadata_text(stock.get("logo_icon_url")),
        "logo_source": _metadata_text(stock.get("logo_source")),
        "logo_source_url": _metadata_text(stock.get("logo_source_url")),
        "logo_checked_at": _metadata_text(stock.get("logo_checked_at")),
    }
    history_parts = []
    if info.get("founded_year"):
        history_parts.append(f"Founded in {info['founded_year']}")
    if info.get("headquarters"):
        history_parts.append(f"headquartered in {info['headquarters']}")
    if info.get("ipo_year"):
        history_parts.append(f"IPO in {info['ipo_year']}")
    if history_parts:
        info["brief_history"] = "; ".join(history_parts) + "."
    return {key: value for key, value in info.items() if value not in (None, "", [])}


def _metadata_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decision_grade_metadata_gaps(stock: dict[str, Any]) -> list[str]:
    return [
        field
        for field in DECISION_GRADE_REQUIRED_METADATA_FIELDS
        if not _metadata_text(stock.get(field))
    ]


def _metadata_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split("|") if part.strip()]


def _related_news_for_ticker(ticker: str, run_date: date) -> list[dict[str, Any]]:
    try:
        rows = store.news_for_ticker(
            ticker,
            run_date - timedelta(days=RELATED_NEWS_LOOKBACK_DAYS),
            run_date,
        )
    except Exception as exc:
        logger.warning("related_news_lookup_failed", ticker=ticker, error=str(exc))
        return []

    articles: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("title", "")).strip().lower(), str(row.get("source", "")).strip().lower())
        if key in seen or not key[0]:
            continue
        seen.add(key)
        article = {
            "title": str(row.get("title", "")),
            "source": str(row.get("source", "")),
            "published_at": row.get("published_at"),
            "summary": str(row.get("summary", "")),
            "sentiment": str(row.get("sentiment", "neutral")),
        }
        if row.get("url"):
            article["url"] = str(row["url"])
        articles.append(article)
        if len(articles) >= RELATED_NEWS_MAX_ARTICLES:
            break
    return articles


def _upcoming_events_for_ticker(ticker: str, run_date: date) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        earnings = store.earnings_events_for_ticker(
            ticker,
            run_date,
            run_date + timedelta(days=EARNINGS_LOOKAHEAD_DAYS),
        )
    except Exception as exc:
        logger.warning("recommendation_earnings_events_lookup_failed", ticker=ticker, error=str(exc))
        earnings = []
    for event in earnings:
        if not event.get("is_upcoming"):
            continue
        event_date = _parse_date(event.get("event_date"))
        if event_date is None:
            continue
        details = {
            key: _jsonable_value(event.get(key))
            for key in ("eps_estimate", "time_of_day")
            if event.get(key) is not None
        }
        events.append(
            {
                "event_type": "earnings",
                "event_date": event_date.isoformat(),
                "title": "Upcoming earnings",
                "provider": event.get("provider"),
                "source_url": event.get("source_url"),
                "details": details,
            }
        )

    try:
        dividends = store.dividend_events_for_ticker(
            ticker,
            run_date,
            run_date + timedelta(days=DIVIDEND_LOOKAHEAD_DAYS),
        )
    except Exception as exc:
        logger.warning("recommendation_dividend_events_lookup_failed", ticker=ticker, error=str(exc))
        dividends = []
    for event in dividends:
        if not event.get("is_upcoming"):
            continue
        event_date = _parse_date(event.get("ex_dividend_date"))
        if event_date is None:
            continue
        details = {
            key: _jsonable_value(event.get(key))
            for key in ("dividend_amount", "dividend_yield", "pay_date")
            if event.get(key) is not None
        }
        events.append(
            {
                "event_type": "dividend",
                "event_date": event_date.isoformat(),
                "title": "Upcoming ex-dividend date",
                "provider": event.get("provider"),
                "source_url": event.get("source_url"),
                "details": details,
            }
        )
    return sorted(events, key=lambda item: (item["event_date"], item["event_type"]))[:6]


def _price_chart_for_ticker(ticker: str, run_date: date) -> dict[str, Any] | None:
    """Return compact static OHLCV chart data for the public artifact."""
    try:
        rows = store.get_stock_data(
            ticker,
            run_date
            - timedelta(days=PRICE_CHART_LOOKBACK_DAYS + PRICE_CHART_SMA_WINDOW * 3),
            run_date,
        )
    except Exception as exc:
        logger.warning("price_chart_lookup_failed", ticker=ticker, error=str(exc))
        return None

    ordered = _ordered_stock_rows(rows)
    visible_rows = ordered[-PRICE_CHART_MAX_ROWS:]
    if len(visible_rows) < 2:
        return None

    candles: list[dict[str, Any]] = []
    all_candles: list[dict[str, Any]] = []
    for row in ordered:
        try:
            all_candles.append(
                {
                    "date": str(row["trading_date"])[:10],
                    "open": _jsonable_value(row["open_price"]),
                    "high": _jsonable_value(row["high_price"]),
                    "low": _jsonable_value(row["low_price"]),
                    "close": _jsonable_value(_analysis_close_price(row)),
                    "volume": int(row.get("volume", 0)),
                }
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            logger.warning(
                "price_chart_row_skipped",
                ticker=ticker,
                trading_date=row.get("trading_date"),
                error=str(exc),
            )
    visible_dates = {str(row["trading_date"])[:10] for row in visible_rows}
    candles = [candle for candle in all_candles if candle["date"] in visible_dates]
    if len(candles) < 2:
        return None

    all_closes = [Decimal(str(candle["close"])) for candle in all_candles]
    closes = [Decimal(str(candle["close"])) for candle in candles]
    lows = [Decimal(str(candle["low"])) for candle in candles]
    highs = [Decimal(str(candle["high"])) for candle in candles]
    sma_20 = _sma_chart_points(all_candles, all_closes, PRICE_CHART_SMA_WINDOW, visible_dates)
    trend_line = _trend_line_for_candles(candles, closes)
    support_window = lows[-20:] if len(lows) >= 20 else lows
    resistance_window = highs[-20:] if len(highs) >= 20 else highs

    latest = visible_rows[-1]
    return {
        "period_start": candles[0]["date"],
        "period_end": candles[-1]["date"],
        "currency": latest.get("currency"),
        "candles": candles,
        "sma_20": sma_20,
        "trend_line": trend_line,
        "support": _jsonable_value(min(support_window)) if support_window else None,
        "resistance": _jsonable_value(max(resistance_window)) if resistance_window else None,
    }


def _sma_chart_points(
    candles: list[dict[str, Any]],
    closes: list[Decimal],
    window: int,
    visible_dates: set[str] | None = None,
) -> list[dict[str, Any]]:
    if len(closes) < window:
        return []
    points = []
    for index in range(window - 1, len(closes)):
        if visible_dates is not None and candles[index]["date"] not in visible_dates:
            continue
        value = _average_decimal(closes[index - window + 1 : index + 1])
        points.append({"date": candles[index]["date"], "value": _jsonable_value(value)})
    return points


def _trend_line_for_candles(
    candles: list[dict[str, Any]], closes: list[Decimal]
) -> dict[str, Any] | None:
    if len(closes) < 2:
        return None
    count = Decimal(len(closes))
    xs = [Decimal(index) for index in range(len(closes))]
    sum_x = sum(xs, Decimal("0"))
    sum_y = sum(closes, Decimal("0"))
    sum_xy = sum((x * y for x, y in zip(xs, closes)), Decimal("0"))
    sum_x2 = sum((x * x for x in xs), Decimal("0"))
    denominator = count * sum_x2 - sum_x * sum_x
    if denominator == 0:
        return None
    slope = (count * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / count
    end_x = Decimal(len(closes) - 1)
    return {
        "start_date": candles[0]["date"],
        "start_value": _jsonable_value(intercept),
        "end_date": candles[-1]["date"],
        "end_value": _jsonable_value(intercept + slope * end_x),
        "slope_per_session": _jsonable_value(slope),
    }


def _review_rejection_audit(
    analyses: list[dict[str, Any]], stock_map: dict[str, dict[str, Any]], run_date: date
) -> list[dict[str, Any]]:
    """Return analyst/reviewer details for withheld AI recommendations."""
    suppressed = [
        analysis
        for analysis in analyses
        if analysis.get("ai_review")
        and analysis.get("recommendation") in {"BUY", "SELL"}
        and not _is_publication_allowed(analysis)
    ]
    suppressed.sort(
        key=lambda row: (
            row.get("recommendation") == "BUY",
            row.get("opportunity_score", 0),
            row.get("negative_score", 0),
            row.get("confidence_score", 0),
        ),
        reverse=True,
    )

    audit_rows: list[dict[str, Any]] = []
    for row in suppressed[:50]:
        stock = stock_map.get(row["ticker"], {})
        signals = row.get("signals", [])
        audit_rows.append(
            _with_recommendation_context(
                {
                    "ticker": row["ticker"],
                    "company_name": stock.get("company_name", row["ticker"]),
                    "sector": stock.get("sector"),
                    "logo_url": _stock_logo_url(stock),
                    "company_info": _company_info(stock),
                    "analysis_method": row.get("analysis_method", "ai"),
                    "analysis_model": row.get("analysis_model", OPENAI_ANALYSIS_MODEL),
                    "recommendation": row["recommendation"],
                    "risk_level": row["risk_level"],
                    "confidence_score": row["confidence_score"],
                    "opportunity_score": row["opportunity_score"],
                    "negative_score": row["negative_score"],
                    "catalyst": row["catalyst"],
                    "analyst_reasoning": row["reasoning"],
                    "invalidation_criteria": row["invalidation_criteria"],
                    "supporting_evidence": _evidence(signals),
                    "source_traceability": [signal["source"] for signal in signals[:5] if signal.get("source")],
                    "needed_evidence": _needed_evidence_plan(row, stock),
                    "ai_review": row.get("ai_review"),
                },
                row["ticker"],
                run_date,
            )
        )
    return audit_rows


def _needed_evidence_plan(
    analysis: dict[str, Any], stock: dict[str, Any]
) -> list[dict[str, Any]]:
    review = analysis.get("ai_review") or {}
    text = " ".join(
        str(part or "")
        for part in [
            review.get("what_would_make_approvable"),
            review.get("rationale"),
            " ".join(review.get("concerns") or []),
        ]
    ).lower()
    gaps: list[dict[str, Any]] = []

    def add(
        gap_type: str,
        title: str,
        collection_plan: str,
        source_candidates: list[str],
        status: str = "needed",
    ) -> None:
        if any(row["gap_type"] == gap_type for row in gaps):
            return
        gaps.append(
            {
                "gap_type": gap_type,
                "title": title,
                "status": status,
                "collection_plan": collection_plan,
                "source_candidates": source_candidates,
            }
        )

    if "8-k" in text or "filing" in text:
        add(
            "sec_8k_substance",
            "SEC filing substance",
            "Fetch the latest 8-K filing text, extract item numbers and material business terms, and summarize why the filing is bullish, bearish, or neutral.",
            ["SEC submissions API", "SEC filing text", "company investor relations"],
        )
    if any(term in text for term in ["valuation", "peer", "revenue", "margin", "guidance", "fundamental", "pipeline"]):
        add(
            "fundamental_valuation_context",
            "Fundamental and valuation context",
            "Collect recent revenue, margin, guidance, valuation multiple, peer/history comparison, and durable downside-risk context before publishing an actionable call.",
            ["SEC 10-Q/10-K", "earnings release", "earnings transcript", "Financial Modeling Prep", "Finnhub fundamentals"],
        )
    if any(term in text for term in ["sector", "metadata", "company identification", "ticker/company", "identification is"]):
        add(
            "metadata_quality",
            "Company metadata verification",
            "Verify ticker, company name, sector, industry, exchange, and profile source before the candidate can be treated as decision-grade.",
            ["watchlist metadata seed", "Nasdaq company profile", "SEC company tickers"],
        )
    if any(term in text for term in ["company-specific", "specific news", "negative catalyst", "material contract", "business impact", "unrelated"]):
        add(
            "company_specific_catalyst",
            "Company-specific catalyst verification",
            "Collect ticker-specific news or event evidence and reject broad market articles that do not materially explain this company.",
            ["NewsAPI company query", "Finnhub company news", "company press releases"],
        )
    if any(term in text for term in ["momentum", "breakout", "support", "resistance", "technical", "follow-through", "time horizon", "mean-revert"]):
        add(
            "technical_confirmation",
            "Technical confirmation and trade horizon",
            "Add support/resistance, breakout or breakdown level, invalidation price, multi-session volume confirmation, relative performance, and explicit trade horizon.",
            ["stored OHLCV", "sector ETF context", "relative strength calculations"],
        )
    if any(term in text for term in ["analyst", "upgrade", "target", "estimate cut", "estimate cuts"]):
        add(
            "analyst_action_recency",
            "Recent analyst action context",
            "Distinguish stale recommendation mix from recent upgrades, downgrades, estimate revisions, and price-target changes.",
            ["Finnhub recommendation trends", "Finnhub upgrade/downgrade", "price-target provider"],
        )
    if any(term in text for term in ["accounting", "regulatory", "order", "orders"]):
        add(
            "negative_event_detail",
            "Negative event detail",
            "Collect specific adverse company evidence such as accounting, regulatory, order, margin, or guidance deterioration before publishing a SELL.",
            ["company news", "SEC filings", "analyst estimate revisions"],
        )

    if not gaps and review.get("what_would_make_approvable"):
        add(
            "reviewer_requested_evidence",
            "Reviewer-requested evidence",
            "Turn the reviewer note into a specific collector task or source-backed manual research item.",
            ["review model note"],
            status="temporary_suspended",
        )
    return gaps


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif not value:
        return None
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _jsonable_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


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
