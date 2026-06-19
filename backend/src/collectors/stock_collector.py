"""Stock Data Collector Lambda handler.

Fetches daily OHLCV data for all monitored stocks from yfinance (primary)
with Alpha Vantage as fallback. Triggered by EventBridge daily at 21:00 UTC.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7
"""

import os
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import boto3
import requests
import structlog
import yfinance as yf

from src.db.connection import DatabasePool, store

logger = structlog.get_logger(__name__)

# Configuration
ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"
NASDAQ_HISTORICAL_BASE_URL = os.environ.get(
    "NASDAQ_HISTORICAL_BASE_URL",
    "https://api.nasdaq.com/api/quote/{ticker}/historical",
)
STOOQ_BASE_URL = os.environ.get("STOOQ_BASE_URL", "https://stooq.com/q/d/l/")
NASDAQ_MAX_RECORDS_PER_TICKER = int(
    os.environ.get("NASDAQ_MAX_RECORDS_PER_TICKER", "90")
)
NASDAQ_HISTORICAL_MAX_RECORDS_PER_TICKER = int(
    os.environ.get("NASDAQ_HISTORICAL_MAX_RECORDS_PER_TICKER", "1500")
)
STOOQ_MAX_RECORDS_PER_TICKER = int(os.environ.get("STOOQ_MAX_RECORDS_PER_TICKER", "90"))
STOOQ_HISTORICAL_MAX_RECORDS_PER_TICKER = int(
    os.environ.get("STOOQ_HISTORICAL_MAX_RECORDS_PER_TICKER", "1500")
)
DEFAULT_MARKET_DATA_CURRENCY = os.environ.get("STOCK_DATA_DEFAULT_CURRENCY", "USD")
STOCK_HISTORY_BUCKET = os.environ.get(
    "STOCKARA_STOCK_HISTORY_BUCKET",
    os.environ.get("STOCKARA_ARTIFACT_BUCKET", ""),
)
STOCK_HISTORY_PREFIX = os.environ.get("STOCK_HISTORY_PREFIX", "stock-history")
STOOQ_BACKFILL_PREFIX = os.environ.get(
    "STOOQ_BACKFILL_PREFIX", f"{STOCK_HISTORY_PREFIX}/stooq-upload"
)
STOOQ_BACKFILL_FILES_PER_RUN = int(os.environ.get("STOOQ_BACKFILL_FILES_PER_RUN", "1"))
STOOQ_BACKFILL_RECORDS_PER_FILE = int(
    os.environ.get("STOOQ_BACKFILL_RECORDS_PER_FILE", "120")
)
BATCH_SIZE = int(os.environ.get("STOCK_COLLECTOR_BATCH_SIZE", "5"))
MAX_TICKERS_PER_RUN = int(os.environ.get("STOCK_COLLECTOR_MAX_TICKERS", "25"))
HISTORICAL_BACKFILL_TICKERS_PER_RUN = int(
    os.environ.get("STOCK_HISTORICAL_BACKFILL_TICKERS_PER_RUN", "1")
)
HISTORICAL_BACKFILL_MAX_CHAINED_INVOCATIONS = int(
    os.environ.get("STOCK_HISTORICAL_BACKFILL_MAX_CHAINED_INVOCATIONS", "1500")
)
INITIAL_HISTORY_PERIOD = os.environ.get("STOCK_INITIAL_HISTORY_PERIOD", "5y")
INCREMENTAL_PERIOD = os.environ.get("STOCK_INCREMENTAL_PERIOD", "10d")
YFINANCE_BATCH_PAUSE_SECONDS = float(os.environ.get("YFINANCE_BATCH_PAUSE_SECONDS", "1"))
STOCK_COLLECTION_MIN_COMPLETENESS = float(
    os.environ.get("STOCK_COLLECTION_MIN_COMPLETENESS", "0.9")
)
STOCK_FAILED_RETRY_AFTER_HOURS = int(
    os.environ.get("STOCK_FAILED_RETRY_AFTER_HOURS", "6")
)
STOCK_COLLECTOR_MIN_REMAINING_SECONDS = int(
    os.environ.get("STOCK_COLLECTOR_MIN_REMAINING_SECONDS", "120")
)
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 2
CLOUDWATCH_NAMESPACE = "StockMonitoring"


@dataclass
class ExtractResult:
    records: list[dict[str, Any]] = field(default_factory=list)
    failure_reason: str | None = None


@dataclass
class StoreResult:
    inserted_records: int = 0
    duplicate_records: int = 0
    failed_records: int = 0


@dataclass
class BatchResult:
    records_inserted: int = 0
    duplicate_records: int = 0
    failed_records: int = 0
    failed_tickers: list[str] = field(default_factory=list)
    malformed_tickers: list[str] = field(default_factory=list)
    no_data_tickers: list[str] = field(default_factory=list)
    collected_tickers: set[str] = field(default_factory=set)


def handler(event: dict, context: Any) -> dict:
    """Lambda handler for stock data collection.

    Triggered by EventBridge daily after market close.
    Fetches OHLCV data for all active stocks in the watchlist.
    """
    log = logger.bind(lambda_event=event)
    log.info("stock_collector_started")

    try:
        DatabasePool.initialize()
        stocks = _fetch_watchlist()

        if not stocks:
            log.warning("no_active_tickers_found")
            _record_collection_summary(
                _build_collection_summary(
                    active_ticker_count=0,
                    selected_ticker_count=0,
                    records_collected=0,
                    failed_tickers=[],
                )
            )
            return {"statusCode": 200, "body": "No active tickers to collect"}

        if (event or {}).get("mode") == "historical_backfill":
            return _run_historical_backfill(stocks, event or {}, context)
        if (event or {}).get("mode") == "stooq_s3_backfill":
            return _run_stooq_s3_backfill(stocks, event or {}, context)

        selected_stocks = _select_due_stocks(stocks, event or {})
        if not selected_stocks:
            log.info("no_due_tickers_found", active_ticker_count=len(stocks))
            _record_collection_summary(
                _build_collection_summary(
                    active_ticker_count=len(stocks),
                    selected_ticker_count=0,
                    records_collected=0,
                    failed_tickers=[],
                )
            )
            return {"statusCode": 200, "body": "No due tickers to collect"}

        log.info(
            "watchlist_loaded",
            active_ticker_count=len(stocks),
            selected_ticker_count=len(selected_stocks),
            batch_size=BATCH_SIZE,
        )

        collected_count = 0
        duplicate_count = 0
        malformed_tickers: list[str] = []
        no_data_tickers: list[str] = []
        failed_tickers: list[str] = []
        fallback_succeeded: set[str] = set()
        collected_tickers: set[str] = set()
        stock_metadata_by_ticker = {
            stock["ticker"]: stock for stock in selected_stocks
        }
        attempted_tickers: set[str] = set()
        stopped_for_time = False

        for period, period_stocks in _group_stocks_by_period(selected_stocks).items():
            tickers = [stock["ticker"] for stock in period_stocks]
            batches = [tickers[i : i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
            for batch_idx, batch in enumerate(batches):
                if _should_stop_for_time(context):
                    stopped_for_time = True
                    log.warning(
                        "stock_collector_soft_deadline_reached",
                        phase="primary",
                        remaining_seconds=_remaining_seconds(context),
                        deferred_ticker_count=len(selected_stocks) - len(attempted_tickers),
                    )
                    break
                log.info(
                    "processing_batch",
                    batch_index=batch_idx,
                    batch_size=len(batch),
                    period=period,
                )
                attempted_tickers.update(batch)
                batch_result = _process_batch(
                    batch,
                    period=period,
                    stock_metadata_by_ticker=stock_metadata_by_ticker,
                )
                collected_count += batch_result.records_inserted
                duplicate_count += batch_result.duplicate_records
                failed_tickers.extend(batch_result.failed_tickers)
                malformed_tickers.extend(batch_result.malformed_tickers)
                no_data_tickers.extend(batch_result.no_data_tickers)
                collected_tickers.update(batch_result.collected_tickers)
                if YFINANCE_BATCH_PAUSE_SECONDS > 0:
                    time.sleep(YFINANCE_BATCH_PAUSE_SECONDS)
            if stopped_for_time:
                break

        # Attempt fallback providers for failed tickers
        if failed_tickers and not _should_stop_for_time(context):
            log.info("market_data_fallback_starting", failed_count=len(failed_tickers))
            fallback_result, fallback_succeeded = _alpha_vantage_fallback_with_details(
                failed_tickers,
                stock_metadata_by_ticker=stock_metadata_by_ticker,
            )
            collected_count += fallback_result.inserted_records
            duplicate_count += fallback_result.duplicate_records
            collected_tickers.update(fallback_succeeded)
            remaining_tickers = sorted(set(failed_tickers) - fallback_succeeded)

            if remaining_tickers:
                nasdaq_result, nasdaq_succeeded = _nasdaq_fallback_with_details(
                    remaining_tickers,
                    stock_metadata_by_ticker=stock_metadata_by_ticker,
                )
                collected_count += nasdaq_result.inserted_records
                duplicate_count += nasdaq_result.duplicate_records
                fallback_succeeded.update(nasdaq_succeeded)
                collected_tickers.update(nasdaq_succeeded)
                remaining_tickers = sorted(set(failed_tickers) - fallback_succeeded)

            if remaining_tickers:
                stooq_result, stooq_succeeded = _stooq_fallback_with_details(
                    remaining_tickers,
                    stock_metadata_by_ticker=stock_metadata_by_ticker,
                )
                collected_count += stooq_result.inserted_records
                duplicate_count += stooq_result.duplicate_records
                fallback_succeeded.update(stooq_succeeded)
                collected_tickers.update(stooq_succeeded)

            remaining_failures = len(set(failed_tickers) - fallback_succeeded)

            if remaining_failures > 0:
                log.error(
                    "tickers_failed_all_providers",
                    remaining_failures=remaining_failures,
                )
        elif failed_tickers:
            stopped_for_time = True
            log.warning(
                "stock_collector_soft_deadline_reached",
                phase="fallback",
                remaining_seconds=_remaining_seconds(context),
                fallback_deferred_ticker_count=len(set(failed_tickers)),
            )

        remaining_failed_tickers = sorted(set(failed_tickers) - fallback_succeeded)
        attempted_count = len(attempted_tickers)
        deferred_count = len(selected_stocks) - attempted_count
        summary = _build_collection_summary(
            active_ticker_count=len(stocks),
            selected_ticker_count=attempted_count,
            records_collected=collected_count,
            duplicate_records=duplicate_count,
            malformed_tickers=malformed_tickers,
            no_data_tickers=no_data_tickers,
            recovered_tickers=sorted(collected_tickers),
            failed_tickers=remaining_failed_tickers,
        )
        _record_collection_summary(summary)
        _record_failed_ticker_state(summary)

        # Emit CloudWatch metric
        _emit_metric("stocks_collected", collected_count)
        _emit_collection_summary_metrics(summary)
        movement_signal_count = _compute_and_store_movement_signals(collected_tickers)
        _emit_metric("market_movement_signals_collected", movement_signal_count)

        log.info(
            "stock_collector_completed",
            collected=collected_count,
            failed=len(remaining_failed_tickers),
            selected=len(selected_stocks),
            attempted=attempted_count,
            deferred=deferred_count,
            stopped_for_time=stopped_for_time,
            completeness_ratio=summary["completeness_ratio"],
        )

        return {
            "statusCode": 200,
            "body": (
                f"Collected {collected_count} new records for "
                f"{attempted_count} attempted ticker(s); "
                f"{deferred_count} deferred for a later run"
            ),
        }

    except Exception as e:
        log.error("stock_collector_failed", error=str(e), exc_info=True)
        _emit_metric("stocks_collected", 0)
        raise
    finally:
        DatabasePool.close()


def _fetch_watchlist() -> list[dict[str, Any]]:
    """Fetch active stock metadata from the stocks watchlist table."""
    return store.active_stock_metadata()


def _run_historical_backfill(
    stocks: list[dict[str, Any]], event: dict[str, Any], context: Any
) -> dict[str, Any]:
    max_tickers = int(event.get("max_tickers", HISTORICAL_BACKFILL_TICKERS_PER_RUN))
    selected = _select_historical_backfill_stocks(stocks, event, max_tickers)
    run_id = str(
        event.get("backfill_run_id")
        or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    event["backfill_run_id"] = run_id
    processed_tickers = {
        str(ticker).upper()
        for ticker in event.get("processed_tickers", [])
    }
    if not selected:
        return {
            "statusCode": 200,
            "body": {
                "mode": "historical_backfill",
                "status": "complete",
                "processed_count": 0,
                "message": "No tickers need historical backfill",
            },
        }

    archived = 0
    restored = 0
    fetched = 0
    failed: list[str] = []
    no_data: list[str] = []
    collected_tickers: set[str] = set()
    inserted_records = 0
    duplicate_records = 0
    failed_records = 0

    for stock in selected:
        if _should_stop_for_time(context):
            break
        ticker = stock["ticker"]
        archive_records = _load_history_archive(ticker) or []
        records_to_store: list[dict[str, Any]] = []
        fetched_missing_records: list[dict[str, Any]] = []
        restored_from_archive = bool(archive_records)
        if archive_records:
            restored += 1
            needs_history_restore = _stock_metadata_needs_history_restore(stock)
            earliest_archive_date = _earliest_history_record_date(archive_records)
            latest_archive_date = _latest_history_record_date(archive_records)
            if _history_archive_needs_backfill(earliest_archive_date):
                fetched_missing_records = _fetch_historical_records(ticker, stock) or []
                if fetched_missing_records:
                    fetched += 1
                    records_to_store = _merge_record_lists(
                        archive_records,
                        fetched_missing_records,
                    )
                elif needs_history_restore:
                    records_to_store = archive_records
            elif _history_archive_needs_update(latest_archive_date):
                fetched_missing_records = _fetch_missing_historical_records(
                    ticker,
                    stock,
                    latest_archive_date,
                )
                if fetched_missing_records:
                    fetched += 1
                    if _stock_metadata_needs_history_restore(stock):
                        records_to_store = _merge_record_lists(
                            archive_records,
                            fetched_missing_records,
                        )
                    else:
                        records_to_store = fetched_missing_records
                elif needs_history_restore:
                    records_to_store = archive_records
            elif needs_history_restore:
                records_to_store = archive_records
        else:
            records_to_store = _fetch_historical_records(ticker, stock) or []
            if records_to_store:
                fetched += 1
        if not records_to_store:
            processed_tickers.add(ticker)
            if archive_records:
                collected_tickers.add(ticker)
            else:
                failed.append(ticker)
                no_data.append(ticker)
            continue
        stored = _store_records(records_to_store)
        inserted_records += stored.inserted_records
        duplicate_records += stored.duplicate_records
        failed_records += stored.failed_records
        if stored.inserted_records > 0 or stored.duplicate_records > 0:
            collected_tickers.add(ticker)
            if not restored_from_archive or fetched_missing_records:
                archived += 1
        else:
            failed.append(ticker)
        processed_tickers.add(ticker)

    summary = _build_collection_summary(
        active_ticker_count=len(stocks),
        selected_ticker_count=len(selected),
        records_collected=inserted_records,
        duplicate_records=duplicate_records,
        failed_tickers=failed,
        no_data_tickers=no_data,
        recovered_tickers=sorted(collected_tickers),
    )
    summary["mode"] = "historical_backfill"
    summary["s3_archives_written"] = archived
    summary["s3_archives_restored"] = restored
    summary["provider_fetches"] = fetched
    summary["failed_record_count"] = failed_records
    _record_collection_summary(summary)
    _record_failed_ticker_state(summary)
    _emit_metric("historical_backfill_archives_written", archived)
    _emit_metric("historical_backfill_archives_restored", restored)
    _emit_metric("historical_backfill_provider_fetches", fetched)
    _emit_collection_summary_metrics(summary)
    if collected_tickers:
        movement_signal_count = _compute_and_store_movement_signals(collected_tickers)
        _emit_metric("market_movement_signals_collected", movement_signal_count)

    should_continue = bool(event.get("continue_backfill", False))
    invocation_count = int(event.get("invocation_count", 0))
    continue_queued = False
    if (
        should_continue
        and invocation_count < HISTORICAL_BACKFILL_MAX_CHAINED_INVOCATIONS
        and _has_more_historical_backfill_work(stocks, event, selected)
    ):
        continue_queued = _invoke_next_historical_backfill(
            event,
            invocation_count + 1,
            processed_tickers,
        )

    _record_historical_backfill_status(
        run_id=run_id,
        active_ticker_count=len(stocks),
        selected_tickers=[stock["ticker"] for stock in selected],
        processed_tickers=processed_tickers,
        inserted_records=inserted_records,
        duplicate_records=duplicate_records,
        failed_records=failed_records,
        failed_tickers=failed,
        archived=archived,
        restored=restored,
        fetched=fetched,
        invocation_count=invocation_count,
        continue_queued=continue_queued,
        complete=not continue_queued
        and not _has_more_historical_backfill_work(stocks, event, selected),
    )

    return {
        "statusCode": 200,
        "body": {
            "mode": "historical_backfill",
            "backfill_run_id": run_id,
            "processed_tickers": [stock["ticker"] for stock in selected],
            "processed_total": len(processed_tickers),
            "active_ticker_count": len(stocks),
            "records_inserted": inserted_records,
            "duplicate_records": duplicate_records,
            "s3_archives_written": archived,
            "s3_archives_restored": restored,
            "provider_fetches": fetched,
            "failed_tickers": failed,
            "continue_queued": continue_queued,
        },
    }


def _run_stooq_s3_backfill(
    stocks: list[dict[str, Any]], event: dict[str, Any], context: Any
) -> dict[str, Any]:
    bucket = event.get("bucket") or STOCK_HISTORY_BUCKET
    prefix = event.get("s3_prefix") or STOOQ_BACKFILL_PREFIX
    if not bucket:
        return {
            "statusCode": 400,
            "body": {
                "mode": "stooq_s3_backfill",
                "status": "failed",
                "message": "No S3 bucket configured for Stooq backfill",
            },
        }

    stock_by_ticker = {stock["ticker"].upper(): stock for stock in stocks}
    requested_tickers = {
        str(ticker).upper()
        for ticker in event.get("tickers", [])
    }
    max_files = int(event.get("max_files", STOOQ_BACKFILL_FILES_PER_RUN))
    max_records_per_file = int(
        event.get("max_records_per_file", STOOQ_BACKFILL_RECORDS_PER_FILE)
    )
    run_id = str(
        event.get("backfill_run_id")
        or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    continuation_token = event.get("continuation_token")
    start_after_key = event.get("start_after_key")
    processed_tickers = {
        str(ticker).upper()
        for ticker in event.get("processed_tickers", [])
    }

    s3 = boto3.client("s3")
    processed_files = 0
    skipped_files = 0
    inserted_records = 0
    duplicate_records = 0
    failed_records = 0
    malformed_files: list[str] = []
    unavailable_tickers: list[str] = []
    collected_tickers: set[str] = set()
    next_continuation_token = continuation_token
    next_start_after_key = start_after_key
    exhausted = False

    while processed_files < max_files and not _should_stop_for_time(context):
        list_kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Prefix": prefix,
            "MaxKeys": 1000,
        }
        if next_continuation_token:
            list_kwargs["ContinuationToken"] = next_continuation_token
        elif next_start_after_key:
            list_kwargs["StartAfter"] = next_start_after_key
        response = s3.list_objects_v2(**list_kwargs)
        keys = [
            item["Key"]
            for item in response.get("Contents", [])
            if item.get("Key") and str(item["Key"]).lower().endswith(".txt")
        ]
        next_continuation_token = response.get("NextContinuationToken")
        if not keys and not next_continuation_token:
            exhausted = True
            break

        for key in keys:
            if processed_files >= max_files:
                break
            next_start_after_key = key
            ticker_from_key = _stooq_backfill_ticker_from_key(key)
            if not ticker_from_key:
                skipped_files += 1
                continue
            if requested_tickers and ticker_from_key not in requested_tickers:
                skipped_files += 1
                continue
            if ticker_from_key not in stock_by_ticker:
                unavailable_tickers.append(ticker_from_key)
                skipped_files += 1
                continue
            object_response = s3.get_object(Bucket=bucket, Key=key)
            text = _decode_stooq_backfill_bytes(object_response["Body"].read(), key)
            records = _parse_stooq_backfill_txt(
                text,
                stock_metadata_by_ticker=stock_by_ticker,
            )
            records = _limit_stooq_backfill_records(records, max_records_per_file)
            if not records:
                malformed_files.append(key)
                skipped_files += 1
                continue

            ticker = records[0]["ticker"]
            if ticker != ticker_from_key:
                malformed_files.append(key)
                skipped_files += 1
                continue

            stored = _store_stooq_backfill_records(records)
            inserted_records += stored.inserted_records
            duplicate_records += stored.duplicate_records
            failed_records += stored.failed_records
            processed_tickers.add(ticker)
            processed_files += 1
            if stored.inserted_records > 0 or stored.duplicate_records > 0:
                collected_tickers.add(ticker)

        if (
            processed_files >= max_files
            and keys
            and next_start_after_key != keys[-1]
        ):
            next_continuation_token = None

        if not next_continuation_token and (
            not keys or next_start_after_key == keys[-1]
        ):
            exhausted = True
            break

    continue_queued = False
    if (
        bool(event.get("continue_backfill", False))
        and not exhausted
        and (next_continuation_token or next_start_after_key)
    ):
        continue_queued = _invoke_next_stooq_s3_backfill(
            event,
            next_continuation_token,
            next_start_after_key,
            processed_tickers,
            run_id,
        )

    summary = _build_collection_summary(
        active_ticker_count=len(stocks),
        selected_ticker_count=processed_files,
        records_collected=inserted_records,
        duplicate_records=duplicate_records,
        failed_tickers=sorted(set(unavailable_tickers)),
        malformed_tickers=malformed_files,
        recovered_tickers=sorted(collected_tickers),
    )
    summary["mode"] = "stooq_s3_backfill"
    summary["failed_record_count"] = failed_records
    summary["skipped_file_count"] = skipped_files
    _record_collection_summary(summary)
    _emit_collection_summary_metrics(summary)
    _emit_metric("stooq_s3_backfill_files_processed", processed_files)
    _emit_metric("stooq_s3_backfill_records_inserted", inserted_records)

    if collected_tickers and bool(event.get("compute_signals", False)):
        movement_signal_count = _compute_and_store_movement_signals(collected_tickers)
        _emit_metric("market_movement_signals_collected", movement_signal_count)

    _record_stooq_s3_backfill_status(
        run_id=run_id,
        bucket=bucket,
        prefix=prefix,
        processed_files=processed_files,
        skipped_files=skipped_files,
        processed_tickers=processed_tickers,
        inserted_records=inserted_records,
        duplicate_records=duplicate_records,
        failed_records=failed_records,
        malformed_files=malformed_files,
        unavailable_tickers=unavailable_tickers,
        continuation_token=next_continuation_token,
        start_after_key=next_start_after_key,
        continue_queued=continue_queued,
        complete=exhausted and not continue_queued,
    )

    return {
        "statusCode": 200,
        "body": {
            "mode": "stooq_s3_backfill",
            "backfill_run_id": run_id,
            "bucket": bucket,
            "s3_prefix": prefix,
            "processed_files": processed_files,
            "skipped_files": skipped_files,
            "processed_total": len(processed_tickers),
            "records_inserted": inserted_records,
            "duplicate_records": duplicate_records,
            "failed_records": failed_records,
            "malformed_files": malformed_files,
            "unavailable_tickers": sorted(set(unavailable_tickers)),
            "continuation_token": next_continuation_token,
            "start_after_key": next_start_after_key,
            "continue_queued": continue_queued,
            "complete": exhausted and not continue_queued,
        },
    }


def _invoke_next_stooq_s3_backfill(
    event: dict[str, Any],
    continuation_token: str | None,
    start_after_key: str | None,
    processed_tickers: set[str],
    run_id: str,
) -> bool:
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
    if not function_name:
        logger.warning("stooq_s3_backfill_continue_unavailable_no_function_name")
        return False
    payload = {
        **event,
        "mode": "stooq_s3_backfill",
        "continue_backfill": True,
        "continuation_token": continuation_token,
        "start_after_key": start_after_key,
        "processed_tickers": sorted(processed_tickers),
        "backfill_run_id": run_id,
    }
    try:
        boto3.client("lambda").invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        logger.info(
            "stooq_s3_backfill_continue_invoked",
            processed_ticker_count=len(processed_tickers),
        )
        return True
    except Exception as exc:
        logger.warning("stooq_s3_backfill_continue_invoke_failed", error=str(exc))
        return False


def _select_historical_backfill_stocks(
    stocks: list[dict[str, Any]], event: dict[str, Any], max_tickers: int
) -> list[dict[str, Any]]:
    requested = {ticker.upper() for ticker in event.get("tickers", [])}
    if requested:
        stocks = [stock for stock in stocks if stock["ticker"] in requested]
    processed = {
        str(ticker).upper()
        for ticker in event.get("processed_tickers", [])
    }
    scan_all = bool(event.get("scan_all", False))
    due = [
        stock
        for stock in stocks
        if stock["ticker"] not in processed
        and (scan_all or _needs_historical_backfill(stock))
    ]
    due.sort(
        key=lambda stock: (
            stock.get("latest_stock_data_date") is not None,
            stock.get("latest_stock_collection_failure_count", 0) or 0,
            stock["ticker"],
        )
    )
    return due[:max_tickers]


def _needs_historical_backfill(stock: dict[str, Any]) -> bool:
    if not stock.get("latest_stock_data_date"):
        return True
    row_count = stock.get("stock_history_row_count")
    return row_count is not None and int(row_count or 0) < 20


def _has_more_historical_backfill_work(
    stocks: list[dict[str, Any]],
    event: dict[str, Any],
    selected: list[dict[str, Any]],
) -> bool:
    processed_tickers = {
        str(ticker).upper()
        for ticker in event.get("processed_tickers", [])
    }
    processed_tickers.update(stock["ticker"] for stock in selected)
    scan_all = bool(event.get("scan_all", False))
    return any(
        stock["ticker"] not in processed_tickers
        and (scan_all or _needs_historical_backfill(stock))
        for stock in stocks
    )


def _remaining_seconds(context: Any) -> float | None:
    remaining = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(remaining):
        return None
    return remaining() / 1000


def _should_stop_for_time(context: Any) -> bool:
    remaining = _remaining_seconds(context)
    return (
        remaining is not None
        and remaining <= STOCK_COLLECTOR_MIN_REMAINING_SECONDS
    )


def _history_archive_key(ticker: str) -> str:
    safe_ticker = ticker.upper().replace("/", "-")
    return f"{STOCK_HISTORY_PREFIX}/{safe_ticker}.json"


def _load_history_archive(ticker: str) -> list[dict[str, Any]] | None:
    if not STOCK_HISTORY_BUCKET:
        return None
    try:
        response = boto3.client("s3").get_object(
            Bucket=STOCK_HISTORY_BUCKET,
            Key=_history_archive_key(ticker),
        )
        payload = json.loads(response["Body"].read().decode("utf-8"))
        records = payload.get("records", [])
        if records:
            logger.info(
                "stock_history_archive_restored",
                ticker=ticker,
                record_count=len(records),
            )
            return records
    except Exception as exc:
        error_code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if error_code not in {"NoSuchKey", "404", "NotFound"}:
            logger.warning(
                "stock_history_archive_load_failed",
                ticker=ticker,
                error=str(exc),
            )
    return None


def _put_history_archive(ticker: str, records: list[dict[str, Any]]) -> None:
    if not STOCK_HISTORY_BUCKET or not records:
        return
    body = json.dumps(
        {
            "ticker": ticker,
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "record_count": len(records),
            "records": [_jsonable_record(record) for record in records],
        },
        default=str,
    ).encode("utf-8")
    boto3.client("s3").put_object(
        Bucket=STOCK_HISTORY_BUCKET,
        Key=_history_archive_key(ticker),
        Body=body,
        ContentType="application/json",
        CacheControl="private, max-age=31536000, immutable",
    )
    logger.info(
        "stock_history_archive_written",
        ticker=ticker,
        record_count=len(records),
    )


def _record_historical_backfill_status(
    *,
    run_id: str,
    active_ticker_count: int,
    selected_tickers: list[str],
    processed_tickers: set[str],
    inserted_records: int,
    duplicate_records: int,
    failed_records: int,
    failed_tickers: list[str],
    archived: int,
    restored: int,
    fetched: int,
    invocation_count: int,
    continue_queued: bool,
    complete: bool,
) -> None:
    if not STOCK_HISTORY_BUCKET:
        return
    processed_count = len(processed_tickers)
    body = json.dumps(
        {
            "mode": "historical_backfill",
            "run_id": run_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "active_ticker_count": active_ticker_count,
            "processed_count": processed_count,
            "remaining_estimate": max(active_ticker_count - processed_count, 0),
            "progress_percent": (
                round((processed_count / active_ticker_count) * 100, 2)
                if active_ticker_count
                else 100.0
            ),
            "last_selected_tickers": selected_tickers,
            "last_failed_tickers": failed_tickers,
            "last_inserted_records": inserted_records,
            "last_duplicate_records": duplicate_records,
            "last_failed_records": failed_records,
            "last_s3_archives_written": archived,
            "last_s3_archives_restored": restored,
            "last_provider_fetches": fetched,
            "invocation_count": invocation_count,
            "max_chained_invocations": HISTORICAL_BACKFILL_MAX_CHAINED_INVOCATIONS,
            "continue_queued": continue_queued,
            "complete": complete,
            "processed_tickers_sample": sorted(processed_tickers)[-50:],
        },
        default=str,
    ).encode("utf-8")
    key = f"{STOCK_HISTORY_PREFIX}/_backfill/latest.json"
    try:
        boto3.client("s3").put_object(
            Bucket=STOCK_HISTORY_BUCKET,
            Key=key,
            Body=body,
            ContentType="application/json",
            CacheControl="no-store",
        )
        logger.info(
            "historical_backfill_status_written",
            key=key,
            processed_count=processed_count,
            active_ticker_count=active_ticker_count,
            continue_queued=continue_queued,
        )
    except Exception as exc:
        logger.warning("historical_backfill_status_write_failed", error=str(exc))


def _record_stooq_s3_backfill_status(
    *,
    run_id: str,
    bucket: str,
    prefix: str,
    processed_files: int,
    skipped_files: int,
    processed_tickers: set[str],
    inserted_records: int,
    duplicate_records: int,
    failed_records: int,
    malformed_files: list[str],
    unavailable_tickers: list[str],
    continuation_token: str | None,
    start_after_key: str | None,
    continue_queued: bool,
    complete: bool,
) -> None:
    if not STOCK_HISTORY_BUCKET:
        return
    body = json.dumps(
        {
            "mode": "stooq_s3_backfill",
            "run_id": run_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source_bucket": bucket,
            "source_prefix": prefix,
            "processed_files": processed_files,
            "skipped_files": skipped_files,
            "processed_ticker_count": len(processed_tickers),
            "inserted_records": inserted_records,
            "duplicate_records": duplicate_records,
            "failed_records": failed_records,
            "malformed_files_sample": malformed_files[-25:],
            "unavailable_tickers_sample": sorted(set(unavailable_tickers))[-50:],
            "continuation_token": continuation_token,
            "start_after_key": start_after_key,
            "continue_queued": continue_queued,
            "complete": complete,
            "processed_tickers_sample": sorted(processed_tickers)[-50:],
        },
        default=str,
    ).encode("utf-8")
    key = f"{STOCK_HISTORY_PREFIX}/_backfill/stooq-upload-latest.json"
    try:
        boto3.client("s3").put_object(
            Bucket=STOCK_HISTORY_BUCKET,
            Key=key,
            Body=body,
            ContentType="application/json",
            CacheControl="no-store",
        )
        logger.info(
            "stooq_s3_backfill_status_written",
            key=key,
            processed_files=processed_files,
            continue_queued=continue_queued,
        )
    except Exception as exc:
        logger.warning("stooq_s3_backfill_status_write_failed", error=str(exc))


def _merge_history_archive(ticker: str, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    existing_records = _load_history_archive(ticker) or []
    by_date = {
        str(record.get("trading_date"))[:10]: record for record in existing_records
    }
    for record in records:
        by_date[str(record.get("trading_date"))[:10]] = record
    merged = [
        by_date[key]
        for key in sorted(key for key in by_date if key and key != "None")
    ]
    _put_history_archive(ticker, merged)


def _latest_history_record_date(records: list[dict[str, Any]]) -> date | None:
    latest: date | None = None
    for record in records:
        try:
            record_date = _record_date(record.get("trading_date"))
        except Exception:
            continue
        if latest is None or record_date > latest:
            latest = record_date
    return latest


def _earliest_history_record_date(records: list[dict[str, Any]]) -> date | None:
    earliest: date | None = None
    for record in records:
        try:
            record_date = _record_date(record.get("trading_date"))
        except Exception:
            continue
        if earliest is None or record_date < earliest:
            earliest = record_date
    return earliest


def _history_archive_needs_update(latest_record_date: date | None) -> bool:
    return latest_record_date is None or latest_record_date < date.today()


def _history_archive_needs_backfill(earliest_record_date: date | None) -> bool:
    return (
        earliest_record_date is None
        or earliest_record_date > _history_backfill_start_date() + timedelta(days=7)
    )


def _stock_metadata_needs_history_restore(stock: dict[str, Any]) -> bool:
    if not stock.get("latest_stock_data_date"):
        return True
    row_count = stock.get("stock_history_row_count")
    return row_count is not None and int(row_count or 0) < 20


def _merge_record_lists(
    existing_records: list[dict[str, Any]],
    new_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_date = {
        str(record.get("trading_date"))[:10]: record
        for record in existing_records
        if record.get("trading_date")
    }
    for record in new_records:
        if record.get("trading_date"):
            by_date[str(record.get("trading_date"))[:10]] = record
    return [by_date[key] for key in sorted(by_date)]


def _jsonable_record(record: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, Decimal):
            output[key] = str(value)
        elif isinstance(value, (date, datetime)):
            output[key] = value.isoformat()
        else:
            output[key] = value
    return output


def _fetch_historical_records(
    ticker: str, stock_metadata: dict[str, Any] | None = None
) -> list[dict[str, Any]] | None:
    data = _fetch_yfinance_with_retry([ticker], period=INITIAL_HISTORY_PERIOD)
    if data is not None:
        extract = _extract_ticker_data_result(
            data,
            ticker,
            period=INITIAL_HISTORY_PERIOD,
            stock_metadata=stock_metadata,
        )
        if extract.records:
            return extract.records
    records = _fetch_nasdaq_with_retry(
        ticker,
        stock_metadata=stock_metadata,
        start_date=_history_backfill_start_date(),
        end_date=date.today(),
        max_records=NASDAQ_HISTORICAL_MAX_RECORDS_PER_TICKER,
        fetch_period="nasdaq_historical",
    )
    if records:
        return records
    return _fetch_stooq_with_retry(
        ticker,
        stock_metadata=stock_metadata,
        max_records=STOOQ_HISTORICAL_MAX_RECORDS_PER_TICKER,
        fetch_period="stooq_historical",
    )


def _fetch_missing_historical_records(
    ticker: str,
    stock_metadata: dict[str, Any] | None,
    latest_record_date: date | None,
) -> list[dict[str, Any]]:
    start_date = (
        latest_record_date + timedelta(days=1)
        if latest_record_date
        else _history_backfill_start_date()
    )
    end_date = date.today() + timedelta(days=1)
    if start_date >= end_date:
        return []

    data = _fetch_yfinance_with_retry(
        [ticker],
        period="custom",
        start=start_date,
        end=end_date,
    )
    if data is not None:
        extract = _extract_ticker_data_result(
            data,
            ticker,
            period=f"{(end_date - start_date).days}d",
            stock_metadata=stock_metadata,
        )
        if extract.records:
            return [
                record
                for record in extract.records
                if _record_date(record["trading_date"]) >= start_date
            ]

    fallback_records = (
        _fetch_nasdaq_with_retry(
            ticker,
            stock_metadata=stock_metadata,
            start_date=start_date,
            end_date=date.today(),
            max_records=NASDAQ_HISTORICAL_MAX_RECORDS_PER_TICKER,
            fetch_period="nasdaq_historical",
        )
        or _fetch_stooq_with_retry(
            ticker,
            stock_metadata=stock_metadata,
            max_records=STOOQ_HISTORICAL_MAX_RECORDS_PER_TICKER,
            fetch_period="stooq_historical",
        )
        or []
    )
    return [
        record
        for record in fallback_records
        if _record_date(record["trading_date"]) >= start_date
    ]


def _history_backfill_start_date(today: date | None = None) -> date:
    window = _fetch_window(INITIAL_HISTORY_PERIOD, today=today)
    start = window.get("fetch_window_start")
    if start:
        return date.fromisoformat(start)
    return (today or date.today()) - timedelta(days=365 * 5)


def _invoke_next_historical_backfill(
    event: dict[str, Any],
    invocation_count: int,
    processed_tickers: set[str],
) -> bool:
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
    if not function_name:
        logger.warning("historical_backfill_continue_unavailable_no_function_name")
        return False
    payload = {
        **event,
        "mode": "historical_backfill",
        "continue_backfill": True,
        "invocation_count": invocation_count,
        "processed_tickers": sorted(processed_tickers),
    }
    try:
        boto3.client("lambda").invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        logger.info(
            "historical_backfill_continue_invoked",
            invocation_count=invocation_count,
            max_tickers=payload.get("max_tickers"),
        )
        return True
    except Exception as exc:
        logger.warning(
            "historical_backfill_continue_invoke_failed",
            invocation_count=invocation_count,
            error=str(exc),
        )
        return False


def _select_due_stocks(stocks: list[dict[str, Any]], event: dict[str, Any]) -> list[dict[str, Any]]:
    requested = {ticker.upper() for ticker in event.get("tickers", [])}
    if requested:
        stocks = [stock for stock in stocks if stock["ticker"] in requested]

    max_tickers = int(event.get("max_tickers", MAX_TICKERS_PER_RUN))
    ordered = sorted(
        stocks,
        key=lambda stock: (
            not _failed_retry_due(stock),
            stock.get("latest_stock_data_date") is not None,
            stock.get("latest_stock_data_date") or "",
            stock["ticker"],
        ),
    )
    return ordered[:max_tickers]


def _group_stocks_by_period(stocks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {
        INITIAL_HISTORY_PERIOD: [],
        INCREMENTAL_PERIOD: [],
    }
    for stock in stocks:
        period = INCREMENTAL_PERIOD if stock.get("latest_stock_data_date") else INITIAL_HISTORY_PERIOD
        grouped.setdefault(period, []).append(stock)
    return {period: values for period, values in grouped.items() if values}


def _failed_retry_due(stock: dict[str, Any], now: datetime | None = None) -> bool:
    failed_at = stock.get("latest_stock_collection_failed_at")
    if not failed_at:
        return False
    now = now or datetime.now(timezone.utc)
    try:
        failed_time = datetime.fromisoformat(str(failed_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if failed_time.tzinfo is None:
        failed_time = failed_time.replace(tzinfo=timezone.utc)
    return now - failed_time >= timedelta(hours=STOCK_FAILED_RETRY_AFTER_HOURS)


def _build_collection_summary(
    active_ticker_count: int,
    selected_ticker_count: int,
    records_collected: int,
    failed_tickers: list[str],
    duplicate_records: int = 0,
    malformed_tickers: list[str] | None = None,
    no_data_tickers: list[str] | None = None,
    recovered_tickers: list[str] | None = None,
) -> dict[str, Any]:
    malformed_tickers = malformed_tickers or []
    no_data_tickers = no_data_tickers or []
    recovered_tickers = recovered_tickers or []
    failed_ticker_count = len(set(failed_tickers))
    successful_ticker_count = max(selected_ticker_count - failed_ticker_count, 0)
    completeness_ratio = (
        successful_ticker_count / selected_ticker_count if selected_ticker_count else 1.0
    )
    if active_ticker_count == 0:
        status = "no_active_tickers"
    elif selected_ticker_count == 0:
        status = "no_due_tickers"
    elif failed_ticker_count == 0:
        status = "complete"
    elif successful_ticker_count == 0:
        status = "failed"
    elif completeness_ratio < STOCK_COLLECTION_MIN_COMPLETENESS:
        status = "degraded"
    else:
        status = "partial"

    threshold_met = completeness_ratio >= STOCK_COLLECTION_MIN_COMPLETENESS
    return {
        "status": status,
        "active_ticker_count": active_ticker_count,
        "selected_ticker_count": selected_ticker_count,
        "successful_ticker_count": successful_ticker_count,
        "failed_ticker_count": failed_ticker_count,
        "records_collected": records_collected,
        "duplicate_record_count": duplicate_records,
        "malformed_ticker_count": len(set(malformed_tickers)),
        "no_data_ticker_count": len(set(no_data_tickers)),
        "completeness_ratio": round(completeness_ratio, 4),
        "minimum_completeness_ratio": STOCK_COLLECTION_MIN_COMPLETENESS,
        "completeness_threshold_met": threshold_met,
        "failed_tickers": failed_tickers[:50],
        "failed_tickers_truncated": len(failed_tickers) > 50,
        "malformed_tickers": sorted(set(malformed_tickers))[:50],
        "malformed_tickers_truncated": len(set(malformed_tickers)) > 50,
        "no_data_tickers": sorted(set(no_data_tickers))[:50],
        "no_data_tickers_truncated": len(set(no_data_tickers)) > 50,
        "recovered_tickers": sorted(set(recovered_tickers))[:50],
        "recovered_tickers_truncated": len(set(recovered_tickers)) > 50,
        "retry_after_hours": STOCK_FAILED_RETRY_AFTER_HOURS,
    }


def _record_collection_summary(summary: dict[str, Any]) -> None:
    try:
        store.put_collection_summary("STOCK_COLLECTION", summary)
    except Exception as e:
        logger.warning("stock_collection_summary_write_failed", error=str(e))


def _record_failed_ticker_state(summary: dict[str, Any]) -> None:
    failed_tickers = summary.get("failed_tickers", [])
    failed_set = set(failed_tickers)
    malformed_set = set(summary.get("malformed_tickers", []))
    no_data_set = set(summary.get("no_data_tickers", []))
    for ticker in failed_tickers:
        if ticker in malformed_set:
            reason = "malformed"
        elif ticker in no_data_set:
            reason = "no_data"
        else:
            reason = "all_providers_failed"
        try:
            store.mark_stock_collection_failed(
                ticker,
                reason=reason,
                retry_after_hours=STOCK_FAILED_RETRY_AFTER_HOURS,
            )
        except Exception as exc:
            logger.warning(
                "stock_collection_failure_state_write_failed",
                ticker=ticker,
                error=str(exc),
            )

    # Clear stale failure markers for tickers that recovered in this run.
    for ticker in set(summary.get("recovered_tickers", [])) - failed_set:
        try:
            store.clear_stock_collection_failure(ticker)
        except Exception as exc:
            logger.warning(
                "stock_collection_failure_state_clear_failed",
                ticker=ticker,
                error=str(exc),
            )


def _emit_collection_summary_metrics(summary: dict[str, Any]) -> None:
    status = str(summary.get("status", "unknown"))
    metric_data = [
        {
            "MetricName": "stock_collection_completeness_percent",
            "Value": float(summary.get("completeness_ratio", 0)) * 100,
            "Unit": "Percent",
        },
        {
            "MetricName": "stock_collection_failed_tickers",
            "Value": int(summary.get("failed_ticker_count", 0)),
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_duplicate_records",
            "Value": int(summary.get("duplicate_record_count", 0)),
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_malformed_tickers",
            "Value": int(summary.get("malformed_ticker_count", 0)),
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_no_data_tickers",
            "Value": int(summary.get("no_data_ticker_count", 0)),
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_successful_tickers",
            "Value": int(summary.get("successful_ticker_count", 0)),
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_partial_runs",
            "Value": 1 if status == "partial" else 0,
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_failed_runs",
            "Value": 1 if status in {"failed", "degraded"} else 0,
            "Unit": "Count",
        },
        {
            "MetricName": "stock_collection_threshold_breaches",
            "Value": 0 if summary.get("completeness_threshold_met", True) else 1,
            "Unit": "Count",
        },
    ]
    _emit_metric_data(metric_data)


def _process_batch(
    tickers: list[str],
    period: str = "1d",
    stock_metadata_by_ticker: dict[str, dict[str, Any]] | None = None,
) -> BatchResult:
    """Process a batch of tickers via yfinance with retry logic.

    Returns granular accounting for inserted records, duplicates, and failures.
    """
    result = BatchResult()

    data = _fetch_yfinance_with_retry(tickers, period=period)

    if data is None:
        # Entire batch failed
        result.failed_tickers.extend(tickers)
        result.no_data_tickers.extend(tickers)
        return result

    for ticker in tickers:
        extract = _extract_ticker_data_result(
            data,
            ticker,
            period=period,
            stock_metadata=(stock_metadata_by_ticker or {}).get(ticker),
        )
        if extract.records:
            stored = _store_records(extract.records)
            result.records_inserted += stored.inserted_records
            result.duplicate_records += stored.duplicate_records
            result.failed_records += stored.failed_records
            if stored.inserted_records > 0 or stored.duplicate_records > 0:
                result.collected_tickers.add(ticker)
            else:
                result.failed_tickers.append(ticker)
        else:
            result.failed_tickers.append(ticker)
            if extract.failure_reason == "malformed":
                result.malformed_tickers.append(ticker)
            else:
                result.no_data_tickers.append(ticker)

    return result


def _compute_and_store_movement_signals(tickers: set[str]) -> int:
    signal_count = 0
    for ticker in sorted(tickers):
        try:
            rows = store.get_stock_data(
                ticker,
                date.today() - timedelta(days=60),
                date.today(),
            )
            for signal in _movement_signals_from_rows(ticker, rows):
                store.put_market_signal(signal)
                signal_count += 1
        except Exception as exc:
            logger.warning("movement_signal_generation_failed", ticker=ticker, error=str(exc))
    return signal_count


def _movement_signals_from_rows(ticker: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) < 2:
        return []
    ordered = sorted(rows, key=lambda row: str(row["trading_date"]))
    latest = ordered[-1]
    previous = ordered[-2]
    close = _analysis_close_price(latest)
    previous_close = _analysis_close_price(previous)
    if previous_close <= 0:
        return []
    price_change = (close - previous_close) / previous_close * Decimal("100")
    prior_rows = ordered[:-1]
    avg_volume = sum(Decimal(str(row.get("volume", 0))) for row in prior_rows) / max(
        Decimal(len(prior_rows)), Decimal("1")
    )
    latest_volume = Decimal(str(latest.get("volume", 0)))
    volume_ratio = latest_volume / avg_volume if avg_volume > 0 else Decimal("1")
    signal_date = _record_date(latest["trading_date"])
    signals = []
    if abs(price_change) >= Decimal("3"):
        score = int(max(-45, min(45, float(price_change) * 6)))
        signals.append(
            _market_signal(
                ticker,
                signal_date,
                "price_move",
                "positive" if price_change > 0 else "negative",
                score,
                "Large daily price move",
                f"{ticker} moved {price_change:.2f}% versus the prior close.",
                {
                    "price_change_percent": price_change,
                    "close_price": close,
                    "previous_close_price": previous_close,
                    "volume": int(latest_volume),
                    "average_volume": avg_volume,
                },
            )
        )
    if volume_ratio >= Decimal("1.8"):
        signals.append(
            _market_signal(
                ticker,
                signal_date,
                "volume_move",
                "positive" if price_change >= 0 else "negative",
                25 if price_change >= 0 else -25,
                "Unusual volume",
                f"{ticker} traded at {volume_ratio:.1f}x its recent average volume.",
                {
                    "price_change_percent": price_change,
                    "volume_ratio": volume_ratio,
                    "close_price": close,
                    "previous_close_price": previous_close,
                    "volume": int(latest_volume),
                    "average_volume": avg_volume,
                },
            )
        )
    return signals


def _market_signal(
    ticker: str,
    signal_date: date,
    signal_type: str,
    direction: str,
    score: int,
    title: str,
    summary: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "signal_date": signal_date,
        "signal_type": signal_type,
        "direction": direction,
        "score": score,
        "title": title,
        "summary": summary,
        "source": {
            "provider": "stock_collector",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "raw": {key: str(value) for key, value in metrics.items()},
        },
        **metrics,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _analysis_close_price(row: dict[str, Any]) -> Decimal:
    adjusted = row.get("adjusted_close_price")
    if adjusted is not None:
        return Decimal(str(adjusted))
    return Decimal(str(row["close_price"]))


def _record_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _fetch_yfinance_with_retry(
    tickers: list[str],
    period: str = "1d",
    start: date | None = None,
    end: date | None = None,
) -> Any | None:
    """Fetch data from yfinance with exponential backoff retry.

    Retries up to MAX_RETRIES times with exponential backoff starting
    at INITIAL_BACKOFF_SECONDS.
    """
    ticker_str = " ".join(tickers)
    backoff = INITIAL_BACKOFF_SECONDS

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log = logger.bind(
                attempt=attempt,
                ticker_count=len(tickers),
                period=period,
                start=start.isoformat() if start else None,
                end=end.isoformat() if end else None,
            )
            log.info("yfinance_fetch_attempt")

            download_kwargs: dict[str, Any] = {
                "group_by": "ticker",
                "auto_adjust": False,
                "threads": False,
                "progress": False,
                "timeout": 30,
            }
            if start or end:
                if start:
                    download_kwargs["start"] = start.isoformat()
                if end:
                    download_kwargs["end"] = end.isoformat()
            else:
                download_kwargs["period"] = period

            data = yf.download(ticker_str, **download_kwargs)

            if data is not None and not data.empty:
                log.info("yfinance_fetch_success")
                return data

            log.warning("yfinance_returned_empty")

        except Exception as e:
            logger.warning(
                "yfinance_fetch_failed",
                attempt=attempt,
                error=str(e),
            )

        if attempt < MAX_RETRIES:
            logger.info("yfinance_retry_backoff", wait_seconds=backoff)
            time.sleep(backoff)
            backoff *= 2

    logger.error("yfinance_all_retries_exhausted", tickers=tickers[:5])
    return None


def _extract_ticker_data(
    data: Any,
    ticker: str,
    period: str = "1d",
    stock_metadata: dict[str, Any] | None = None,
) -> list[dict] | None:
    """Extract OHLCV records for a single ticker from yfinance DataFrame.

    Returns None if data is missing or malformed.
    """
    result = _extract_ticker_data_result(data, ticker, period, stock_metadata)
    return result.records if result.records else None


def _extract_ticker_data_result(
    data: Any,
    ticker: str,
    period: str = "1d",
    stock_metadata: dict[str, Any] | None = None,
) -> ExtractResult:
    """Extract OHLCV records and classify failures for collection summaries."""
    try:
        # For single ticker downloads, columns are flat
        # For multi-ticker downloads, columns are multi-level (ticker, field)
        if hasattr(data.columns, "nlevels") and data.columns.nlevels > 1:
            if ticker not in data.columns.get_level_values(0):
                logger.warning("ticker_not_in_response", ticker=ticker)
                return ExtractResult(failure_reason="no_data")
            ticker_data = data[ticker]
        else:
            # Single ticker case
            ticker_data = data

        if ticker_data.empty:
            return ExtractResult(failure_reason="no_data")

        records = []
        for idx, row in ticker_data.iterrows():
            record = _validate_record(
                ticker,
                idx,
                row,
                period=period,
                stock_metadata=stock_metadata,
            )
            if record:
                records.append(record)

        if records:
            return ExtractResult(records=records)
        return ExtractResult(failure_reason="malformed")

    except Exception as e:
        logger.warning(
            "ticker_data_extraction_failed",
            ticker=ticker,
            error=str(e),
        )
        return ExtractResult(failure_reason="malformed")


def _validate_record(
    ticker: str,
    trading_date: Any,
    row: Any,
    period: str = "1d",
    stock_metadata: dict[str, Any] | None = None,
) -> dict | None:
    """Validate and normalize a single OHLCV record.

    Returns None and logs a warning if the record is malformed.
    """
    try:
        # Extract and validate date
        if hasattr(trading_date, "date"):
            record_date = trading_date.date()
        elif isinstance(trading_date, date):
            record_date = trading_date
        else:
            record_date = date.fromisoformat(str(trading_date)[:10])

        # Extract price fields - yfinance uses 'Open', 'High', 'Low', 'Close', 'Volume'
        open_price = _to_decimal(row.get("Open"))
        high_price = _to_decimal(row.get("High"))
        low_price = _to_decimal(row.get("Low"))
        close_price = _to_decimal(row.get("Close"))
        adjusted_close_price = _to_decimal(row.get("Adj Close"))
        volume = int(row.get("Volume", 0))

        # Validate all required fields are present and valid
        if any(p is None for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "malformed_record_missing_prices",
                ticker=ticker,
                trading_date=str(record_date),
            )
            return None

        if any(p <= 0 for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "malformed_record_invalid_prices",
                ticker=ticker,
                trading_date=str(record_date),
            )
            return None

        if volume < 0:
            logger.warning(
                "malformed_record_invalid_volume",
                ticker=ticker,
                trading_date=str(record_date),
            )
            return None

        return {
            "ticker": ticker,
            "trading_date": record_date,
            "open_price": open_price,
            "high_price": high_price,
            "low_price": low_price,
            "close_price": close_price,
            "adjusted_close_price": adjusted_close_price,
            "volume": volume,
            "data_provider": "yfinance",
            "provider_symbol": ticker,
            "provider_endpoint": "yf.download",
            "provider_priority": "primary",
            "price_adjustment": "unadjusted",
            "has_adjusted_close": adjusted_close_price is not None,
            "corporate_action_adjusted": False,
            "adjustment_context": (
                "raw_ohlcv_with_adjusted_close"
                if adjusted_close_price is not None
                else "raw_ohlcv_only"
            ),
            "split_dividend_adjustment": (
                "adjusted_close_available"
                if adjusted_close_price is not None
                else "not_available"
            ),
            "currency": _metadata_value(
                stock_metadata,
                "currency",
                DEFAULT_MARKET_DATA_CURRENCY,
            ),
            "exchange": _metadata_value(stock_metadata, "exchange"),
            "fetch_period": period,
            **_fetch_window(period),
        }

    except (ValueError, TypeError, InvalidOperation) as e:
        logger.warning(
            "malformed_record_discarded",
            ticker=ticker,
            error=str(e),
        )
        return None


def _to_decimal(value: Any) -> Decimal | None:
    """Convert a value to Decimal, returning None if conversion fails."""
    if value is None:
        return None
    try:
        import math

        float_val = float(value)
        if math.isnan(float_val) or math.isinf(float_val):
            return None
        return Decimal(str(round(float_val, 4)))
    except (ValueError, TypeError, InvalidOperation):
        return None


def _metadata_value(
    stock_metadata: dict[str, Any] | None,
    field: str,
    default: str | None = None,
) -> str | None:
    if not stock_metadata:
        return default
    value = stock_metadata.get(field)
    if value is None:
        return default
    value_str = str(value).strip()
    return value_str or default


def _fetch_window(period: str, today: date | None = None) -> dict[str, str | None]:
    today = today or date.today()
    if period.endswith("d") and period[:-1].isdigit():
        start = today - timedelta(days=int(period[:-1]))
    elif period.endswith("y") and period[:-1].isdigit():
        years = int(period[:-1])
        try:
            start = today.replace(year=today.year - years)
        except ValueError:
            start = today.replace(month=2, day=28, year=today.year - years)
    elif period == "compact":
        start = today - timedelta(days=100)
    else:
        start = None

    return {
        "fetch_window_start": start.isoformat() if start else None,
        "fetch_window_end": today.isoformat(),
    }


def _store_records(records: list[dict]) -> StoreResult:
    """Store OHLCV records in DynamoDB, skipping duplicate ticker/date items."""
    result = StoreResult()
    if not records:
        return result

    archive_records_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        try:
            record["collected_at"] = datetime.utcnow().isoformat()
            if store.put_stock_data(record):
                result.inserted_records += 1
            else:
                result.duplicate_records += 1
                logger.debug(
                    "duplicate_record_skipped",
                    ticker=record["ticker"],
                    trading_date=str(record["trading_date"]),
                )
            archive_records_by_ticker.setdefault(record["ticker"], []).append(record)
        except Exception as e:
            logger.warning(
                "record_insert_failed",
                ticker=record["ticker"],
                error=str(e),
            )
            result.failed_records += 1

    for ticker, ticker_records in archive_records_by_ticker.items():
        try:
            _merge_history_archive(ticker, ticker_records)
        except Exception as exc:
            logger.warning(
                "stock_history_archive_merge_failed",
                ticker=ticker,
                error=str(exc),
            )

    return result


def _store_stooq_backfill_records(records: list[dict]) -> StoreResult:
    result = StoreResult()
    if not records:
        return result
    try:
        stored = store.put_stock_data_backfill_batch(records)
        result.inserted_records = int(stored.get("inserted_records", 0))
        result.duplicate_records = int(stored.get("duplicate_records", 0))
        result.failed_records = int(stored.get("failed_records", 0))
    except Exception as exc:
        logger.warning(
            "stooq_backfill_batch_store_failed",
            ticker=records[0].get("ticker"),
            error=str(exc),
        )
        result.failed_records = len(records)
    return result


def _alpha_vantage_fallback(tickers: list[str]) -> int:
    """Attempt to fetch data from Alpha Vantage for failed tickers.

    Alpha Vantage free tier: 500 calls/day, one ticker at a time.
    Uses retry logic with exponential backoff.

    Returns the number of successfully collected records.
    """
    result, _ = _alpha_vantage_fallback_with_details(tickers)
    return result.inserted_records


def _alpha_vantage_fallback_with_details(
    tickers: list[str],
    stock_metadata_by_ticker: dict[str, dict[str, Any]] | None = None,
) -> tuple[StoreResult, set[str]]:
    """Attempt Alpha Vantage fallback and return collected records plus successful tickers."""
    if not ALPHA_VANTAGE_API_KEY:
        logger.warning("alpha_vantage_api_key_not_configured")
        return StoreResult(), set()

    result = StoreResult()
    successful_tickers: set[str] = set()

    for ticker in tickers:
        records = _fetch_alpha_vantage_with_retry(
            ticker,
            stock_metadata=(stock_metadata_by_ticker or {}).get(ticker),
        )
        if records:
            stored = _store_records(records)
            result.inserted_records += stored.inserted_records
            result.duplicate_records += stored.duplicate_records
            result.failed_records += stored.failed_records
            if stored.inserted_records > 0 or stored.duplicate_records > 0:
                successful_tickers.add(ticker)

    return result, successful_tickers


def _nasdaq_fallback_with_details(
    tickers: list[str],
    stock_metadata_by_ticker: dict[str, dict[str, Any]] | None = None,
) -> tuple[StoreResult, set[str]]:
    """Attempt no-key Nasdaq historical-data fallback."""
    result = StoreResult()
    successful_tickers: set[str] = set()

    for ticker in tickers:
        records = _fetch_nasdaq_with_retry(
            ticker,
            stock_metadata=(stock_metadata_by_ticker or {}).get(ticker),
        )
        if records:
            stored = _store_records(records)
            result.inserted_records += stored.inserted_records
            result.duplicate_records += stored.duplicate_records
            result.failed_records += stored.failed_records
            if stored.inserted_records > 0 or stored.duplicate_records > 0:
                successful_tickers.add(ticker)

    return result, successful_tickers


def _stooq_fallback_with_details(
    tickers: list[str],
    stock_metadata_by_ticker: dict[str, dict[str, Any]] | None = None,
) -> tuple[StoreResult, set[str]]:
    """Attempt no-key Stooq fallback and return collected records plus successful tickers."""
    result = StoreResult()
    successful_tickers: set[str] = set()

    for ticker in tickers:
        records = _fetch_stooq_with_retry(
            ticker,
            stock_metadata=(stock_metadata_by_ticker or {}).get(ticker),
        )
        if records:
            stored = _store_records(records)
            result.inserted_records += stored.inserted_records
            result.duplicate_records += stored.duplicate_records
            result.failed_records += stored.failed_records
            if stored.inserted_records > 0 or stored.duplicate_records > 0:
                successful_tickers.add(ticker)

    return result, successful_tickers


def _fetch_alpha_vantage_with_retry(
    ticker: str,
    stock_metadata: dict[str, Any] | None = None,
) -> list[dict] | None:
    """Fetch daily data for a single ticker from Alpha Vantage with retry."""
    backoff = INITIAL_BACKOFF_SECONDS

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log = logger.bind(ticker=ticker, attempt=attempt, provider="alpha_vantage")
            log.info("alpha_vantage_fetch_attempt")

            response = requests.get(
                ALPHA_VANTAGE_BASE_URL,
                params={
                    "function": "TIME_SERIES_DAILY",
                    "symbol": ticker,
                    "outputsize": "compact",
                    "apikey": ALPHA_VANTAGE_API_KEY,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            # Check for API errors
            if "Error Message" in data or "Note" in data:
                error_msg = data.get("Error Message", data.get("Note", "Unknown error"))
                log.warning("alpha_vantage_api_error", error=error_msg)
                if attempt < MAX_RETRIES:
                    time.sleep(backoff)
                    backoff *= 2
                continue

            time_series = data.get("Time Series (Daily)", {})
            if not time_series:
                log.warning("alpha_vantage_no_data")
                return None

            # Get only the most recent trading day
            records = []
            for date_str, values in sorted(time_series.items(), reverse=True)[:1]:
                record = _parse_alpha_vantage_record(
                    ticker,
                    date_str,
                    values,
                    stock_metadata=stock_metadata,
                )
                if record:
                    records.append(record)

            if records:
                log.info("alpha_vantage_fetch_success")
                return records

        except requests.exceptions.RequestException as e:
            logger.warning(
                "alpha_vantage_request_failed",
                ticker=ticker,
                attempt=attempt,
                error=str(e),
            )

        if attempt < MAX_RETRIES:
            logger.info("alpha_vantage_retry_backoff", wait_seconds=backoff)
            time.sleep(backoff)
            backoff *= 2

    logger.error("alpha_vantage_all_retries_exhausted", ticker=ticker)
    return None


def _fetch_nasdaq_with_retry(
    ticker: str,
    stock_metadata: dict[str, Any] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    max_records: int | None = None,
    fetch_period: str = "nasdaq_recent",
) -> list[dict] | None:
    """Fetch recent daily OHLCV records from Nasdaq's historical endpoint."""
    backoff = INITIAL_BACKOFF_SECONDS
    today = end_date or date.today()
    from_date = start_date or (today - timedelta(days=120))
    record_limit = max_records or NASDAQ_MAX_RECORDS_PER_TICKER
    url = NASDAQ_HISTORICAL_BASE_URL.format(ticker=ticker.upper())

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log = logger.bind(ticker=ticker, attempt=attempt, provider="nasdaq")
            log.info("nasdaq_fetch_attempt")
            response = requests.get(
                url,
                params={
                    "assetclass": "stocks",
                    "fromdate": from_date.isoformat(),
                    "todate": today.isoformat(),
                    "limit": record_limit,
                },
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 Stockara/1.0",
                },
                timeout=30,
            )
            response.raise_for_status()
            records = _parse_nasdaq_response(
                ticker,
                response.json(),
                stock_metadata=stock_metadata,
                max_records=record_limit,
                fetch_period=fetch_period,
                fetch_window_start=from_date,
                fetch_window_end=today,
            )
            if records:
                log.info("nasdaq_fetch_success")
                return records
            log.warning("nasdaq_no_data")

        except (requests.exceptions.RequestException, ValueError) as e:
            logger.warning(
                "nasdaq_request_failed",
                ticker=ticker,
                attempt=attempt,
                error=str(e),
            )

        if attempt < MAX_RETRIES:
            logger.info("nasdaq_retry_backoff", wait_seconds=backoff)
            time.sleep(backoff)
            backoff *= 2

    logger.error("nasdaq_all_retries_exhausted", ticker=ticker)
    return None


def _parse_nasdaq_response(
    ticker: str,
    data: dict[str, Any],
    stock_metadata: dict[str, Any] | None = None,
    max_records: int = NASDAQ_MAX_RECORDS_PER_TICKER,
    fetch_period: str = "nasdaq_recent",
    fetch_window_start: date | None = None,
    fetch_window_end: date | None = None,
) -> list[dict] | None:
    payload = data.get("data") or {}
    trades_table = payload.get("tradesTable") or {}
    rows = trades_table.get("rows", [])
    if not rows:
        return None

    records: list[dict] = []
    for row in rows[:max_records]:
        record = _parse_nasdaq_record(
            ticker,
            row,
            stock_metadata=stock_metadata,
            fetch_period=fetch_period,
            fetch_window_start=fetch_window_start,
            fetch_window_end=fetch_window_end,
        )
        if record:
            records.append(record)
    return records or None


def _parse_nasdaq_record(
    ticker: str,
    values: dict[str, Any],
    stock_metadata: dict[str, Any] | None = None,
    fetch_period: str = "nasdaq_recent",
    fetch_window_start: date | None = None,
    fetch_window_end: date | None = None,
) -> dict | None:
    try:
        record_date = datetime.strptime(str(values.get("date", "")), "%m/%d/%Y").date()
        open_price = _to_decimal(_strip_market_value(values.get("open")))
        high_price = _to_decimal(_strip_market_value(values.get("high")))
        low_price = _to_decimal(_strip_market_value(values.get("low")))
        close_price = _to_decimal(_strip_market_value(values.get("close")))
        volume = int(_strip_market_value(values.get("volume")) or 0)

        if any(p is None for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "nasdaq_malformed_record",
                ticker=ticker,
                trading_date=str(values.get("date")),
            )
            return None

        if any(p <= 0 for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "nasdaq_invalid_prices",
                ticker=ticker,
                trading_date=str(values.get("date")),
            )
            return None

        if volume < 0:
            logger.warning(
                "nasdaq_invalid_volume",
                ticker=ticker,
                trading_date=str(values.get("date")),
            )
            return None

        return {
            "ticker": ticker,
            "trading_date": record_date,
            "open_price": open_price,
            "high_price": high_price,
            "low_price": low_price,
            "close_price": close_price,
            "volume": volume,
            "data_provider": "nasdaq",
            "provider_symbol": ticker.upper(),
            "provider_endpoint": "api/quote/{ticker}/historical",
            "provider_priority": "fallback",
            "price_adjustment": "unadjusted",
            "adjusted_close_price": None,
            "has_adjusted_close": False,
            "corporate_action_adjusted": False,
            "adjustment_context": "raw_ohlcv_only",
            "split_dividend_adjustment": "not_available",
            "currency": _metadata_value(
                stock_metadata,
                "currency",
                DEFAULT_MARKET_DATA_CURRENCY,
            ),
            "exchange": _metadata_value(stock_metadata, "exchange"),
            "fetch_period": fetch_period,
            "fetch_window_start": (
                fetch_window_start.isoformat()
                if fetch_window_start
                else _fetch_window("compact")["fetch_window_start"]
            ),
            "fetch_window_end": (
                fetch_window_end.isoformat()
                if fetch_window_end
                else _fetch_window("compact")["fetch_window_end"]
            ),
        }

    except (ValueError, TypeError) as e:
        logger.warning(
            "nasdaq_parse_failed",
            ticker=ticker,
            values=values,
            error=str(e),
        )
        return None


def _strip_market_value(value: Any) -> str:
    return str(value or "").replace("$", "").replace(",", "").strip()


def _fetch_stooq_with_retry(
    ticker: str,
    stock_metadata: dict[str, Any] | None = None,
    max_records: int | None = None,
    fetch_period: str = "stooq_daily",
) -> list[dict] | None:
    """Fetch the most recent daily OHLCV record from Stooq without an API key."""
    backoff = INITIAL_BACKOFF_SECONDS
    provider_symbol = _stooq_symbol(ticker, stock_metadata)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log = logger.bind(
                ticker=ticker,
                provider_symbol=provider_symbol,
                attempt=attempt,
                provider="stooq",
            )
            log.info("stooq_fetch_attempt")

            response = requests.get(
                STOOQ_BASE_URL,
                params={"s": provider_symbol, "i": "d"},
                timeout=30,
            )
            response.raise_for_status()
            records = _parse_stooq_csv(
                ticker,
                provider_symbol,
                response.text,
                stock_metadata=stock_metadata,
                max_records=max_records or STOOQ_MAX_RECORDS_PER_TICKER,
                fetch_period=fetch_period,
            )
            if records:
                log.info("stooq_fetch_success")
                return records
            log.warning("stooq_no_data")

        except requests.exceptions.RequestException as e:
            logger.warning(
                "stooq_request_failed",
                ticker=ticker,
                attempt=attempt,
                error=str(e),
            )

        if attempt < MAX_RETRIES:
            logger.info("stooq_retry_backoff", wait_seconds=backoff)
            time.sleep(backoff)
            backoff *= 2

    logger.error("stooq_all_retries_exhausted", ticker=ticker)
    return None


def _stooq_symbol(ticker: str, stock_metadata: dict[str, Any] | None = None) -> str:
    exchange = _metadata_value(stock_metadata, "exchange", "").upper()
    normalized = ticker.lower().replace(".", "-")
    if exchange in {"NYSE", "NASDAQ", "NYSEARCA", "NYSEAMERICAN", "AMEX", ""}:
        return f"{normalized}.us"
    return normalized


def _parse_stooq_csv(
    ticker: str,
    provider_symbol: str,
    csv_text: str,
    stock_metadata: dict[str, Any] | None = None,
    max_records: int = STOOQ_MAX_RECORDS_PER_TICKER,
    fetch_period: str = "stooq_daily",
) -> list[dict] | None:
    try:
        import csv
        from io import StringIO

        if "<html" in csv_text.lower() or "requires javascript" in csv_text.lower():
            logger.warning("stooq_challenge_page_returned", ticker=ticker)
            return None

        rows = list(csv.DictReader(StringIO(csv_text.strip())))
        if not rows:
            return None

        records: list[dict] = []
        for row in reversed(rows):
            record = _parse_stooq_record(
                ticker,
                provider_symbol,
                row,
                stock_metadata=stock_metadata,
                fetch_period=fetch_period,
            )
            if record:
                records.append(record)
            if len(records) >= max_records:
                break
        return records or None
    except csv.Error as e:
        logger.warning("stooq_csv_parse_failed", ticker=ticker, error=str(e))
        return None


def _decode_stooq_backfill_bytes(data: bytes, key: str) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        logger.warning(
            "stooq_backfill_utf8_decode_failed",
            key=key,
            error=str(exc),
            fallback_encoding="cp1250",
        )
        return data.decode("cp1250", errors="replace")


def _stooq_backfill_ticker_from_key(key: str) -> str | None:
    filename = key.rsplit("/", 1)[-1].lower()
    if not filename.endswith(".us.txt"):
        return None
    ticker = filename.removesuffix(".us.txt").strip()
    return ticker.upper() if ticker else None


def _parse_stooq_backfill_txt(
    text: str,
    stock_metadata_by_ticker: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]] | None:
    try:
        import csv
        from io import StringIO

        rows = list(csv.DictReader(StringIO(text.strip())))
        if not rows:
            return None

        records: list[dict[str, Any]] = []
        expected_ticker: str | None = None
        stock_metadata_by_ticker = stock_metadata_by_ticker or {}
        for row in rows:
            ticker_value = str(row.get("<TICKER>") or "").strip().upper()
            if not ticker_value:
                return None
            ticker = ticker_value.removesuffix(".US")
            if expected_ticker is None:
                expected_ticker = ticker
            elif ticker != expected_ticker:
                logger.warning(
                    "stooq_backfill_file_mixed_tickers",
                    expected_ticker=expected_ticker,
                    ticker=ticker,
                )
                return None

            record = _parse_stooq_backfill_record(
                ticker,
                ticker_value.lower(),
                row,
                stock_metadata=stock_metadata_by_ticker.get(ticker),
            )
            if record:
                records.append(record)

        return records or None
    except csv.Error as exc:
        logger.warning("stooq_backfill_csv_parse_failed", error=str(exc))
        return None


def _limit_stooq_backfill_records(
    records: list[dict[str, Any]] | None,
    max_records: int,
) -> list[dict[str, Any]] | None:
    if not records:
        return records
    if max_records <= 0 or len(records) <= max_records:
        return records
    return sorted(records, key=lambda record: _record_date(record["trading_date"]))[
        -max_records:
    ]


def _parse_stooq_backfill_record(
    ticker: str,
    provider_symbol: str,
    values: dict[str, Any],
    stock_metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        if str(values.get("<PER>", "")).strip().upper() != "D":
            return None
        date_value = str(values.get("<DATE>", "")).strip()
        record_date = date(
            int(date_value[0:4]),
            int(date_value[4:6]),
            int(date_value[6:8]),
        )
        open_price = _to_decimal(values.get("<OPEN>"))
        high_price = _to_decimal(values.get("<HIGH>"))
        low_price = _to_decimal(values.get("<LOW>"))
        close_price = _to_decimal(values.get("<CLOSE>"))
        volume = _stooq_backfill_volume(values.get("<VOL>"))

        if any(p is None for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "stooq_backfill_malformed_record",
                ticker=ticker,
                trading_date=date_value,
            )
            return None

        if any(p <= 0 for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "stooq_backfill_invalid_prices",
                ticker=ticker,
                trading_date=date_value,
            )
            return None

        if volume < 0:
            logger.warning(
                "stooq_backfill_invalid_volume",
                ticker=ticker,
                trading_date=date_value,
            )
            return None

        return {
            "ticker": ticker,
            "trading_date": record_date,
            "open_price": open_price,
            "high_price": high_price,
            "low_price": low_price,
            "close_price": close_price,
            "adjusted_close_price": close_price,
            "volume": volume,
            "data_provider": "stooq",
            "provider_symbol": provider_symbol,
            "provider_endpoint": "uploaded_stooq_txt",
            "provider_priority": "operator_backfill",
            "price_adjustment": "adjusted",
            "has_adjusted_close": True,
            "corporate_action_adjusted": True,
            "adjustment_context": "stooq_adjusted_ohlcv",
            "split_dividend_adjustment": "provider_adjusted_ohlcv",
            "currency": _metadata_value(
                stock_metadata,
                "currency",
                DEFAULT_MARKET_DATA_CURRENCY,
            ),
            "exchange": _metadata_value(stock_metadata, "exchange"),
            "fetch_period": "stooq_uploaded_history",
            "fetch_window_start": None,
            "fetch_window_end": None,
        }
    except (ValueError, TypeError, InvalidOperation) as exc:
        logger.warning(
            "stooq_backfill_parse_failed",
            ticker=ticker,
            values=values,
            error=str(exc),
        )
        return None


def _stooq_backfill_volume(value: Any) -> int:
    if value is None or str(value).strip() == "":
        return 0
    return int(Decimal(str(value)).to_integral_value(rounding=ROUND_HALF_UP))


def _parse_stooq_record(
    ticker: str,
    provider_symbol: str,
    values: dict[str, Any],
    stock_metadata: dict[str, Any] | None = None,
    fetch_period: str = "stooq_daily",
) -> dict | None:
    try:
        record_date = date.fromisoformat(str(values.get("Date", ""))[:10])
        open_price = _to_decimal(values.get("Open"))
        high_price = _to_decimal(values.get("High"))
        low_price = _to_decimal(values.get("Low"))
        close_price = _to_decimal(values.get("Close"))
        volume = int(values.get("Volume") or 0)

        if any(p is None for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "stooq_malformed_record",
                ticker=ticker,
                trading_date=str(values.get("Date")),
            )
            return None

        if any(p <= 0 for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "stooq_invalid_prices",
                ticker=ticker,
                trading_date=str(values.get("Date")),
            )
            return None

        if volume < 0:
            logger.warning(
                "stooq_invalid_volume",
                ticker=ticker,
                trading_date=str(values.get("Date")),
            )
            return None

        return {
            "ticker": ticker,
            "trading_date": record_date,
            "open_price": open_price,
            "high_price": high_price,
            "low_price": low_price,
            "close_price": close_price,
            "volume": volume,
            "data_provider": "stooq",
            "provider_symbol": provider_symbol,
            "provider_endpoint": "q/d/l",
            "provider_priority": "fallback",
            "price_adjustment": "unadjusted",
            "adjusted_close_price": None,
            "has_adjusted_close": False,
            "corporate_action_adjusted": False,
            "adjustment_context": "raw_ohlcv_only",
            "split_dividend_adjustment": "not_available",
            "currency": _metadata_value(
                stock_metadata,
                "currency",
                DEFAULT_MARKET_DATA_CURRENCY,
            ),
            "exchange": _metadata_value(stock_metadata, "exchange"),
            "fetch_period": fetch_period,
            **_fetch_window(INITIAL_HISTORY_PERIOD if "historical" in fetch_period else "compact"),
        }

    except (ValueError, TypeError) as e:
        logger.warning(
            "stooq_parse_failed",
            ticker=ticker,
            values=values,
            error=str(e),
        )
        return None


def _parse_alpha_vantage_record(
    ticker: str,
    date_str: str,
    values: dict,
    stock_metadata: dict[str, Any] | None = None,
) -> dict | None:
    """Parse a single Alpha Vantage daily record."""
    try:
        record_date = date.fromisoformat(date_str)
        open_price = _to_decimal(values.get("1. open"))
        high_price = _to_decimal(values.get("2. high"))
        low_price = _to_decimal(values.get("3. low"))
        close_price = _to_decimal(values.get("4. close"))
        volume = int(values.get("5. volume", 0))

        if any(p is None for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "alpha_vantage_malformed_record",
                ticker=ticker,
                trading_date=date_str,
            )
            return None

        if any(p <= 0 for p in [open_price, high_price, low_price, close_price]):
            logger.warning(
                "alpha_vantage_invalid_prices",
                ticker=ticker,
                trading_date=date_str,
            )
            return None

        return {
            "ticker": ticker,
            "trading_date": record_date,
            "open_price": open_price,
            "high_price": high_price,
            "low_price": low_price,
            "close_price": close_price,
            "volume": volume,
            "data_provider": "alpha_vantage",
            "provider_symbol": ticker,
            "provider_endpoint": "TIME_SERIES_DAILY",
            "provider_priority": "fallback",
            "price_adjustment": "unadjusted",
            "adjusted_close_price": None,
            "has_adjusted_close": False,
            "corporate_action_adjusted": False,
            "adjustment_context": "raw_ohlcv_only",
            "split_dividend_adjustment": "not_available",
            "currency": _metadata_value(
                stock_metadata,
                "currency",
                DEFAULT_MARKET_DATA_CURRENCY,
            ),
            "exchange": _metadata_value(stock_metadata, "exchange"),
            "fetch_period": "compact",
            **_fetch_window("compact"),
        }

    except (ValueError, TypeError) as e:
        logger.warning(
            "alpha_vantage_parse_failed",
            ticker=ticker,
            date_str=date_str,
            error=str(e),
        )
        return None


def _emit_metric(metric_name: str, value: float) -> None:
    """Emit a custom CloudWatch metric."""
    _emit_metric_data(
        [
            {
                "MetricName": metric_name,
                "Value": value,
                "Unit": "Count",
            }
        ]
    )


def _emit_metric_data(metric_data: list[dict[str, Any]]) -> None:
    """Emit custom CloudWatch metrics."""
    try:
        client = boto3.client("cloudwatch")
        timestamp = datetime.now(timezone.utc)
        client.put_metric_data(
            Namespace=CLOUDWATCH_NAMESPACE,
            MetricData=[
                {
                    **metric,
                    "Timestamp": timestamp,
                }
                for metric in metric_data
            ],
        )
        logger.info(
            "cloudwatch_metrics_emitted",
            metrics=[metric["MetricName"] for metric in metric_data],
        )
    except Exception as e:
        logger.warning(
            "cloudwatch_metric_failed",
            metrics=[metric.get("MetricName") for metric in metric_data],
            error=str(e),
        )
