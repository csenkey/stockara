"""Tests for the ApiStack CDK stack."""

import json
from unittest.mock import patch

import aws_cdk as cdk
import aws_cdk.assertions as assertions
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3

from stacks.api_stack import BACKEND_ASSET_PATH
from stacks.api_stack import BACKEND_WATCHLIST_SEED_PATH
from stacks.api_stack import ApiStack
from stacks.api_stack import _file_sha256


def test_calendar_collector_lambdas_and_schedules_are_created():
    app = cdk.App()
    stack = cdk.Stack(app, "Deps")
    table = dynamodb.Table(
        stack,
        "DataTable",
        partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
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
            "Name": "stockara-codex-test-stock-collection",
            "ScheduleExpression": "rate(15 minutes)",
            "State": "DISABLED",
        },
    )
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "Name": "stockara-codex-test-collection-distributor",
            "ScheduleExpression": "rate(5 minutes)",
            "State": "DISABLED",
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
            "State": "DISABLED",
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
            "SeedHash": _file_sha256(BACKEND_WATCHLIST_SEED_PATH),
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "stockara-codex-test-watchlist-seed",
            "Handler": "src.scripts.seed_watchlist_handler.handler",
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
    earnings_rules = template.find_resources(
        "AWS::Events::Rule",
        {"Properties": {"Name": "stockara-codex-test-earnings-collection"}},
    )
    assert len(earnings_rules) == 1
    earnings_target = next(iter(earnings_rules.values()))["Properties"]["Targets"][0]
    assert "Input" not in earnings_target
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


def test_daily_pipeline_state_machine_is_created_for_daily_production_run():
    app = cdk.App()
    stack = cdk.Stack(app, "Deps")
    table = dynamodb.Table(
        stack,
        "DataTable",
        partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
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

    # Resolve only CDK's ARN tokens; exercise the actual synthesized data paths.
    asl = json.loads(
        "".join(
            part if isinstance(part, str) else "arn:test"
            for part in properties["DefinitionString"]["Fn::Join"][1]
        )
    )
    _assert_bounded_parallel_payloads(asl["States"])
    readiness_choices = asl["States"]["DecidePublicationReadiness"]["Choices"]
    optional_paths = {
        "$.analysis.Payload.body.workflow_decision",
        "$.analysis.Payload.body.stage",
    }
    for choice in readiness_choices:
        referenced_optional_paths = {
            condition.get("Variable")
            for condition in choice.get("And", [])
            if condition.get("Variable") in optional_paths
        }
        for path in referenced_optional_paths:
            assert {
                "Variable": path,
                "IsPresent": True,
            } in choice["And"]

    assert properties["StateMachineName"] == "stockara-codex-test-daily-pipeline"
    assert properties["StateMachineType"] == "STANDARD"
    assert "Daily Stockara workflow coordinating coarse" in definition
    assert "fallback_max_tickers" in definition
    for state_name in [
        "SyncStaticMetadata",
        "CreateOrRefreshManifest",
        "DispatchManifestTasks",
        "WaitForManifestDispatch",
        "DecideManifestDispatchReadiness",
        "ManifestDispatchExhausted",
        "CollectPrices",
        "RepairPriceGaps",
        "CollectNews",
        "RecordNewsDegraded",
        "CollectCalendarsAndEvidence",
        "CollectEarnings",
        "RecordEarningsDegraded",
        "CollectDividends",
        "RecordDividendsDegraded",
        "CollectEvidence",
        "RecordEvidenceDegraded",
        "WaitForAnalysisWindow",
        "AnalyzeAndPublish",
        "CollectReviewEvidence",
        "IsReviewNewsRepairRequired",
        "RepairReviewNews",
        "SkipReviewNews",
        "IsReviewEvidenceRepairRequired",
        "RepairReviewEvidence",
        "SkipReviewEvidence",
        "ReanalyzeAfterEvidence",
        "WaitForAnalysisProgress",
        "DecideAnalysisProgress",
        "DecidePublicationReadiness",
        "Publish",
        "PublishDegraded",
        "WaitOrRepair",
        "Blocked",
        "ClassifyWorkflowFailure",
        "RecordCollectPricesFailure",
        "RecordAnalyzeAndPublishFailure",
        "PublishWorkflowStatus",
        "PublishWorkflowStatusFailed",
    ]:
        assert state_name in definition
    assert "Classify the daily run after analyzer publication" in definition
    assert "publish_degraded" in definition
    assert "wait_or_repair" in definition
    assert "workflow_decision" in definition
    assert "Publication suppressed:*" in definition
    assert "publish_workflow_status" in definition
    assert "$$.Execution.Id" in definition
    assert "$$.Execution.Name" in definition
    assert "$$.Execution.StartTime" in definition
    assert "MaxAttempts" in definition
    assert "Catch" in definition
    assert "States.ALL" in definition
    assert "ProviderThrottled" in definition
    assert "CollectionManifestIncomplete" in definition
    assert "OpenAITransientError" in definition
    assert "ArtifactPublishFailed" in definition
    assert "Parallel" in definition
    assert "Wait only until the manifest's configured analysis timestamp." in definition
    assert (
        "Continue bounded analyzer batches until publication is terminal." in definition
    )
    assert (
        "Wait for leased manifest worker Lambdas before dispatching more chunks."
        in definition
    )
    assert "dispatch_ready_for_analysis" in definition
    assert "dispatch_deadline_exceeded" in definition
    assert "analysis_not_before" in definition
    assert "$.manifest.Payload.body.manifest_date" in definition
    assert "max_tasks_per_run" in definition
    assert '\\"max_tasks_per_run\\":0' in definition
    assert '\\"max_tasks_per_run\\":4' in definition
    assert '\\"task_types\\":[\\"price\\"]' in definition
    assert '\\"Seconds\\":60' in definition
    assert "repair_news" in definition
    assert "repair_calendars" in definition
    assert "repair_evidence" in definition
    assert "repair_review_evidence" in definition
    assert "evidence_repair_needed" in definition
    assert "targeted_tickers" in definition
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
            {
                "Description": "Manual/shadow Step Functions workflow for the daily pipeline"
            }
        ),
    )
    template.resource_count_is("AWS::Events::Rule", 11)
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Handler": "src.services.workflow_reporter.handler",
            "Timeout": 120,
        },
    )
    template.has_resource_properties(
        "AWS::SQS::Queue",
        {
            "FifoQueue": True,
            "ContentBasedDeduplication": True,
            "VisibilityTimeout": 180,
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::EventSourceMapping",
        {"BatchSize": 1},
    )
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "EventPattern": assertions.Match.object_like(
                {
                    "source": ["aws.states"],
                    "detail": assertions.Match.object_like(
                        {"status": ["SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"]}
                    ),
                }
            ),
        },
    )
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "ScheduleExpression": "cron(20 0,1,6 * * ? *)",
        },
    )


def _assert_bounded_parallel_payloads(states):
    """Model Parallel input/result processing with production-sized context."""
    from copy import deepcopy

    original = {
        "prices": {"details": "x" * 90_000},
        "analysis": {"Payload": {"body": {"run_date": "2026-08-25"}}},
    }
    data = deepcopy(original)
    for name in ["CollectCalendarsAndEvidence"] + ["CollectReviewEvidence"] * 5:
        state = states[name]
        branch_input = data
        if "Parameters" in state:
            branch_input = {
                key.removesuffix(".$"): deepcopy(data[value[2:]])
                if key.endswith(".$")
                else value
                for key, value in state["Parameters"].items()
            }
        outputs = []
        for branch in state["Branches"]:
            # Success, skipped and caught-error paths must all remain bounded.
            for end in (s for s in branch["States"].values() if s.get("End")):
                result = deepcopy(branch_input)
                if end.get("Type") == "Pass" and "Result" in end:
                    result = end["Result"]
                elif "ResultPath" in end:
                    result[end["ResultPath"][2:]] = {"status": "partial"}
                if end.get("OutputPath", "$") != "$":
                    result = result[end["OutputPath"][2:]]
                assert len(json.dumps(result).encode()) < 32_768
            outputs.append(result)
        assert len(json.dumps(outputs).encode()) < 65_536
        if state.get("ResultPath") is not None:
            data[state["ResultPath"][2:]] = outputs
        assert len(json.dumps(data).encode()) < 196_608
    assert data["analysis"] == original["analysis"]


def test_daily_pipeline_state_machine_iam_is_scoped_to_workflow_lambdas():
    app = cdk.App()
    stack = cdk.Stack(app, "Deps")
    table = dynamodb.Table(
        stack,
        "DataTable",
        partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
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
        "AWS::IAM::Role",
        {
            "AssumeRolePolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Action": "sts:AssumeRole",
                                "Principal": {"Service": "states.amazonaws.com"},
                            }
                        )
                    ]
                )
            }
        },
    )
    resources = template.to_json()["Resources"]
    workflow_policy = next(
        resource
        for resource in resources.values()
        if resource["Type"] == "AWS::IAM::Policy"
        and resource["Properties"]["PolicyName"].startswith(
            "DailyPipelineWorkflowRoleDefaultPolicy"
        )
    )
    invoked_lambda_ids = {
        statement["Resource"][0]["Fn::GetAtt"][0]
        for statement in workflow_policy["Properties"]["PolicyDocument"]["Statement"]
        if statement.get("Action") == "lambda:InvokeFunction"
    }
    assert invoked_lambda_ids == {
        "WatchlistSeedFunction5D4797FE",
        "CollectionDistributorFunction304D7CC9",
        "StockCollectorFunctionCA81C1AF",
        "NewsCollectorFunctionC5829D81",
        "EarningsCollectorFunctionDB794F40",
        "DividendCollectorFunction8FB42B38",
        "EvidenceCollectorFunctionE44F8DC8",
        "Phase1AnalyzerPublisherFunction7EEB9A09",
    }
    reporter_policy = next(
        resource["Properties"]["PolicyDocument"]
        for resource in resources.values()
        if resource["Type"] == "AWS::IAM::Policy"
        and resource["Properties"]["PolicyName"].startswith(
            "WorkflowReporterFunctionServiceRoleDefaultPolicy"
        )
    )
    policy_text = json.dumps(reporter_policy)
    assert "states:GetExecutionHistory" in policy_text
    assert "workflow/*" in policy_text
    assert "secretsmanager:" not in policy_text
    assert "dynamodb:" not in policy_text
    assert "lambda:InvokeFunction" not in policy_text
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Action": "states:StartExecution",
                                "Resource": {"Ref": "DailyPipelineWorkflow331E993A"},
                            }
                        )
                    ]
                )
            }
        },
    )


def test_watchlist_seed_lambda_uses_dependency_bundled_backend_asset():
    app = cdk.App()
    stack = cdk.Stack(app, "Deps")
    table = dynamodb.Table(
        stack,
        "DataTable",
        partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
        sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
    )
    bucket = s3.Bucket(stack, "Artifacts")
    code = lambda_.Code.from_inline("def handler(event, context): return {}")
    with patch("stacks.api_stack._lambda.Code.from_asset", return_value=code) as asset:
        ApiStack(
            app,
            "ApiTest",
            data_table=table,
            artifact_bucket=bucket,
            deployment_stage="codex-test",
        )

    assert [call.args[0] for call in asset.call_args_list] == [BACKEND_ASSET_PATH]


def test_authentication_and_protected_holding_review_api_are_created():
    app = cdk.App()
    stack = cdk.Stack(app, "Deps")
    table = dynamodb.Table(
        stack,
        "DataTable",
        partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
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
            site_url="https://stocks.example.com",
            deployment_stage="codex-test",
        )
    template = assertions.Template.from_stack(api_stack)

    template.has_resource_properties(
        "AWS::Cognito::UserPool",
        {
            "AutoVerifiedAttributes": ["email"],
            "UsernameAttributes": ["email"],
            "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": False},
        },
    )
    template.has_resource_properties(
        "AWS::Cognito::UserPoolClient",
        {
            "AllowedOAuthFlows": ["code"],
            "AllowedOAuthScopes": ["openid", "email", "profile"],
            "CallbackURLs": ["https://stocks.example.com/?auth=callback"],
            "LogoutURLs": ["https://stocks.example.com"],
            "SupportedIdentityProviders": ["COGNITO"],
        },
    )
    template.has_resource_properties(
        "AWS::Cognito::ManagedLoginBranding",
        {"UseCognitoProvidedValues": True},
    )
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {
            "HttpMethod": "POST",
            "AuthorizationType": "COGNITO_USER_POOLS",
        },
    )


def test_optional_google_facebook_and_apple_identity_providers_are_created():
    app = cdk.App()
    stack = cdk.Stack(app, "Deps")
    table = dynamodb.Table(
        stack,
        "DataTable",
        partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
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
            google_oauth_client_id="google-client",
            google_oauth_client_secret_name="stockara/test/google-secret",
            facebook_oauth_client_id="facebook-client",
            facebook_oauth_client_secret_name="stockara/test/facebook-secret",
            apple_oauth_client_id="apple-service-id",
            apple_oauth_team_id="apple-team",
            apple_oauth_key_id="apple-key",
            apple_oauth_private_key_secret_name="stockara/test/apple-key",
            deployment_stage="codex-test",
        )
    template = assertions.Template.from_stack(api_stack)

    for provider_type in ["Google", "Facebook", "SignInWithApple"]:
        template.has_resource_properties(
            "AWS::Cognito::UserPoolIdentityProvider",
            {"ProviderType": provider_type},
        )
    template.has_resource_properties(
        "AWS::Cognito::UserPoolClient",
        {
            "SupportedIdentityProviders": [
                "COGNITO",
                "Google",
                "Facebook",
                "SignInWithApple",
            ]
        },
    )
