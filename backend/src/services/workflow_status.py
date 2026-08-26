"""Compact workflow status contracts, independent of analysis and providers."""

from datetime import date, datetime, timezone
from typing import Any


def _status_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def build_workflow_status_payload(
    event: dict[str, Any],
    fallback_run_date: date,
) -> dict[str, Any]:
    """Build the compact daily workflow status artifact payload."""
    workflow_result = event.get("workflow_result") or {}
    analysis_payload = (workflow_result.get("analysis") or {}).get("Payload") or {}
    analysis_body = analysis_payload.get("body") or {}
    if not isinstance(analysis_body, dict):
        analysis_body = {"message": str(analysis_body)}

    workflow_decision = workflow_result.get("workflow_decision") or {}
    decision = str(
        workflow_decision.get("decision")
        or analysis_body.get("workflow_decision")
        or "blocked"
    )
    status_by_decision = {
        "publish": "success",
        "publish_degraded": "degraded",
        "wait_or_repair": "waiting",
        "blocked": "blocked",
    }
    run_date = (
        _status_date(event.get("run_date"))
        or _status_date(event.get("execution_started_at"))
        or _status_date(analysis_body.get("publication_date"))
        or fallback_run_date
    )
    optional_summaries = _optional_collection_summaries(workflow_result)
    degraded_steps = [
        str(summary.get("step"))
        for summary in optional_summaries
        if str(summary.get("status") or "").lower()
        in {"partial", "degraded", "failed", "error"}
        and summary.get("step")
    ]
    workflow_error = workflow_result.get("workflow_error")
    failed_step = None
    if isinstance(workflow_error, dict):
        failed_step = workflow_error.get("step")

    return {
        "artifact_type": "daily_workflow_status",
        "run_date": run_date.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workflow": str(event.get("workflow") or "daily_step_functions"),
        "status": status_by_decision.get(decision, "blocked"),
        "business_status": status_by_decision.get(decision, "blocked"),
        "execution_status": "SUCCEEDED",
        "decision": decision,
        "failed_step": failed_step,
        "degraded_steps": sorted(set(degraded_steps)),
        "analysis_reached": bool(workflow_result.get("analysis")),
        "execution": {
            "id": event.get("execution_id"),
            "name": event.get("execution_name"),
            "started_at": event.get("execution_started_at"),
        },
        "artifacts": {
            "workflow": {
                "latest": "workflow/latest.json",
                "history": f"workflow/history/{run_date.isoformat()}.json",
            },
            "top_picks": "top-picks/latest.json",
            "data_readiness": "data-readiness/latest.json",
            "sell_alerts": "sell-alerts/latest.json",
        },
        "analyzer": {
            "status_code": analysis_payload.get("statusCode"),
            "stage": analysis_body.get("stage"),
            "publication_status": analysis_body.get("publication_status"),
            "publication_date": analysis_body.get("publication_date"),
            "suppression_reason": analysis_body.get("suppression_reason"),
            "top_picks_count": analysis_body.get("top_picks_count"),
            "sell_alerts_count": analysis_body.get("sell_alerts_count"),
            "data_readiness_overall_status": analysis_body.get(
                "data_readiness_overall_status"
            ),
            "data_readiness_summary": analysis_body.get("data_readiness_summary") or {},
            "message": analysis_body.get("message"),
        },
        "steps": {
            "sync_static_metadata": _workflow_step_summary(
                workflow_result.get("sync_static_metadata")
            ),
            "manifest": _workflow_step_summary(workflow_result.get("manifest")),
            "manifest_dispatch": _workflow_step_summary(
                workflow_result.get("manifest_dispatch")
            ),
            "prices": _workflow_step_summary(workflow_result.get("prices")),
            "price_gap_repair": _workflow_step_summary(
                workflow_result.get("price_gap_repair")
            ),
            "news": _workflow_step_summary(workflow_result.get("news")),
            "optional_evidence": _optional_collection_summaries(
                {"optional_evidence": workflow_result.get("optional_evidence") or []}
            ),
        },
        "workflow_error": workflow_error,
    }


def _workflow_step_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "unknown"}
    payload = value.get("Payload") if "Payload" in value else value
    if not isinstance(payload, dict):
        return {
            "status_code": value.get("StatusCode"),
            "status": str(payload) if payload is not None else "unknown",
        }
    body = payload.get("body")
    if not isinstance(body, dict):
        body = {"message": str(body)} if body is not None else {}
    summary = {
        "status_code": payload.get("statusCode") or value.get("StatusCode"),
        "status": body.get("status") or payload.get("status"),
        "step": body.get("step") or payload.get("step"),
        "required": body.get("required")
        if "required" in body
        else payload.get("required"),
        "error_type": body.get("error_type") or payload.get("error_type"),
        "retryable": body.get("retryable")
        if "retryable" in body
        else payload.get("retryable"),
        "occurred_at": body.get("occurred_at") or payload.get("occurred_at"),
        "stage": body.get("stage"),
        "message": body.get("message") or payload.get("message"),
        "reason": body.get("reason"),
        "processed_count": body.get("processed_count"),
        "collected_count": body.get("collected_count"),
        "events_collected": body.get("events_collected"),
        "selected_ticker_count": body.get("selected_ticker_count"),
        "failed_count": body.get("failed_count"),
        "candidate_count": body.get("candidate_count"),
        "analyzed_count": body.get("analyzed_count"),
        "active_incomplete_task_count": body.get("active_incomplete_task_count"),
        "incomplete_task_count": body.get("incomplete_task_count"),
        "dispatch_deadline": body.get("dispatch_deadline"),
        "dispatch_deadline_exceeded": body.get("dispatch_deadline_exceeded"),
        "task_counts": body.get("task_counts"),
        "provider_health": body.get("provider_health"),
        "warnings": body.get("warnings"),
    }
    return {key: item for key, item in summary.items() if item is not None}


def _optional_collection_summaries(event: dict[str, Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    news = event.get("news")
    if news is not None:
        summary = _workflow_step_summary(news)
        summary.setdefault("step", "CollectNews")
        summaries.append(summary)
    for item in event.get("optional_evidence") or []:
        if not isinstance(item, dict):
            continue
        matched = False
        for key, step_name in (
            ("earnings", "CollectEarnings"),
            ("dividends", "CollectDividends"),
            ("evidence", "CollectEvidence"),
        ):
            if key not in item:
                continue
            summary = _workflow_step_summary(item[key])
            summary.setdefault("step", step_name)
            summaries.append(summary)
            matched = True
        if not matched:
            summaries.append(_workflow_step_summary(item))
    return summaries
