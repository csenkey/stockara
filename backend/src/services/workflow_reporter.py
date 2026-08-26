"""Independent, serialized workflow reporting. Never calls providers or analysis.

All production workflow-status writers use this Lambda (reserved concurrency 1).
EventBridge is backed up by scheduled reconciliation against Step Functions.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError
import structlog

from src.services.workflow_status import build_workflow_status_payload

logger = structlog.get_logger(__name__)
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}
FAILURES = TERMINAL - {"SUCCEEDED"}


def _utc(value):
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def _read(s3, bucket, key):
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except ClientError as exc:
        if exc.response["Error"]["Code"] in {"NoSuchKey", "404"}:
            return None
        raise


def _order(payload):
    execution = payload.get("execution") or {}
    return (
        payload["run_date"],
        _utc(execution.get("started_at") or payload["run_date"]).isoformat(),
        execution.get("id") or "",
        payload.get("status_source") == "execution_observer",
    )


def publish_report(s3, bucket, payload):
    """Single-writer ordering prevents late events replacing newer results."""
    execution = payload.get("execution") or {}
    name = execution.get("name")
    keys = []
    if name and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
        keys.append(f"workflow/executions/{name}.json")
    keys.extend(
        [f"workflow/history/{payload['run_date']}.json", "workflow/latest.json"]
    )
    changed = False
    for key in keys:
        previous = _read(s3, bucket, key)
        if previous:
            if _order(previous) > _order(payload):
                continue
            # Retried events should not refresh an old report's timestamp.
            if {k: v for k, v in previous.items() if k != "generated_at"} == {
                k: v for k, v in payload.items() if k != "generated_at"
            }:
                continue
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(payload, default=str, indent=2).encode(),
            ContentType="application/json",
            CacheControl="public, max-age=300",
        )
        changed = True
    return changed


def _history_context(states, arn):
    """Read a bounded tail, using state input only to derive public summaries."""
    context, step, reached = {}, None, False
    token = None
    for _ in range(3):
        args = dict(
            executionArn=arn,
            reverseOrder=True,
            maxResults=100,
            includeExecutionData=True,
        )
        if token:
            args["nextToken"] = token
        page = states.get_execution_history(**args)
        for event in page.get("events", []):
            entered = event.get("stateEnteredEventDetails") or {}
            if entered:
                step = step or entered.get("name")
                reached |= entered.get("name") in {
                    "AnalyzeAndPublish",
                    "ReanalyzeAfterEvidence",
                }
                if not context and entered.get("input"):
                    context = json.loads(entered["input"])
                reached |= bool(context.get("analysis"))
        token = page.get("nextToken")
        if context or not token:
            break
    return context, step, reached


def execution_report(states, arn, now):
    execution = states.describe_execution(executionArn=arn)
    status = execution["status"]
    if status not in TERMINAL:
        return None
    started = _utc(execution["startDate"])
    context = json.loads(execution.get("output") or "{}")
    step, reached = None, bool(context.get("analysis"))
    if status in FAILURES:
        context, step, reached = _history_context(states, arn)
        cause = execution.get("cause") or ""
        match = re.search(r"(?:state/task|state) ['\"]([^'\"]+)['\"]", cause)
        step = match.group(1) if match else step
        error = execution.get("error") or status
        # Runtime causes may contain whole input payloads; never publish those.
        message = (
            "Workflow data exceeded the Step Functions payload limit."
            if error == "States.DataLimitExceeded"
            else f"Execution {status.lower()} at {step or 'an unknown step'}; inspect AWS execution details."
        )
        context["workflow_decision"] = {"decision": "blocked"}
        context["workflow_error"] = {
            "step": step,
            "error_type": error,
            "message": message,
            "retryable": False,
            "occurred_at": _utc(execution["stopDate"]).isoformat(),
        }
    payload = build_workflow_status_payload(
        {
            "run_date": started.date().isoformat(),
            "workflow_result": context,
            "execution_id": arn,
            "execution_name": execution["name"],
            "execution_started_at": started.isoformat(),
        },
        started.date(),
    )
    payload.update(
        execution_status=status,
        status_source="execution_observer",
        generated_at=now.isoformat(),
        analysis_reached=reached,
    )
    return payload


def expected_run_start(now):
    """Daily 21:05 UTC start, with a three-hour timeout + 15-minute grace."""
    start = now.replace(hour=21, minute=5, second=0, microsecond=0)
    while start + timedelta(hours=3, minutes=15) > now:
        start -= timedelta(days=1)
    return start


def reconcile(states, machine_arn, now):
    expected = expected_run_start(now)
    executions = states.list_executions(stateMachineArn=machine_arn, maxResults=25)[
        "executions"
    ]
    recent = [e for e in executions if _utc(e["startDate"]) >= expected]
    if recent:
        latest = max(recent, key=lambda e: _utc(e["startDate"]))
        return execution_report(states, latest["executionArn"], now)
    payload = build_workflow_status_payload(
        {
            "run_date": expected.date().isoformat(),
            "execution_started_at": expected.isoformat(),
            "workflow_result": {
                "workflow_decision": {"decision": "blocked"},
                "workflow_error": {
                    "step": "DailySchedule",
                    "error_type": "MissingExecution",
                    "message": "No daily execution started by the publication deadline.",
                    "retryable": True,
                    "occurred_at": now.isoformat(),
                },
            },
        },
        expected.date(),
    )
    payload.update(
        execution_status="NOT_STARTED",
        status_source="execution_observer",
        generated_at=now.isoformat(),
    )
    return payload


def handler(event, context):
    now = datetime.now(timezone.utc)
    bucket = os.environ["STOCKARA_ARTIFACT_BUCKET"]
    machine_arn = os.environ["STOCKARA_DAILY_WORKFLOW_ARN"]
    s3 = boto3.client("s3")
    if event.get("mode") == "publish_workflow_status":
        payload = build_workflow_status_payload(event, now.date())
        payload["status_source"] = "workflow"
        payload["generated_at"] = now.isoformat()
    elif event.get("mode") == "publish_report":
        payload = event["report"]
    elif event.get("mode") == "reconcile":
        payload = reconcile(boto3.client("stepfunctions"), machine_arn, now)
    else:
        detail = event.get("detail") or {}
        if (
            event.get("source") != "aws.states"
            or detail.get("stateMachineArn") != machine_arn
            or detail.get("status") not in TERMINAL
        ):
            raise ValueError("Unexpected workflow status event")
        payload = execution_report(
            boto3.client("stepfunctions"), detail["executionArn"], now
        )
    changed = publish_report(s3, bucket, payload) if payload else False
    metrics = []
    if changed and payload.get("status_source") == "execution_observer":
        metrics = [
            {
                "MetricName": "daily_workflow_completed",
                "Value": int(payload["execution_status"] in TERMINAL),
                "Unit": "Count",
            },
            {
                "MetricName": "daily_workflow_degraded",
                "Value": int(payload["status"] == "degraded"),
                "Unit": "Count",
            },
            {
                "MetricName": "daily_workflow_blocked",
                "Value": int(payload["status"] == "blocked"),
                "Unit": "Count",
            },
        ]
    if event.get("mode") == "reconcile":
        latest = _read(s3, bucket, "workflow/latest.json") or {}
        metrics.append(
            {
                "MetricName": "workflow_report_overdue",
                "Unit": "Count",
                "Value": int(
                    latest.get("run_date", "")
                    < expected_run_start(now).date().isoformat()
                ),
            }
        )
    if metrics:
        boto3.client("cloudwatch").put_metric_data(
            Namespace="StockaraPhase1", MetricData=metrics
        )
    if payload is None:
        return {"statusCode": 200, "body": {"status": "running"}}
    logger.info(
        "workflow_report_processed",
        run_date=payload["run_date"],
        execution_status=payload["execution_status"],
        changed=changed,
    )
    return {
        "statusCode": 200,
        "body": {
            "status": "published",
            "workflow_status": payload["status"],
            "artifact": "workflow/latest.json",
        },
    }
