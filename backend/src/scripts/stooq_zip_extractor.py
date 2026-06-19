"""One-time Stooq zip extractor.

Streams downloaded Stooq `.txt` files from a zip object in S3 back into S3 so
the stock collector can run its existing historical backfill against normal
objects under a prefix.
"""

import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

import boto3
from boto3.s3.transfer import TransferConfig
import structlog

logger = structlog.get_logger(__name__)

DEFAULT_BUCKET = os.environ.get("STOCKARA_ARTIFACT_BUCKET", "")
DEFAULT_ZIP_KEY = os.environ.get("STOOQ_ZIP_KEY", "stooq/data.zip")
DEFAULT_OUTPUT_PREFIX = os.environ.get("STOOQ_EXTRACTED_PREFIX", "stooq-extracted/")
DEFAULT_MAX_ENTRIES = int(os.environ.get("STOOQ_ZIP_EXTRACT_MAX_ENTRIES", "1000"))
DEFAULT_BACKFILL_MAX_FILES = int(os.environ.get("STOOQ_BACKFILL_MAX_FILES", "250"))
STOCK_COLLECTOR_FUNCTION_NAME = os.environ.get("STOCK_COLLECTOR_FUNCTION_NAME", "")
LOCAL_ZIP_PATH = "/tmp/stooq-data.zip"
S3_TRANSFER_CONFIG = TransferConfig(use_threads=False)


def handler(event: dict[str, Any] | None, context: Any) -> dict[str, Any]:
    event = event or {}
    bucket = str(event.get("bucket") or DEFAULT_BUCKET)
    zip_key = str(event.get("zip_key") or DEFAULT_ZIP_KEY)
    output_prefix = _prefix(str(event.get("output_prefix") or DEFAULT_OUTPUT_PREFIX))
    max_entries = int(event.get("max_entries", DEFAULT_MAX_ENTRIES))
    start_after = event.get("start_after")
    continue_extraction = bool(event.get("continue_extraction", True))
    start_backfill_on_complete = bool(event.get("start_backfill_on_complete", False))
    backfill_event = dict(event.get("backfill_event") or {})
    backfill_max_files = int(
        event.get("backfill_max_files")
        or backfill_event.get("max_files")
        or DEFAULT_BACKFILL_MAX_FILES
    )

    if not bucket:
        return _response(400, {"status": "failed", "message": "bucket is required"})

    run_id = str(
        event.get("run_id") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    logger.info(
        "stooq_zip_extraction_started",
        bucket=bucket,
        zip_key=zip_key,
        output_prefix=output_prefix,
        max_entries=max_entries,
        start_after=start_after,
        run_id=run_id,
    )

    s3 = boto3.client("s3")
    s3.download_file(bucket, zip_key, LOCAL_ZIP_PATH)

    extracted_count = 0
    skipped_count = 0
    last_member: str | None = None
    next_start_after: str | None = None
    complete = False

    with zipfile.ZipFile(LOCAL_ZIP_PATH) as archive:
        members = [
            info
            for info in archive.infolist()
            if _is_stooq_txt_member(info)
        ]
        members.sort(key=lambda info: info.filename)

        eligible = start_after is None
        for info in members:
            if not eligible:
                eligible = info.filename > str(start_after)
                if not eligible:
                    skipped_count += 1
                    continue

            if extracted_count >= max_entries or _should_stop_for_time(context):
                next_start_after = last_member
                break

            output_key = output_prefix + _safe_member_key(info.filename)
            with archive.open(info) as source:
                s3.upload_fileobj(
                    source,
                    bucket,
                    output_key,
                    ExtraArgs={"ContentType": "text/plain"},
                    Config=S3_TRANSFER_CONFIG,
                )
            extracted_count += 1
            last_member = info.filename

        complete = next_start_after is None

    continuation_queued = False
    if not complete and continue_extraction and last_member:
        continuation_queued = _invoke_self(
            {
                **event,
                "bucket": bucket,
                "zip_key": zip_key,
                "output_prefix": output_prefix,
                "max_entries": max_entries,
                "start_after": last_member,
                "continue_extraction": True,
                "run_id": run_id,
            }
        )

    backfill_queued = False
    if complete and start_backfill_on_complete:
        backfill_queued = _invoke_stock_backfill(
            {
                "mode": "stooq_s3_backfill",
                "bucket": bucket,
                "s3_prefix": output_prefix,
                "max_files": backfill_max_files,
                "continue_backfill": True,
                **backfill_event,
            }
        )

    status = {
        "mode": "stooq_zip_extraction",
        "run_id": run_id,
        "bucket": bucket,
        "zip_key": zip_key,
        "output_prefix": output_prefix,
        "extracted_count": extracted_count,
        "skipped_count": skipped_count,
        "last_member": last_member,
        "next_start_after": next_start_after,
        "complete": complete,
        "continuation_queued": continuation_queued,
        "backfill_queued": backfill_queued,
    }
    _write_status(s3, bucket, output_prefix, status)
    logger.info("stooq_zip_extraction_completed", **status)
    return _response(200, status)


def _safe_member_key(filename: str) -> str:
    path = PurePosixPath(filename)
    safe_parts = [
        part
        for part in path.parts
        if part not in {"", ".", ".."} and not part.startswith("/")
    ]
    return "/".join(safe_parts)


def _is_stooq_txt_member(info: zipfile.ZipInfo) -> bool:
    if info.is_dir():
        return False
    path = PurePosixPath(info.filename)
    if not info.filename.lower().endswith(".txt"):
        return False
    if any(part == "__MACOSX" for part in path.parts):
        return False
    if any(part.startswith("._") for part in path.parts):
        return False
    return True


def _prefix(value: str) -> str:
    stripped = value.strip("/")
    return f"{stripped}/" if stripped else ""


def _remaining_seconds(context: Any) -> float | None:
    remaining = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(remaining):
        return None
    return remaining() / 1000


def _should_stop_for_time(context: Any) -> bool:
    remaining = _remaining_seconds(context)
    return remaining is not None and remaining <= 90


def _invoke_self(payload: dict[str, Any]) -> bool:
    function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
    if not function_name:
        logger.warning("stooq_zip_extractor_self_invoke_unavailable")
        return False
    try:
        boto3.client("lambda").invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        return True
    except Exception as exc:
        logger.warning("stooq_zip_extractor_self_invoke_failed", error=str(exc))
        return False


def _invoke_stock_backfill(payload: dict[str, Any]) -> bool:
    if not STOCK_COLLECTOR_FUNCTION_NAME:
        logger.warning("stock_backfill_invoke_unavailable_no_function_name")
        return False
    try:
        boto3.client("lambda").invoke(
            FunctionName=STOCK_COLLECTOR_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        return True
    except Exception as exc:
        logger.warning("stock_backfill_invoke_failed", error=str(exc))
        return False


def _write_status(
    s3: Any, bucket: str, output_prefix: str, status: dict[str, Any]
) -> None:
    key = f"{output_prefix}_status/latest.json"
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(status, default=str).encode("utf-8"),
            ContentType="application/json",
            CacheControl="no-store",
        )
    except Exception as exc:
        logger.warning("stooq_zip_extraction_status_write_failed", error=str(exc))


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status_code, "body": body}
