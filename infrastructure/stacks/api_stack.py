"""CDK stack for the Phase 1 API, collectors, analyzer, and publisher."""

import os

from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
    aws_apigateway as apigw,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_s3 as s3,
)
from constructs import Construct

from .naming import resource_name


BACKEND_ASSET_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend")
)


class ApiStack(Stack):
    """Phase 1 compute stack.

    Public reads are served from the frontend/artifact bucket. API Gateway is
    retained only for `/api/health`; all picks and alerts are static JSON.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        data_table: dynamodb.ITable,
        artifact_bucket: s3.IBucket,
        deployment_stage: str = "prod",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        lambda_base_policy = iam.ManagedPolicy.from_aws_managed_policy_name(
            "service-role/AWSLambdaBasicExecutionRole"
        )

        batch_role = iam.Role(
            self,
            "Phase1BatchRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[lambda_base_policy],
            description="Role for Phase 1 collectors, scanner, analyzer, and publisher",
        )
        batch_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["cloudwatch:PutMetricData", "secretsmanager:GetSecretValue"],
                resources=["*"],
            )
        )

        api_role = iam.Role(
            self,
            "HealthApiRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[lambda_base_policy],
            description="Role for public health API",
        )

        common_env = {
            "POWERTOOLS_SERVICE_NAME": "stockara-phase1",
            "LOG_LEVEL": "INFO",
            "STOCKARA_TABLE_NAME": data_table.table_name,
            "STOCKARA_ARTIFACT_BUCKET": artifact_bucket.bucket_name,
            "DEPLOYMENT_STAGE": deployment_stage,
        }

        self.stock_collector_fn = _lambda.Function(
            self,
            "StockCollectorFunction",
            function_name=resource_name(
                deployment_stage,
                "stockara-stock-collector",
                "stock-collector",
            ),
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.collectors.stock_collector.handler",
            code=_lambda.Code.from_asset(BACKEND_ASSET_PATH),
            memory_size=512,
            timeout=Duration.minutes(15),
            role=batch_role,
            environment={**common_env, "POWERTOOLS_SERVICE_NAME": "stock-collector"},
            description="Collects daily OHLCV data for the Phase 1 universe",
        )

        self.news_collector_fn = _lambda.Function(
            self,
            "NewsCollectorFunction",
            function_name=resource_name(
                deployment_stage,
                "stockara-news-collector",
                "news-collector",
            ),
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.collectors.news_collector.handler",
            code=_lambda.Code.from_asset(BACKEND_ASSET_PATH),
            memory_size=256,
            timeout=Duration.minutes(5),
            role=batch_role,
            environment={**common_env, "POWERTOOLS_SERVICE_NAME": "news-collector"},
            description="Collects and summarizes news for Phase 1 signals",
        )

        self.ai_analyzer_fn = _lambda.Function(
            self,
            "Phase1AnalyzerPublisherFunction",
            function_name=resource_name(
                deployment_stage,
                "stockara-phase1-analyzer-publisher",
                "phase1-analyzer-publisher",
            ),
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.analysis.ai_analyzer.handler",
            code=_lambda.Code.from_asset(BACKEND_ASSET_PATH),
            memory_size=1024,
            timeout=Duration.minutes(15),
            role=batch_role,
            environment={**common_env, "POWERTOOLS_SERVICE_NAME": "phase1-publisher"},
            description="Scores candidates, analyzes shortlist, and publishes static top picks",
        )

        self.api_handler_fn = _lambda.Function(
            self,
            "HealthApiFunction",
            function_name=resource_name(
                deployment_stage,
                "stockara-health-api",
                "health-api",
            ),
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.api.handler.handler",
            code=_lambda.Code.from_asset(BACKEND_ASSET_PATH),
            memory_size=256,
            timeout=Duration.seconds(15),
            role=api_role,
            environment={**common_env, "POWERTOOLS_SERVICE_NAME": "health-api"},
            description="Serves the public health endpoint",
        )

        data_table.grant_read_write_data(self.stock_collector_fn)
        data_table.grant_read_write_data(self.news_collector_fn)
        data_table.grant_read_write_data(self.ai_analyzer_fn)
        data_table.grant_read_data(self.api_handler_fn)
        artifact_bucket.grant_put(self.ai_analyzer_fn)

        self.api = apigw.RestApi(
            self,
            "StockaraHealthApi",
            rest_api_name=resource_name(deployment_stage, "Stockara Health API", "api"),
            description="Small public health API for the Stockara Phase 1 pipeline",
            deploy_options=apigw.StageOptions(
                stage_name=deployment_stage,
                throttling_rate_limit=20,
                throttling_burst_limit=40,
            ),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=["GET", "OPTIONS"],
                allow_headers=["Content-Type"],
            ),
        )

        api_resource = self.api.root.add_resource("api")
        health_resource = api_resource.add_resource("health")
        health_resource.add_method("GET", apigw.LambdaIntegration(self.api_handler_fn))

        events.Rule(
            self,
            "StockCollectionSchedule",
            rule_name=resource_name(
                deployment_stage,
                "stockara-stock-collection",
                "stock-collection",
            ),
            description="Triggers stock data collection daily at 21:00 UTC",
            schedule=events.Schedule.cron(
                minute="0", hour="21", day="*", month="*", year="*"
            ),
            targets=[targets.LambdaFunction(self.stock_collector_fn)],
        )

        events.Rule(
            self,
            "NewsCollectionSchedule",
            rule_name=resource_name(
                deployment_stage,
                "stockara-news-collection",
                "news-collection",
            ),
            description="Triggers news collection daily before analysis",
            schedule=events.Schedule.cron(
                minute="30", hour="20", day="*", month="*", year="*"
            ),
            targets=[targets.LambdaFunction(self.news_collector_fn)],
        )

        events.Rule(
            self,
            "Phase1PublishSchedule",
            rule_name=resource_name(
                deployment_stage,
                "stockara-phase1-publish",
                "phase1-publish",
            ),
            description="Publishes static top picks and sell alerts daily at 22:00 UTC",
            schedule=events.Schedule.cron(
                minute="0", hour="22", day="*", month="*", year="*"
            ),
            targets=[targets.LambdaFunction(self.ai_analyzer_fn)],
        )

        CfnOutput(
            self,
            "ApiUrl",
            value=self.api.url,
            description="Base URL for the public health API",
        )
