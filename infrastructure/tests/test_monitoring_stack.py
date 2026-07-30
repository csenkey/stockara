"""Tests for the MonitoringStack CDK stack."""

import json

import aws_cdk as cdk
import aws_cdk.assertions as assertions

from stacks.monitoring_stack import MonitoringStack


def test_collector_completeness_alarms_are_created():
    app = cdk.App()
    stack = MonitoringStack(app, "TestMonitoring", deployment_stage="codex-test")
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


def test_collection_manifest_health_alarms_are_created():
    app = cdk.App()
    stack = MonitoringStack(app, "TestManifestMonitoring", deployment_stage="codex-test")
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
    stack = MonitoringStack(app, "TestMissingMetricMonitoring", deployment_stage="codex-test")
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
    stack = MonitoringStack(app, "TestPublicationMonitoring", deployment_stage="codex-test")
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
    stack = MonitoringStack(app, "TestDailyWorkflowMonitoring", deployment_stage="codex-test")
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
    stack = MonitoringStack(
        app, "TestProductQualityMonitoring", deployment_stage="codex-test"
    )
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
            "stock_price_gaps_detected",
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
    stack = MonitoringStack(
        app, "TestProductQualityDashboard", deployment_stage="codex-test"
    )
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
        "Backfill and gap health",
    ]:
        assert widget_title in dashboard_body
