"""CDK stack for the Phase 1 API, collectors, analyzer, and publisher."""

import os

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    CustomResource,
    Duration,
    Stack,
    aws_apigateway as apigw,
    custom_resources as cr,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

from .naming import resource_name


BACKEND_ASSET_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend")
)
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
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
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
            )
        )
        openai_api_key_secret_name = f"stockara/{deployment_stage}/openai-api-key-current"
        openai_api_key_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "OpenAiApiKeySecret",
            openai_api_key_secret_name,
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
        openai_env = {"OPENAI_API_KEY_SECRET_NAME": openai_api_key_secret_name}
        backend_code = _lambda.Code.from_asset(
            BACKEND_ASSET_PATH,
            bundling=BundlingOptions(
                image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                command=[
                    "bash",
                    "-c",
                    "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output",
                ],
            ),
        )

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
            code=backend_code,
            memory_size=512,
            timeout=Duration.minutes(15),
            role=batch_role,
            environment={
                **common_env,
                "POWERTOOLS_SERVICE_NAME": "stock-collector",
                "STOCK_COLLECTOR_BATCH_SIZE": "5",
                "STOCK_COLLECTOR_MAX_TICKERS": "25",
                "STOCK_INITIAL_HISTORY_PERIOD": "5y",
                "STOCK_INCREMENTAL_PERIOD": "10d",
                "YFINANCE_BATCH_PAUSE_SECONDS": "1",
            },
            description="Collects daily OHLCV data for the Phase 1 universe",
        )

        self.watchlist_seed_fn = _lambda.Function(
            self,
            "WatchlistSeedFunction",
            function_name=resource_name(
                deployment_stage,
                "stockara-watchlist-seed",
                "watchlist-seed",
            ),
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="backend.src.scripts.seed_watchlist_handler.handler",
            code=_lambda.Code.from_asset(
                PROJECT_ROOT,
                exclude=[
                    ".git",
                    ".hypothesis",
                    ".pytest_cache",
                    ".ruff_cache",
                    "docs",
                    "frontend/node_modules",
                    "frontend/dist",
                    "infrastructure/cdk.out",
                ],
            ),
            memory_size=256,
            timeout=Duration.minutes(3),
            role=batch_role,
            description="Seeds the Phase 1 watchlist on first deploy only",
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
            code=backend_code,
            memory_size=256,
            timeout=Duration.minutes(5),
            role=batch_role,
            environment={
                **common_env,
                **openai_env,
                "OPENAI_NEWS_MODEL": "gpt-5.4-mini",
                "POWERTOOLS_SERVICE_NAME": "news-collector",
            },
            description="Collects and summarizes news for Phase 1 signals",
        )

        self.earnings_collector_fn = _lambda.Function(
            self,
            "EarningsCollectorFunction",
            function_name=resource_name(
                deployment_stage,
                "stockara-earnings-collector",
                "earnings-collector",
            ),
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.collectors.earnings_collector.handler",
            code=backend_code,
            memory_size=256,
            timeout=Duration.minutes(10),
            role=batch_role,
            environment={
                **common_env,
                "POWERTOOLS_SERVICE_NAME": "earnings-collector",
            },
            description="Collects earnings calendar events and historical reactions",
        )

        self.dividend_collector_fn = _lambda.Function(
            self,
            "DividendCollectorFunction",
            function_name=resource_name(
                deployment_stage,
                "stockara-dividend-collector",
                "dividend-collector",
            ),
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.collectors.dividend_collector.handler",
            code=backend_code,
            memory_size=256,
            timeout=Duration.minutes(10),
            role=batch_role,
            environment={
                **common_env,
                "POWERTOOLS_SERVICE_NAME": "dividend-collector",
            },
            description="Collects dividend calendar events and historical reactions",
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
            code=backend_code,
            memory_size=1024,
            timeout=Duration.minutes(15),
            role=batch_role,
            environment={
                **common_env,
                **openai_env,
                "OPENAI_ANALYSIS_MODEL": "gpt-5.4-mini",
                "OPENAI_REVIEW_MODEL": "gpt-5.4",
                "POWERTOOLS_SERVICE_NAME": "phase1-publisher",
            },
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
            code=backend_code,
            memory_size=256,
            timeout=Duration.seconds(15),
            role=api_role,
            environment={**common_env, "POWERTOOLS_SERVICE_NAME": "health-api"},
            description="Serves the public health endpoint",
        )

        data_table.grant_read_write_data(self.stock_collector_fn)
        data_table.grant_read_write_data(self.news_collector_fn)
        data_table.grant_read_write_data(self.earnings_collector_fn)
        data_table.grant_read_write_data(self.dividend_collector_fn)
        data_table.grant_read_write_data(self.ai_analyzer_fn)
        data_table.grant_read_write_data(self.watchlist_seed_fn)
        data_table.grant_read_data(self.api_handler_fn)
        artifact_bucket.grant_put(self.ai_analyzer_fn)
        openai_api_key_secret.grant_read(self.news_collector_fn)
        openai_api_key_secret.grant_read(self.ai_analyzer_fn)

        watchlist_seed_provider = cr.Provider(
            self,
            "WatchlistSeedProvider",
            on_event_handler=self.watchlist_seed_fn,
        )
        self.watchlist_seed = CustomResource(
            self,
            "WatchlistSeed",
            service_token=watchlist_seed_provider.service_token,
            properties={
                "TableName": data_table.table_name,
                "SellAlertTickers": "AAPL,MSFT,NVDA",
            },
        )

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

        stock_collection_rule = events.Rule(
            self,
            "StockCollectionSchedule",
            rule_name=resource_name(
                deployment_stage,
                "stockara-stock-collection",
                "stock-collection",
            ),
            description="Triggers bounded stock data collection every 15 minutes",
            schedule=events.Schedule.rate(Duration.minutes(15)),
            targets=[
                targets.LambdaFunction(
                    self.stock_collector_fn,
                    event=events.RuleTargetInput.from_object({"max_tickers": 25}),
                )
            ],
        )
        stock_collection_rule.node.add_dependency(self.watchlist_seed)

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
            "EarningsCollectionSchedule",
            rule_name=resource_name(
                deployment_stage,
                "stockara-earnings-collection",
                "earnings-collection",
            ),
            description="Triggers earnings calendar collection daily before publication",
            schedule=events.Schedule.cron(
                minute="0", hour="20", day="*", month="*", year="*"
            ),
            targets=[
                targets.LambdaFunction(
                    self.earnings_collector_fn,
                    event=events.RuleTargetInput.from_object({"max_tickers": 50}),
                )
            ],
        )

        events.Rule(
            self,
            "DividendCollectionSchedule",
            rule_name=resource_name(
                deployment_stage,
                "stockara-dividend-collection",
                "dividend-collection",
            ),
            description="Triggers dividend calendar collection daily before publication",
            schedule=events.Schedule.cron(
                minute="15", hour="20", day="*", month="*", year="*"
            ),
            targets=[
                targets.LambdaFunction(
                    self.dividend_collector_fn,
                    event=events.RuleTargetInput.from_object({"max_tickers": 50}),
                )
            ],
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
