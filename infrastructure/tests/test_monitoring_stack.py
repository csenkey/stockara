"""Tests for the MonitoringStack CDK stack."""

import json

import aws_cdk as cdk
import aws_cdk.assertions as assertions
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_stepfunctions as sfn

from stacks.monitoring_stack import MonitoringStack


FUNCTION_NAMES = {
    "workflow_reporter": "stockara-codex-test-workflow-reporter",
    "stock_collector": "stockara-codex-test-stock-collector",
    "stooq_zip_extractor": "stockara-codex-test-stooq-zip-extractor",
    "stock_gap_scanner": "stockara-codex-test-stock-gap-scanner",
    "watchlist_seed": "stockara-codex-test-watchlist-seed",
    "news_collector": "stockara-codex-test-news-collector",
    "evidence_collector": "stockara-codex-test-evidence-collector",
    "earnings_collector": "stockara-codex-test-earnings-collector",
    "dividend_collector": "stockara-codex-test-dividend-collector",
    "collection_distributor": "stockara-codex-test-collection-distributor",
    "publisher": "stockara-codex-test-phase1-analyzer-publisher",
    "health_api": "stockara-codex-test-health-api",
}


def _monitoring_stack(app: cdk.App, construct_id: str) -> MonitoringStack:
    dependencies = cdk.Stack(app, f"{construct_id}Dependencies")
    functions = {
        logical_name: lambda_.Function.from_function_name(
            dependencies,
            f"{construct_id}{logical_name}",
            function_name,
        )
        for logical_name, function_name in FUNCTION_NAMES.items()
    }
    workflow = sfn.StateMachine.from_state_machine_name(
        dependencies,
        f"{construct_id}DailyWorkflow",
        "stockara-codex-test-daily-pipeline",
    )
    return MonitoringStack(
        app,
        construct_id,
        monitored_functions=functions,
        daily_workflow=workflow,
        deployment_stage="codex-test",
    )


def test_collector_completeness_alarms_are_created():
    app = cdk.App()
    stack = _monitoring_stack(app, "TestMonitoring")
    template = assertions.Template.from_stack(stack)

    for metric_name in [
        "stock_collection_partial_runs",
        "stock_collection_failed_runs",
        "stock_collection_completeness_percent",
        "news_collection_partial_runs",
        "news_collection_failed_runs",
        "news_collection_completeness_percent",
    ]:
        template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "Namespace": "StockMonitoring",
                "MetricName": metric_name,
            },
        )


def test_every_runtime_function_has_a_lambda_error_alarm():
    app = cdk.App()
    stack = _monitoring_stack(app, "TestFunctionMonitoring")
    template = assertions.Template.from_stack(stack)

    lambda_error_alarms = template.find_resources(
        "AWS::CloudWatch::Alarm",
        {
            "Properties": assertions.Match.object_like(
                {"Namespace": "AWS/Lambda", "MetricName": "Errors"}
            )
        },
    )
    assert len(lambda_error_alarms) == len(FUNCTION_NAMES) == 12


def test_collection_manifest_health_alarms_are_created():
    app = cdk.App()
    stack = _monitoring_stack(app, "TestManifestMonitoring")
    template = assertions.Template.from_stack(stack)

    for metric_name in [
        "collection_manifest_age_minutes",
        "collection_manifest_incomplete_tasks",
        "collection_manifest_retry_exhausted_tasks",
        "collection_manifest_low_coverage_gates",
        "collection_manifest_coverage_percent",
        "collection_provider_failure_tasks",
    ]:
        template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "Namespace": "StockMonitoring",
                "MetricName": metric_name,
            },
        )


def test_missing_collector_metric_alarms_breach_on_missing_data():
    app = cdk.App()
    stack = _monitoring_stack(app, "TestMissingMetricMonitoring")
    template = assertions.Template.from_stack(stack)

    for metric_name in [
        "stock_collection_completeness_percent",
        "news_collection_completeness_percent",
        "collection_manifest_incomplete_tasks",
    ]:
        template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "Namespace": "StockMonitoring",
                "MetricName": metric_name,
                "Statistic": "SampleCount",
                "ComparisonOperator": "LessThanThreshold",
                "Threshold": 1,
                "TreatMissingData": "breaching",
            },
        )


def test_publication_artifact_alarms_are_created():
    app = cdk.App()
    stack = _monitoring_stack(app, "TestPublicationMonitoring")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "Namespace": "StockaraPhase1",
            "MetricName": "artifact_publish_failures",
            "Statistic": "Sum",
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
            "Threshold": 1,
        },
    )

    for metric_name in ["top_picks_published", "sell_alerts_published"]:
        template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "Namespace": "StockaraPhase1",
                "MetricName": metric_name,
                "Statistic": "SampleCount",
                "ComparisonOperator": "LessThanThreshold",
                "Threshold": 1,
                "TreatMissingData": "breaching",
            },
        )


def test_daily_workflow_alarms_are_created():
    app = cdk.App()
    stack = _monitoring_stack(app, "TestDailyWorkflowMonitoring")
    template = assertions.Template.from_stack(stack)

    state_machine_arn = {
        "Fn::Join": [
            "",
            [
                "arn:",
                {"Ref": "AWS::Partition"},
                ":states:",
                {"Ref": "AWS::Region"},
                ":",
                {"Ref": "AWS::AccountId"},
                ":stateMachine:stockara-codex-test-daily-pipeline",
            ],
        ]
    }

    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "Namespace": "AWS/States",
            "MetricName": "ExecutionsFailed",
            "Statistic": "Sum",
            "Dimensions": [
                {
                    "Name": "StateMachineArn",
                    "Value": state_machine_arn,
                }
            ],
            "ComparisonOperator": "GreaterThanOrEqualToThreshold",
            "Threshold": 1,
        },
    )
    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "Namespace": "AWS/States",
            "MetricName": "ExecutionsStarted",
            "Statistic": "SampleCount",
            "ComparisonOperator": "LessThanThreshold",
            "Threshold": 1,
            "TreatMissingData": "breaching",
        },
    )
    for metric_name in ["daily_workflow_degraded", "daily_workflow_blocked"]:
        template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "Namespace": "StockaraPhase1",
                "MetricName": metric_name,
                "Statistic": "Sum",
                "ComparisonOperator": "GreaterThanOrEqualToThreshold",
                "Threshold": 1,
            },
        )


def test_product_quality_alarms_are_created():
    app = cdk.App()
    stack = _monitoring_stack(app, "TestProductQualityMonitoring")
    template = assertions.Template.from_stack(stack)

    expected_alarms = [
        (
            "StockaraPhase1",
            "top_picks_published",
            "Sum",
            "LessThanThreshold",
            1,
            "breaching",
        ),
        (
            "StockaraPhase1",
            "ai_candidates_analyzed",
            "Sum",
            "LessThanThreshold",
            1,
            "breaching",
        ),
        (
            "StockaraPhase1",
            "publication_suppressed",
            "Sum",
            "GreaterThanOrEqualToThreshold",
            1,
            "notBreaching",
        ),
        (
            "StockaraPhase1",
            "fallback_analyses",
            "Sum",
            "GreaterThanOrEqualToThreshold",
            1,
            "notBreaching",
        ),
        (
            "StockMonitoring",
            "news_sources_failed",
            "Sum",
            "GreaterThanOrEqualToThreshold",
            1,
            "notBreaching",
        ),
        (
            "StockMonitoring",
            "stock_collection_failed_tickers",
            "Sum",
            "GreaterThanOrEqualToThreshold",
            25,
            "notBreaching",
        ),
        (
            "StockMonitoring",
            "stock_price_gap_ticker_percent",
            "Maximum",
            "GreaterThanOrEqualToThreshold",
            2,
            "notBreaching",
        ),
        (
            "StockMonitoring",
            "earnings_provider_degraded_runs",
            "Sum",
            "GreaterThanOrEqualToThreshold",
            1,
            "notBreaching",
        ),
        (
            "StockMonitoring",
            "earnings_history_coverage_percent",
            "Minimum",
            "LessThanThreshold",
            90,
            "notBreaching",
        ),
        (
            "StockMonitoring",
            "earnings_history_provider_quota_exhausted_tickers",
            "Sum",
            "GreaterThanOrEqualToThreshold",
            1,
            "notBreaching",
        ),
        (
            "StockaraPhase1",
            "ai_review_invalid_response_exhausted",
            "Sum",
            "GreaterThanOrEqualToThreshold",
            1,
            "notBreaching",
        ),
        (
            "StockaraPhase1",
            "review_feature_missing_incidents",
            "Sum",
            "GreaterThanOrEqualToThreshold",
            1,
            "notBreaching",
        ),
        (
            "StockaraPhase1",
            "review_provider_failure_incidents",
            "Sum",
            "GreaterThanOrEqualToThreshold",
            1,
            "notBreaching",
        ),
        (
            "StockaraPhase1",
            "evidence_repair_candidates_failed",
            "Sum",
            "GreaterThanOrEqualToThreshold",
            1,
            "notBreaching",
        ),
    ]

    for (
        namespace,
        metric_name,
        statistic,
        comparison_operator,
        threshold,
        treat_missing_data,
    ) in expected_alarms:
        template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "Namespace": namespace,
                "MetricName": metric_name,
                "Statistic": statistic,
                "ComparisonOperator": comparison_operator,
                "Threshold": threshold,
                "TreatMissingData": treat_missing_data,
            },
        )


def test_product_quality_dashboard_widgets_are_created():
    app = cdk.App()
    stack = _monitoring_stack(app, "TestProductQualityDashboard")
    template = assertions.Template.from_stack(stack)
    resources = template.to_json()["Resources"]
    dashboard = next(
        resource
        for resource in resources.values()
        if resource["Type"] == "AWS::CloudWatch::Dashboard"
    )
    dashboard_body = json.dumps(dashboard["Properties"]["DashboardBody"])

    for widget_title in [
        "Publication freshness and suppression",
        "Daily workflow status",
        "Fallback and review gate usage",
        "Review evidence recovery",
        "Backfill and gap health",
        "Earnings history coverage",
    ]:
        assert widget_title in dashboard_body


def test_earnings_history_coverage_missing_metric_alarm_breaches():
    app = cdk.App()
    stack = _monitoring_stack(app, "TestEarningsHistoryMetricMonitoring")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::CloudWatch::Alarm",
        {
            "Namespace": "StockMonitoring",
            "MetricName": "earnings_history_coverage_percent",
            "Statistic": "SampleCount",
            "ComparisonOperator": "LessThanThreshold",
            "Threshold": 1,
            "TreatMissingData": "breaching",
        },
    )
