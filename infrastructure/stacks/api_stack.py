"""CDK stack for the Phase 1 API, collectors, analyzer, and publisher."""

import hashlib
import os

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    CustomResource,
    Duration,
    Size,
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
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as sfn_tasks,
)
from constructs import Construct

from .naming import resource_name


BACKEND_ASSET_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend")
)
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
BACKEND_WATCHLIST_SEED_PATH = os.path.join(
    BACKEND_ASSET_PATH, "src", "data", "watchlist_seed.csv"
)


def _file_sha256(path: str) -> str:
    with open(path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


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
        newsapi_key_secret_name = f"stockara/{deployment_stage}/newsapi-key-current"
        newsapi_key_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "NewsApiKeySecret",
            newsapi_key_secret_name,
        )
        finnhub_key_secret_name = f"stockara/{deployment_stage}/finnhub-key-current"
        finnhub_key_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "FinnhubKeySecret",
            finnhub_key_secret_name,
        )
        alpha_vantage_key_secret_name = (
            f"stockara/{deployment_stage}/alpha-vantage-api-key-current"
        )
        alpha_vantage_key_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "AlphaVantageApiKeySecret",
            alpha_vantage_key_secret_name,
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
        market_data_env = {
            "ALPHA_VANTAGE_API_KEY_SECRET_NAME": alpha_vantage_key_secret_name,
        }
        news_provider_env = {
            "NEWSAPI_KEY_SECRET_NAME": newsapi_key_secret_name,
            "FINNHUB_KEY_SECRET_NAME": finnhub_key_secret_name,
            "ALPHA_VANTAGE_API_KEY_SECRET_NAME": alpha_vantage_key_secret_name,
        }
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

        stock_collector_function_name = resource_name(
            deployment_stage,
            "stockara-stock-collector",
            "stock-collector",
        )
        self.stock_collector_fn = _lambda.Function(
            self,
            "StockCollectorFunction",
            function_name=stock_collector_function_name,
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.collectors.stock_collector.handler",
            code=backend_code,
            memory_size=512,
            timeout=Duration.minutes(15),
            role=batch_role,
            environment={
                **common_env,
                **market_data_env,
                "POWERTOOLS_SERVICE_NAME": "stock-collector",
                "STOCKARA_STOCK_HISTORY_BUCKET": artifact_bucket.bucket_name,
                "STOCK_COLLECTOR_BATCH_SIZE": "5",
                "STOCK_COLLECTOR_MAX_TICKERS": "10",
                "STOCK_HISTORICAL_BACKFILL_TICKERS_PER_RUN": "1",
                "STOCK_HISTORICAL_BACKFILL_MAX_CHAINED_INVOCATIONS": "1500",
                "STOCK_INITIAL_HISTORY_PERIOD": "5y",
                "STOCK_INCREMENTAL_PERIOD": "10d",
                "NASDAQ_HISTORICAL_MAX_RECORDS_PER_TICKER": "1500",
                "STOOQ_HISTORICAL_MAX_RECORDS_PER_TICKER": "1500",
                "YFINANCE_BATCH_PAUSE_SECONDS": "1",
            },
            description="Collects daily OHLCV data for the Phase 1 universe",
        )

        self.stooq_zip_extractor_fn = _lambda.Function(
            self,
            "StooqZipExtractorFunction",
            function_name=resource_name(
                deployment_stage,
                "stockara-stooq-zip-extractor",
                "stooq-zip-extractor",
            ),
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.scripts.stooq_zip_extractor.handler",
            code=backend_code,
            memory_size=1024,
            ephemeral_storage_size=Size.gibibytes(2),
            timeout=Duration.minutes(15),
            role=batch_role,
            environment={
                **common_env,
                "POWERTOOLS_SERVICE_NAME": "stooq-zip-extractor",
                "STOOQ_ZIP_KEY": "stooq/data.zip",
                "STOOQ_EXTRACTED_PREFIX": "stooq-extracted/",
                "STOOQ_ZIP_EXTRACT_MAX_ENTRIES": "1000",
                "STOOQ_BACKFILL_MAX_FILES": "5",
                "STOCK_COLLECTOR_FUNCTION_NAME": stock_collector_function_name,
            },
            description="Extracts uploaded Stooq zip files to S3 for one-time backfill",
        )

        self.stock_gap_scanner_fn = _lambda.Function(
            self,
            "StockGapScannerFunction",
            function_name=resource_name(
                deployment_stage,
                "stockara-stock-gap-scanner",
                "stock-gap-scanner",
            ),
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.collectors.stock_gap_scanner.handler",
            code=backend_code,
            memory_size=256,
            timeout=Duration.minutes(5),
            role=batch_role,
            environment={
                **common_env,
                "POWERTOOLS_SERVICE_NAME": "stock-gap-scanner",
                "STOCK_GAP_SCAN_LOOKBACK_DAYS": "90",
                "STOCK_GAP_SCAN_MAX_TASKS": "250",
                "STOCK_GAP_TASK_MAX_RANGE_DAYS": "14",
            },
            description="Scans recent OHLCV history for gaps and queues price backfill tasks",
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
            handler="src.scripts.seed_watchlist_handler.handler",
            code=backend_code,
            memory_size=256,
            timeout=Duration.minutes(3),
            role=batch_role,
            environment={**common_env, "POWERTOOLS_SERVICE_NAME": "watchlist-seed"},
            description="Seeds and syncs the Phase 1 watchlist static metadata",
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
                **news_provider_env,
                "OPENAI_NEWS_MODEL": "gpt-5.4-mini",
                "ALPHA_VANTAGE_NEWS_MAX_TICKERS": "25",
                "NEWS_POLL_INTERVAL_MINUTES": "480",
                "POWERTOOLS_SERVICE_NAME": "news-collector",
            },
            description="Collects and summarizes news for Phase 1 signals",
        )

        self.evidence_collector_fn = _lambda.Function(
            self,
            "EvidenceCollectorFunction",
            function_name=resource_name(
                deployment_stage,
                "stockara-evidence-collector",
                "evidence-collector",
            ),
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.collectors.evidence_collector.handler",
            code=backend_code,
            memory_size=256,
            timeout=Duration.minutes(10),
            role=batch_role,
            environment={
                **common_env,
                **news_provider_env,
                "POWERTOOLS_SERVICE_NAME": "evidence-collector",
                "STOCKARA_SEC_USER_AGENT": (
                    f"Stockara/{deployment_stage} evidence collector "
                    "https://stockara.local"
                ),
                "EVIDENCE_SEC_FILING_LOOKBACK_DAYS": "45",
                "EVIDENCE_ANALYST_LOOKBACK_DAYS": "45",
                "EVIDENCE_SECTOR_LOOKBACK_DAYS": "7",
                "EVIDENCE_MACRO_LOOKBACK_DAYS": "7",
            },
            description="Collects source-backed evidence signals for Phase 1 scoring",
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
                "FINNHUB_KEY_SECRET_NAME": finnhub_key_secret_name,
                "ALPHA_VANTAGE_API_KEY_SECRET_NAME": alpha_vantage_key_secret_name,
                "POWERTOOLS_SERVICE_NAME": "earnings-collector",
                "EARNINGS_CALENDAR_LOOKBACK_DAYS": "1825",
                "EARNINGS_CALENDAR_LOOKAHEAD_DAYS": "120",
                "EARNINGS_CALENDAR_YFINANCE_LIMIT": "32",
                "EARNINGS_ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS": "1.25",
                "EARNINGS_ALPHA_VANTAGE_MAX_CALLS_PER_INVOCATION": "20",
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
                "FINNHUB_KEY_SECRET_NAME": finnhub_key_secret_name,
                "ALPHA_VANTAGE_API_KEY_SECRET_NAME": alpha_vantage_key_secret_name,
                "POWERTOOLS_SERVICE_NAME": "dividend-collector",
                "DIVIDEND_CALENDAR_LOOKBACK_DAYS": "1825",
                "DIVIDEND_CALENDAR_LOOKAHEAD_DAYS": "120",
                "DIVIDEND_CALENDAR_HISTORY_LIMIT": "80",
                "DIVIDEND_ALPHA_VANTAGE_REQUEST_INTERVAL_SECONDS": "1.25",
                "DIVIDEND_ALPHA_VANTAGE_MAX_CALLS_PER_INVOCATION": "20",
            },
            description="Collects dividend calendar events and historical reactions",
        )

        self.collection_distributor_fn = _lambda.Function(
            self,
            "CollectionDistributorFunction",
            function_name=resource_name(
                deployment_stage,
                "stockara-collection-distributor",
                "collection-distributor",
            ),
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.collectors.collection_distributor.handler",
            code=backend_code,
            memory_size=256,
            timeout=Duration.minutes(5),
            role=batch_role,
            environment={
                **common_env,
                "POWERTOOLS_SERVICE_NAME": "collection-distributor",
                "PRICE_COLLECTOR_FUNCTION_NAME": self.stock_collector_fn.function_name,
                "NEWS_COLLECTOR_FUNCTION_NAME": self.news_collector_fn.function_name,
                "EARNINGS_COLLECTOR_FUNCTION_NAME": self.earnings_collector_fn.function_name,
                "DIVIDEND_COLLECTOR_FUNCTION_NAME": self.dividend_collector_fn.function_name,
                "COLLECTION_PRICE_TASK_CHUNK_SIZE": "10",
                "COLLECTION_NEWS_TASK_CHUNK_SIZE": "50",
                "COLLECTION_CALENDAR_TASK_CHUNK_SIZE": "10",
                "COLLECTION_MAX_TASKS_PER_RUN": "4",
                "COLLECTION_ANALYSIS_HOUR_UTC": "22",
            },
            description="Creates the daily S3 manifest for bounded collector work",
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
        data_table.grant_read_write_data(self.evidence_collector_fn)
        data_table.grant_read_write_data(self.earnings_collector_fn)
        data_table.grant_read_write_data(self.dividend_collector_fn)
        data_table.grant_read_data(self.collection_distributor_fn)
        data_table.grant_read_data(self.stock_gap_scanner_fn)
        data_table.grant_read_write_data(self.ai_analyzer_fn)
        data_table.grant_read_write_data(self.watchlist_seed_fn)
        data_table.grant_read_data(self.api_handler_fn)
        artifact_bucket.grant_read_write(self.stock_collector_fn)
        artifact_bucket.grant_read_write(self.stooq_zip_extractor_fn)
        artifact_bucket.grant_read_write(self.collection_distributor_fn)
        artifact_bucket.grant_read_write(self.stock_gap_scanner_fn)
        artifact_bucket.grant_read_write(self.ai_analyzer_fn)
        batch_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=["*"],
            )
        )
        openai_api_key_secret.grant_read(self.news_collector_fn)
        openai_api_key_secret.grant_read(self.ai_analyzer_fn)
        newsapi_key_secret.grant_read(self.news_collector_fn)
        finnhub_key_secret.grant_read(self.news_collector_fn)
        finnhub_key_secret.grant_read(self.evidence_collector_fn)
        finnhub_key_secret.grant_read(self.earnings_collector_fn)
        finnhub_key_secret.grant_read(self.dividend_collector_fn)
        alpha_vantage_key_secret.grant_read(self.news_collector_fn)
        alpha_vantage_key_secret.grant_read(self.stock_collector_fn)
        alpha_vantage_key_secret.grant_read(self.earnings_collector_fn)
        alpha_vantage_key_secret.grant_read(self.dividend_collector_fn)

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
                "SeedHash": _file_sha256(BACKEND_WATCHLIST_SEED_PATH),
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

        self.daily_workflow = self._create_daily_workflow(deployment_stage)
        self.daily_workflow_schedule = self._create_daily_workflow_schedule(
            deployment_stage
        )

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
                    event=events.RuleTargetInput.from_object({"max_tickers": 10}),
                )
            ],
        )
        stock_collection_rule.node.add_dependency(self.watchlist_seed)

        collection_manifest_rule = events.Rule(
            self,
            "CollectionDistributorSchedule",
            rule_name=resource_name(
                deployment_stage,
                "stockara-collection-distributor",
                "collection-distributor",
            ),
            description="Refreshes the daily collection manifest and dispatches one task",
            schedule=events.Schedule.rate(Duration.minutes(5)),
            targets=[targets.LambdaFunction(self.collection_distributor_fn)],
        )
        collection_manifest_rule.node.add_dependency(self.watchlist_seed)

        stock_gap_scan_rule = events.Rule(
            self,
            "StockGapScanSchedule",
            rule_name=resource_name(
                deployment_stage,
                "stockara-stock-gap-scan",
                "stock-gap-scan",
            ),
            description="Scans for missing recent OHLCV rows and enqueues backfill tasks",
            schedule=events.Schedule.cron(minute="15", hour="23"),
            targets=[targets.LambdaFunction(self.stock_gap_scanner_fn)],
        )
        stock_gap_scan_rule.node.add_dependency(self.watchlist_seed)

        events.Rule(
            self,
            "NewsCollectionSchedule",
            rule_name=resource_name(
                deployment_stage,
                "stockara-news-collection",
                "news-collection",
            ),
            description=(
                "Triggers quota-conscious news collection for daily catalyst freshness"
            ),
            schedule=events.Schedule.cron(
                minute="30", hour="6,14,21", day="*", month="*", year="*"
            ),
            targets=[targets.LambdaFunction(self.news_collector_fn)],
        )

        events.Rule(
            self,
            "EvidenceCollectionSchedule",
            rule_name=resource_name(
                deployment_stage,
                "stockara-evidence-collection",
                "evidence-collection",
            ),
            description="Triggers SEC filing and analyst-action evidence collection daily",
            schedule=events.Schedule.cron(
                minute="45", hour="20", day="*", month="*", year="*"
            ),
            targets=[
                targets.LambdaFunction(
                    self.evidence_collector_fn,
                    event=events.RuleTargetInput.from_object({"max_tickers": 100}),
                )
            ],
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
            description=(
                "Disabled legacy publisher schedule retained as rollback path; "
                "daily publication is owned by the Step Functions workflow"
            ),
            enabled=False,
            schedule=events.Schedule.rate(Duration.minutes(5)),
            targets=[
                targets.LambdaFunction(
                    self.ai_analyzer_fn,
                    event=events.RuleTargetInput.from_object({"mode": "daily"}),
                )
            ],
        )

        CfnOutput(
            self,
            "ApiUrl",
            value=self.api.url,
            description="Base URL for the public health API",
        )
        CfnOutput(
            self,
            "StooqZipExtractorFunctionName",
            value=self.stooq_zip_extractor_fn.function_name,
            description="Lambda function for extracting uploaded Stooq zip data",
        )
        CfnOutput(
            self,
            "EvidenceCollectorFunctionName",
            value=self.evidence_collector_fn.function_name,
            description="Lambda function for collecting SEC filings and analyst actions",
        )
        CfnOutput(
            self,
            "DailyWorkflowStateMachineName",
            value=self.daily_workflow.state_machine_name,
            description="Manual/shadow Step Functions workflow for the daily pipeline",
        )

    def _create_daily_workflow(self, deployment_stage: str) -> sfn.StateMachine:
        sync_static_metadata = self._lambda_workflow_step(
            "SyncStaticMetadata",
            self.watchlist_seed_fn,
            {
                "mode": "sync_static_metadata",
                "tickers": [],
                "provider_budget": {},
                "dry_run": False,
            },
            "$.sync_static_metadata",
        )
        create_or_refresh_manifest = self._lambda_workflow_step(
            "CreateOrRefreshManifest",
            self.collection_distributor_fn,
            {
                "workflow": "daily_step_functions",
                "stage": "create_or_refresh_manifest",
                "max_tasks_per_run": 80,
            },
            "$.manifest",
        )
        dispatch_manifest_tasks = self._lambda_workflow_step(
            "DispatchManifestTasks",
            self.collection_distributor_fn,
            {
                "workflow": "daily_step_functions",
                "stage": "dispatch_manifest_tasks",
                "max_tasks_per_run": 80,
            },
            "$.manifest_dispatch",
        )
        wait_for_manifest_dispatch = sfn.Wait(
            self,
            "WaitForManifestDispatch",
            time=sfn.WaitTime.duration(Duration.minutes(5)),
            comment="Wait for leased manifest worker Lambdas before dispatching more chunks.",
        )
        wait_for_manifest_dispatch.next(dispatch_manifest_tasks)
        collect_prices = self._lambda_workflow_step(
            "CollectPrices",
            self.stock_collector_fn,
            {"max_tickers": 50, "workflow": "daily_step_functions"},
            "$.prices",
        )
        repair_price_gaps = self._lambda_workflow_step(
            "RepairPriceGaps",
            self.stock_collector_fn,
            {
                "mode": "repair_price_gaps",
                "max_tickers": 50,
                "workflow": "daily_step_functions",
            },
            "$.price_gap_repair",
        )
        collect_news = self._lambda_workflow_step(
            "CollectNews",
            self.news_collector_fn,
            {
                "mode": "repair_news",
                "max_tickers": 75,
                "provider_budget": {
                    "newsapi": 20,
                    "finnhub": 50,
                    "alpha_vantage": 10,
                },
                "workflow": "daily_step_functions",
            },
            "$.news",
        )
        collect_earnings = self._lambda_workflow_step(
            "CollectEarnings",
            self.earnings_collector_fn,
            {
                "mode": "repair_calendars",
                "max_tickers": 50,
                "provider_budget": {"alpha_vantage": 20, "finnhub": 50},
                "workflow": "daily_step_functions",
            },
            "$.earnings",
        )
        collect_dividends = self._lambda_workflow_step(
            "CollectDividends",
            self.dividend_collector_fn,
            {
                "mode": "repair_calendars",
                "max_tickers": 50,
                "provider_budget": {"alpha_vantage": 20, "finnhub": 50},
                "workflow": "daily_step_functions",
            },
            "$.dividends",
        )
        collect_evidence = self._lambda_workflow_step(
            "CollectEvidence",
            self.evidence_collector_fn,
            {
                "mode": "repair_evidence",
                "max_tickers": 100,
                "provider_budget": {"sec": 100, "finnhub": 100, "yfinance": 100},
                "workflow": "daily_step_functions",
            },
            "$.evidence",
        )
        analyze_and_publish = self._lambda_workflow_step(
            "AnalyzeAndPublish",
            self.ai_analyzer_fn,
            {"mode": "daily", "workflow": "daily_step_functions"},
            "$.analysis",
        )
        publish_workflow_status = sfn_tasks.LambdaInvoke(
            self,
            "PublishWorkflowStatus",
            lambda_function=self.ai_analyzer_fn,
            payload=sfn.TaskInput.from_object(
                {
                    "mode": "publish_workflow_status",
                    "workflow": "daily_step_functions",
                    "workflow_result": sfn.JsonPath.entire_payload,
                    "execution_id": sfn.JsonPath.execution_id,
                    "execution_name": sfn.JsonPath.execution_name,
                    "execution_started_at": sfn.JsonPath.execution_start_time,
                }
            ),
            result_path="$.workflow_status",
        )
        self._add_workflow_retry_and_catch(
            publish_workflow_status,
            "PublishWorkflowStatus",
        )

        collect_calendars_and_evidence = sfn.Parallel(
            self,
            "CollectCalendarsAndEvidence",
            result_path="$.optional_evidence",
        )
        collect_calendars_and_evidence.branch(collect_earnings)
        collect_calendars_and_evidence.branch(collect_dividends)
        collect_calendars_and_evidence.branch(collect_evidence)
        collect_calendars_and_evidence.add_catch(
            self._workflow_fail_state("CollectCalendarsAndEvidenceFailed"),
            errors=["States.ALL"],
            result_path="$.workflow_error",
        )
        wait_for_analysis_window = sfn.Wait(
            self,
            "WaitForAnalysisWindow",
            time=sfn.WaitTime.duration(Duration.minutes(60)),
            comment="Give collectors time to finish before the 22:00 UTC analysis gate.",
        )
        decide_publication_readiness = self._workflow_readiness_decision()
        publication_readiness_chain = (
            decide_publication_readiness.afterwards().next(publish_workflow_status)
        )
        manifest_dispatch_decision = (
            sfn.Choice(
                self,
                "DecideManifestDispatchReadiness",
                comment=(
                    "Continue once no pending, leased, or running manifest worker "
                    "tasks remain; retry-wait tasks become degraded data quality."
                ),
            )
            .when(
                sfn.Condition.boolean_equals(
                    "$.manifest_dispatch.Payload.body.dispatch_ready_for_analysis",
                    True,
                ),
                collect_prices,
            )
            .otherwise(wait_for_manifest_dispatch)
        )

        definition = (
            sync_static_metadata
            .next(create_or_refresh_manifest)
            .next(dispatch_manifest_tasks)
            .next(manifest_dispatch_decision)
        )
        (
            collect_prices.next(repair_price_gaps)
            .next(collect_news)
            .next(collect_calendars_and_evidence)
            .next(wait_for_analysis_window)
            .next(analyze_and_publish)
            .next(publication_readiness_chain)
        )

        return sfn.StateMachine(
            self,
            "DailyPipelineWorkflow",
            state_machine_name=resource_name(
                deployment_stage,
                "stockara-daily-pipeline",
                "daily-pipeline",
            ),
            state_machine_type=sfn.StateMachineType.STANDARD,
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.hours(3),
            comment=(
                "Manual/shadow daily Stockara workflow coordinating coarse "
                "collector, repair, and publication Lambdas."
            ),
        )

    def _create_daily_workflow_schedule(self, deployment_stage: str) -> events.Rule:
        return events.Rule(
            self,
            "DailyPipelineWorkflowSchedule",
            rule_name=resource_name(
                deployment_stage,
                "stockara-daily-pipeline",
                "daily-pipeline",
            ),
            description=(
                "Starts the shadow Step Functions daily workflow before the "
                "22:00 UTC analysis target"
            ),
            schedule=events.Schedule.cron(
                minute="5", hour="21", day="*", month="*", year="*"
            ),
            targets=[
                targets.SfnStateMachine(
                    self.daily_workflow,
                    input=events.RuleTargetInput.from_object(
                        {"workflow": "scheduled_daily_step_functions"}
                    ),
                )
            ],
        )

    def _workflow_readiness_decision(self) -> sfn.Choice:
        publish = sfn.Pass(
            self,
            "Publish",
            result=sfn.Result.from_object({"decision": "publish"}),
            result_path="$.workflow_decision",
        )
        publish_degraded = sfn.Pass(
            self,
            "PublishDegraded",
            result=sfn.Result.from_object({"decision": "publish_degraded"}),
            result_path="$.workflow_decision",
        )
        wait_or_repair = sfn.Pass(
            self,
            "WaitOrRepair",
            result=sfn.Result.from_object({"decision": "wait_or_repair"}),
            result_path="$.workflow_decision",
        )
        blocked = sfn.Pass(
            self,
            "Blocked",
            result=sfn.Result.from_object({"decision": "blocked"}),
            result_path="$.workflow_decision",
        )

        return (
            sfn.Choice(
                self,
                "DecidePublicationReadiness",
                comment=(
                    "Classify the daily run after analyzer publication into the "
                    "operator-facing readiness outcome."
                ),
            )
            .when(
                sfn.Condition.number_equals("$.analysis.Payload.statusCode", 202),
                wait_or_repair,
            )
            .when(
                sfn.Condition.string_equals(
                    "$.analysis.Payload.body.workflow_decision",
                    "publish",
                ),
                publish,
            )
            .when(
                sfn.Condition.string_equals(
                    "$.analysis.Payload.body.workflow_decision",
                    "publish_degraded",
                ),
                publish_degraded,
            )
            .when(
                sfn.Condition.string_equals(
                    "$.analysis.Payload.body.workflow_decision",
                    "blocked",
                ),
                blocked,
            )
            .when(
                sfn.Condition.string_equals(
                    "$.analysis.Payload.body.stage",
                    "already_published",
                ),
                publish,
            )
            .when(
                sfn.Condition.string_equals(
                    "$.analysis.Payload.body.stage",
                    "scored",
                ),
                wait_or_repair,
            )
            .when(
                sfn.Condition.string_equals(
                    "$.analysis.Payload.body.stage",
                    "analyzed_batch",
                ),
                wait_or_repair,
            )
            .when(
                sfn.Condition.and_(
                    sfn.Condition.is_string("$.analysis.Payload.body"),
                    sfn.Condition.string_matches(
                        "$.analysis.Payload.body",
                        "Publication suppressed:*",
                    ),
                ),
                blocked,
            )
            .otherwise(blocked)
        )

    def _lambda_workflow_step(
        self,
        construct_id: str,
        lambda_function: _lambda.IFunction,
        payload: dict,
        result_path: str,
    ) -> sfn_tasks.LambdaInvoke:
        step = sfn_tasks.LambdaInvoke(
            self,
            construct_id,
            lambda_function=lambda_function,
            payload=sfn.TaskInput.from_object(payload),
            result_path=result_path,
        )
        self._add_workflow_retry_and_catch(step, construct_id)
        return step

    def _add_workflow_retry_and_catch(
        self,
        step: sfn_tasks.LambdaInvoke,
        construct_id: str,
    ) -> None:
        step.add_retry(
            errors=[
                "Lambda.ServiceException",
                "Lambda.AWSLambdaException",
                "Lambda.SdkClientException",
                "Lambda.TooManyRequestsException",
                "States.Timeout",
                "ProviderThrottled",
                "ProviderRateLimited",
                "ProviderQuotaExceeded",
                "CollectionManifestIncomplete",
                "OpenAITransientError",
                "ArtifactPublishFailed",
            ],
            interval=Duration.seconds(2),
            max_attempts=3,
            backoff_rate=2,
        )
        step.add_catch(
            self._workflow_fail_state(f"{construct_id}Failed"),
            errors=["States.ALL"],
            result_path="$.workflow_error",
        )

    def _workflow_fail_state(self, construct_id: str) -> sfn.Fail:
        return sfn.Fail(
            self,
            construct_id,
            error="StockaraDailyWorkflowFailed",
            cause=f"{construct_id} failed after retry/catch handling.",
        )
