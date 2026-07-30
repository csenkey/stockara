"""Tests for the ApiStack CDK stack."""

import json
from unittest.mock import patch

import aws_cdk as cdk
import aws_cdk.assertions as assertions
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3

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
            "FunctionName": "stockara-codex-test-stock-collector",
            "Handler": "src.collectors.stock_collector.handler",
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "ALPHA_VANTAGE_API_KEY_SECRET_NAME": (
                            "stockara/codex-test/alpha-vantage-api-key-current"
                        )
                    }
                )
            },
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "stockara-codex-test-news-collector",
            "Handler": "src.collectors.news_collector.handler",
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "NEWSAPI_KEY_SECRET_NAME": (
                            "stockara/codex-test/newsapi-key-current"
                        ),
                        "FINNHUB_KEY_SECRET_NAME": (
                            "stockara/codex-test/finnhub-key-current"
                        ),
                        "ALPHA_VANTAGE_API_KEY_SECRET_NAME": (
                            "stockara/codex-test/alpha-vantage-api-key-current"
                        ),
                        "ALPHA_VANTAGE_NEWS_MAX_TICKERS": "25",
                    }
                )
            },
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "stockara-codex-test-evidence-collector",
            "Handler": "src.collectors.evidence_collector.handler",
            "Timeout": 600,
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "POWERTOOLS_SERVICE_NAME": "evidence-collector",
                        "FINNHUB_KEY_SECRET_NAME": (
                            "stockara/codex-test/finnhub-key-current"
                        ),
                        "EVIDENCE_SEC_FILING_LOOKBACK_DAYS": "45",
                        "EVIDENCE_ANALYST_LOOKBACK_DAYS": "45",
                        "EVIDENCE_SECTOR_LOOKBACK_DAYS": "7",
                        "EVIDENCE_MACRO_LOOKBACK_DAYS": "7",
                    }
                )
            },
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "stockara-codex-test-collection-distributor",
            "Handler": "src.collectors.collection_distributor.handler",
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "POWERTOOLS_SERVICE_NAME": "collection-distributor",
                        "COLLECTION_PRICE_TASK_CHUNK_SIZE": "10",
                        "COLLECTION_NEWS_TASK_CHUNK_SIZE": "50",
                        "COLLECTION_CALENDAR_TASK_CHUNK_SIZE": "10",
                        "COLLECTION_MAX_TASKS_PER_RUN": "4",
                    }
                )
            },
        },
    )
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Name": "stockara-codex-test-collection-distributor",
            "ScheduleExpression": "rate(5 minutes)",
        },
    )
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Name": "stockara-codex-test-news-collection",
            "ScheduleExpression": "cron(30 6,14,21 * * ? *)",
        },
    )
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Name": "stockara-codex-test-phase1-publish",
            "ScheduleExpression": "rate(5 minutes)",
            "Targets": assertions.Match.array_with(
                [
                    assertions.Match.object_like(
                        {
                            "Input": '{"mode":"daily"}',
                        }
                    )
                ]
            ),
        },
    )
    template.has_resource_properties(
        "AWS::CloudFormation::CustomResource",
        {
            "SellAlertTickers": "AAPL,MSFT,NVDA",
            "SeedHash": assertions.Match.string_like_regexp("^[0-9a-f]{64}$"),
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "stockara-codex-test-earnings-collector",
            "Handler": "src.collectors.earnings_collector.handler",
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "POWERTOOLS_SERVICE_NAME": "earnings-collector",
                        "ALPHA_VANTAGE_API_KEY_SECRET_NAME": (
                            "stockara/codex-test/alpha-vantage-api-key-current"
                        ),
                        "EARNINGS_ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS": "1.25",
                        "EARNINGS_ALPHA_VANTAGE_MAX_CALLS_PER_INVOCATION": "20",
                    }
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
                    {
                        "POWERTOOLS_SERVICE_NAME": "dividend-collector",
                        "FINNHUB_KEY_SECRET_NAME": (
                            "stockara/codex-test/finnhub-key-current"
                        ),
                        "ALPHA_VANTAGE_API_KEY_SECRET_NAME": (
                            "stockara/codex-test/alpha-vantage-api-key-current"
                        ),
                        "DIVIDEND_ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS": "1.25",
                        "DIVIDEND_ALPHA_VANTAGE_MAX_CALLS_PER_INVOCATION": "20",
                    }
                )
            },
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "stockara-codex-test-stooq-zip-extractor",
            "Handler": "src.scripts.stooq_zip_extractor.handler",
            "MemorySize": 1024,
            "EphemeralStorage": {"Size": 2048},
            "Timeout": 900,
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "POWERTOOLS_SERVICE_NAME": "stooq-zip-extractor",
                        "STOOQ_ZIP_KEY": "stooq/data.zip",
                        "STOOQ_EXTRACTED_PREFIX": "stooq-extracted/",
                        "STOCK_COLLECTOR_FUNCTION_NAME": (
                            "stockara-codex-test-stock-collector"
                        ),
                    }
                )
            },
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "stockara-codex-test-stock-gap-scanner",
            "Handler": "src.collectors.stock_gap_scanner.handler",
            "Timeout": 300,
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "POWERTOOLS_SERVICE_NAME": "stock-gap-scanner",
                        "STOCK_GAP_SCAN_LOOKBACK_DAYS": "90",
                        "STOCK_GAP_SCAN_MAX_TASKS": "250",
                        "STOCK_GAP_TASK_MAX_RANGE_DAYS": "14",
                    }
                )
            },
        },
    )
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Name": "stockara-codex-test-stock-gap-scan",
            "ScheduleExpression": "cron(15 23 * * ? *)",
        },
    )
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Name": "stockara-codex-test-dividend-collection",
            "ScheduleExpression": "cron(15 20 * * ? *)",
        },
    )
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Name": "stockara-codex-test-evidence-collection",
            "ScheduleExpression": "cron(45 20 * * ? *)",
        },
    )


def test_daily_pipeline_state_machine_is_created_in_shadow_mode():
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

    template.resource_count_is("AWS::StepFunctions::StateMachine", 1)
    resources = template.find_resources("AWS::StepFunctions::StateMachine")
    state_machine = next(iter(resources.values()))
    properties = state_machine["Properties"]
    definition = json.dumps(properties["DefinitionString"])

    assert properties["StateMachineName"] == "stockara-codex-test-daily-pipeline"
    assert properties["StateMachineType"] == "STANDARD"
    assert "Manual/shadow daily Stockara workflow" in definition
    for state_name in [
        "SyncStaticMetadata",
        "CreateOrRefreshManifest",
        "CollectPrices",
        "RepairPriceGaps",
        "CollectNews",
        "CollectCalendarsAndEvidence",
        "CollectEarnings",
        "CollectDividends",
        "CollectEvidence",
        "WaitForAnalysisWindow",
        "AnalyzeAndPublish",
        "SyncStaticMetadataFailed",
        "CollectNewsFailed",
        "CollectCalendarsAndEvidenceFailed",
        "AnalyzeAndPublishFailed",
    ]:
        assert state_name in definition
    assert "MaxAttempts" in definition
    assert "Catch" in definition
    assert "States.ALL" in definition
    assert "ProviderThrottled" in definition
    assert "CollectionManifestIncomplete" in definition
    assert "OpenAITransientError" in definition
    assert "ArtifactPublishFailed" in definition
    assert "Parallel" in definition
    assert "Give collectors time to finish before the 22:00 UTC analysis gate." in definition
    assert "repair_news" in definition
    assert "repair_calendars" in definition
    assert "repair_evidence" in definition
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Name": "stockara-codex-test-daily-pipeline",
            "ScheduleExpression": "cron(5 21 * * ? *)",
            "Targets": assertions.Match.array_with(
                [
                    assertions.Match.object_like(
                        {
                            "Input": '{"workflow":"scheduled_daily_step_functions"}',
                        }
                    )
                ]
            ),
        },
    )
    template.has_output(
        "DailyWorkflowStateMachineName",
        assertions.Match.object_like(
            {"Description": "Manual/shadow Step Functions workflow for the daily pipeline"}
        ),
    )
    template.resource_count_is("AWS::Events::Rule", 9)
