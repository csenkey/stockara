"""Tests for the ApiStack CDK stack."""

import aws_cdk as cdk
import aws_cdk.assertions as assertions
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from unittest.mock import patch

from stacks.api_stack import ApiStack


def test_calendar_collector_lambdas_and_schedules_are_created():
    app = cdk.App()
    stack = cdk.Stack(app, "Deps")
    table = dynamodb.Table(
        stack,
        "DataTable",
        partition_key=dynamodb.Attribute(
            name="PK", type=dynamodb.AttributeType.STRING
        ),
        sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
    )
    bucket = s3.Bucket(stack, "Artifacts")
    code = lambda_.Code.from_inline("def handler(event, context): return {}")
    with patch("stacks.api_stack._lambda.Code.from_asset", return_value=code):
        api_stack = ApiStack(
            app,
            "ApiTest",
            data_table=table,
            artifact_bucket=bucket,
            deployment_stage="codex-test",
        )
    template = assertions.Template.from_stack(api_stack)

    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "stockara-codex-test-earnings-collector",
            "Handler": "src.collectors.earnings_collector.handler",
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {"POWERTOOLS_SERVICE_NAME": "earnings-collector"}
                )
            },
        },
    )
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Name": "stockara-codex-test-earnings-collection",
            "ScheduleExpression": "cron(0 20 * * ? *)",
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "stockara-codex-test-dividend-collector",
            "Handler": "src.collectors.dividend_collector.handler",
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {"POWERTOOLS_SERVICE_NAME": "dividend-collector"}
                )
            },
        },
    )
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Name": "stockara-codex-test-dividend-collection",
            "ScheduleExpression": "cron(15 20 * * ? *)",
        },
    )
