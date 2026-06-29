"""Validate deployed Lambda invoke responses for workflow smoke tests."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


FAILURE_STATUSES = {"error", "failed", "degraded"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a Lambda response hides collection-quality failures."
    )
    parser.add_argument(
        "--component",
        required=True,
        choices=["stock", "news", "earnings", "dividend"],
    )
    parser.add_argument("--response-file", required=True)
    parser.add_argument("--min-completeness", type=float, default=None)
    args = parser.parse_args()

    payload = _load_json(Path(args.response_file))
    body = _normalize_body(payload.get("body"))
    failures = _validate(args.component, body, args.min_completeness)
    if failures:
        for failure in failures:
            print(f"::error::{failure}", file=sys.stderr)
        return 1

    print(_summary(args.component, body))
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return payload


def _normalize_body(body: Any) -> dict[str, Any]:
    if isinstance(body, dict):
        return body
    if isinstance(body, str):
        stripped = body.strip()
        if stripped.startswith("{"):
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        return {"message": body}
    if body is None:
        return {}
    return {"body": body}


def _validate(
    component: str,
    body: dict[str, Any],
    min_completeness: float | None,
) -> list[str]:
    if component == "stock":
        return _validate_stock(body, min_completeness or 0.9)
    if component == "news":
        return _validate_news(body, min_completeness or 1.0)
    if component in {"earnings", "dividend"}:
        return _validate_calendar(component, body)
    raise AssertionError(f"unsupported component: {component}")


def _validate_stock(body: dict[str, Any], min_completeness: float) -> list[str]:
    summary = _summary_payload(body)
    failures = _status_failures("stock", summary or body)
    if summary:
        selected = int(summary.get("selected_ticker_count", 0) or 0)
        successful = int(summary.get("successful_ticker_count", 0) or 0)
        completeness = float(summary.get("completeness_ratio", 0) or 0)
        threshold = float(
            summary.get("minimum_completeness_ratio", min_completeness)
            or min_completeness
        )
        if selected > 0 and successful == 0:
            failures.append("Stock collector produced zero successful tickers.")
        if selected > 0 and completeness < threshold:
            failures.append(
                "Stock collection completeness "
                f"{completeness:.2%} is below required {threshold:.2%}."
            )
        return failures

    message = str(body.get("message", ""))
    match = re.search(r"Collected\s+(\d+)\s+new records", message)
    if match and int(match.group(1)) == 0:
        failures.append("Stock collector produced zero new records.")
    return failures


def _validate_news(body: dict[str, Any], min_completeness: float) -> list[str]:
    summary = _summary_payload(body)
    target = summary or body
    failures = _status_failures("news", target)
    if summary:
        completeness = float(summary.get("completeness_ratio", 0) or 0)
        if completeness < min_completeness:
            failures.append(
                "News collection completeness "
                f"{completeness:.2%} is below required {min_completeness:.2%}."
            )
        if int(summary.get("articles_fetched", 0) or 0) == 0:
            failures.append("News collector fetched zero articles.")
    return failures


def _validate_calendar(component: str, body: dict[str, Any]) -> list[str]:
    failures = _status_failures(component, body)
    selected = int(body.get("selected_ticker_count", 0) or 0)
    collected = int(body.get("events_collected", 0) or 0)
    failed = len(body.get("failed_tickers", []) or [])
    if selected > 0 and collected == 0:
        failures.append(f"{component.title()} collector produced zero events.")
    if selected > 0 and failed >= selected:
        failures.append(
            f"{component.title()} collector failed every selected ticker "
            f"({failed}/{selected})."
        )
    return failures


def _status_failures(component: str, body: dict[str, Any]) -> list[str]:
    status = str(body.get("status", "") or "").lower()
    if status in FAILURE_STATUSES:
        return [f"{component.title()} collector returned status={status}."]
    return []


def _summary_payload(body: dict[str, Any]) -> dict[str, Any] | None:
    summary = body.get("collection_summary") or body.get("summary")
    if isinstance(summary, dict):
        return summary
    return None


def _summary(component: str, body: dict[str, Any]) -> str:
    summary = _summary_payload(body) or body
    useful = (
        summary.get("successful_ticker_count")
        or summary.get("articles_fetched")
        or summary.get("events_collected")
        or 0
    )
    status = summary.get("status", "ok")
    return f"{component} response validated: status={status} useful_count={useful}"


if __name__ == "__main__":
    raise SystemExit(main())
