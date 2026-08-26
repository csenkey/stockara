"""Independent reporting, ordering, recovery and public-data safety."""

import io
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from src.services import workflow_reporter as reporter

NOW = datetime(2026, 8, 26, 6, 20, tzinfo=timezone.utc)
START = datetime(2026, 8, 25, 21, 5, tzinfo=timezone.utc)
ARN = "arn:aws:states:us-east-1:123456789012:execution:stockara-daily-pipeline:run-25"
MACHINE = "arn:aws:states:us-east-1:123456789012:stateMachine:stockara-daily-pipeline"


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.writes = []

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, **kwargs):
        self.objects[kwargs["Key"]] = kwargs["Body"]
        self.writes.append(kwargs["Key"])


@pytest.fixture
def states():
    client = Mock()
    client.describe_execution.return_value = {
        "status": "FAILED",
        "startDate": START,
        "stopDate": START + timedelta(hours=1),
        "name": "run-25",
        "error": "States.DataLimitExceeded",
        "cause": "The state/task 'CollectReviewEvidence' returned a result exceeding the limit. SECRET",
    }
    client.get_execution_history.return_value = {
        "events": [
            {
                "type": "TaskStateEntered",
                "stateEnteredEventDetails": {
                    "name": "RepairReviewNews",
                    "input": json.dumps(
                        {
                            "analysis": {
                                "Payload": {
                                    "body": {
                                        "stage": "evidence_repair_needed",
                                        "analyzed_count": 50,
                                    }
                                }
                            },
                            "private_input": "SECRET",
                            "prices": {"Payload": {"statusCode": 200}},
                        }
                    ),
                },
            },
        ]
    }
    client.list_executions.return_value = {
        "executions": [{"executionArn": ARN, "startDate": START}]
    }
    return client


@pytest.mark.parametrize("status", ["FAILED", "TIMED_OUT", "ABORTED"])
def test_terminal_failures_publish_actual_status_and_progress_without_raw_input(
    states, status
):
    states.describe_execution.return_value["status"] = status
    report = reporter.execution_report(states, ARN, NOW)
    assert report["run_date"] == "2026-08-25"
    assert report["execution_status"] == status
    assert report["business_status"] == "blocked"
    assert report["failed_step"] == "CollectReviewEvidence"
    assert report["analysis_reached"] is True
    assert report["steps"]["prices"]["status_code"] == 200
    assert "SECRET" not in json.dumps(report)
    assert len(json.dumps(report)) < 10_000
    assert states.get_execution_history.call_args.kwargs["reverseOrder"] is True


def test_success_reconciliation_restores_missing_status_without_analysis(states):
    states.describe_execution.return_value.update(
        status="SUCCEEDED",
        output=json.dumps(
            {
                "workflow_decision": {"decision": "publish_degraded"},
                "analysis": {
                    "Payload": {
                        "statusCode": 200,
                        "body": {
                            "stage": "published",
                            "publication_date": "2026-08-25",
                        },
                    }
                },
            }
        ),
    )
    report = reporter.reconcile(states, MACHINE, NOW)
    assert report["status"] == "degraded"
    assert report["execution_status"] == "SUCCEEDED"
    states.get_execution_history.assert_not_called()


def test_history_pagination_and_preanalysis_failure(states):
    states.describe_execution.return_value.update(
        error="Lambda.ServiceException", cause="secret token"
    )
    states.get_execution_history.side_effect = [
        {"events": [], "nextToken": "next"},
        {
            "events": [
                {
                    "stateEnteredEventDetails": {
                        "name": "SyncStaticMetadata",
                        "input": "{}",
                    }
                }
            ]
        },
    ]
    report = reporter.execution_report(states, ARN, NOW)
    assert report["analysis_reached"] is False
    assert report["failed_step"] == "SyncStaticMetadata"
    assert "secret token" not in json.dumps(report)
    assert states.get_execution_history.call_count == 2


def test_reconciliation_recovers_missed_event(states):
    assert (
        reporter.reconcile(states, MACHINE, NOW)["failed_step"]
        == "CollectReviewEvidence"
    )


def test_reconciliation_does_not_report_running_execution_as_failed(states):
    states.describe_execution.return_value["status"] = "RUNNING"
    assert reporter.reconcile(states, MACHINE, NOW) is None


@pytest.mark.parametrize(
    "time,expected",
    [
        ("2026-08-26T00:19:59+00:00", "2026-08-24"),
        ("2026-08-26T00:20:00+00:00", "2026-08-25"),
        ("2026-08-26T23:00:00+00:00", "2026-08-25"),
    ],
)
def test_deadline_accounts_for_utc_midnight(time, expected):
    assert (
        reporter.expected_run_start(datetime.fromisoformat(time)).date().isoformat()
        == expected
    )


def test_missing_execution_does_not_mislabel_old_failure_as_current(states):
    states.list_executions.return_value["executions"][0]["startDate"] -= timedelta(
        days=1
    )
    report = reporter.reconcile(states, MACHINE, NOW)
    assert report["run_date"] == "2026-08-25"
    assert report["execution_status"] == "NOT_STARTED"
    assert report["failed_step"] == "DailySchedule"
    states.describe_execution.assert_not_called()


def test_serial_writer_is_idempotent_and_protects_latest_and_same_day_history(states):
    s3 = MemoryS3()
    report = reporter.execution_report(states, ARN, NOW)
    assert reporter.publish_report(s3, "bucket", report)
    report["generated_at"] = (NOW + timedelta(hours=1)).isoformat()
    assert not reporter.publish_report(s3, "bucket", report)
    older = json.loads(json.dumps(report))
    older["execution"].update(
        name="older", id=ARN + "-older", started_at="2026-08-25T20:05:00Z"
    )
    reporter.publish_report(s3, "bucket", older)
    assert (
        reporter._read(s3, "bucket", "workflow/latest.json")["execution"]["id"] == ARN
    )
    assert (
        reporter._read(s3, "bucket", "workflow/history/2026-08-25.json")["execution"][
            "id"
        ]
        == ARN
    )
    assert "workflow/executions/older.json" in s3.objects
    assert not any("top-picks" in key for key in s3.writes)


def test_observed_failure_wins_over_delayed_in_workflow_report(states):
    s3 = MemoryS3()
    report = reporter.execution_report(states, ARN, NOW)
    reporter.publish_report(s3, "bucket", report)
    report.update(
        status_source="workflow", execution_status="SUCCEEDED", status="success"
    )
    assert not reporter.publish_report(s3, "bucket", report)


def test_s3_access_denied_is_not_silently_treated_as_missing():
    s3 = Mock()
    s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "GetObject"
    )
    with pytest.raises(ClientError):
        reporter._read(s3, "bucket", "workflow/latest.json")


def test_handler_rejects_other_workflows(monkeypatch):
    monkeypatch.setenv("STOCKARA_ARTIFACT_BUCKET", "bucket")
    monkeypatch.setenv("STOCKARA_DAILY_WORKFLOW_ARN", MACHINE)
    with patch.object(reporter.boto3, "client"), pytest.raises(ValueError):
        reporter.handler(
            {
                "source": "aws.states",
                "detail": {"stateMachineArn": "other", "status": "FAILED"},
            },
            None,
        )


def test_running_reconciliation_still_emits_overdue_metric(monkeypatch, states):
    monkeypatch.setenv("STOCKARA_ARTIFACT_BUCKET", "bucket")
    monkeypatch.setenv("STOCKARA_DAILY_WORKFLOW_ARN", MACHINE)
    states.describe_execution.return_value["status"] = "RUNNING"
    cloudwatch = Mock()
    clients = {"s3": MemoryS3(), "stepfunctions": states, "cloudwatch": cloudwatch}

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    with (
        patch.object(reporter.boto3, "client", side_effect=lambda name: clients[name]),
        patch.object(reporter, "datetime", FrozenDatetime),
    ):
        result = reporter.handler({"mode": "reconcile"}, None)
    assert result["body"]["status"] == "running"
    assert cloudwatch.put_metric_data.call_args.kwargs["MetricData"][0]["Value"] == 1
