"""CDK stack for API Gateway, Lambda functions, and EventBridge rules."""

import os

from aws_cdk import (
    Duration,
    Stack,
    aws_apigateway as apigw,
    aws_cognito as cognito,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


BACKEND_ASSET_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend")
)


class ApiStack(Stack):
    """Defines the API and compute resources for the Stock Monitoring System.

    Includes Lambda functions, API Gateway with Cognito authorizer,
    and EventBridge scheduled rules for batch processing.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        data_table: dynamodb.ITable,
        user_pool: cognito.IUserPool,
        user_pool_client: cognito.IUserPoolClient,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.user_pool = user_pool

        # ─── IAM Roles (least-privilege) ─────────────────────────────────

        # Base Lambda execution role policy
        lambda_base_policy = iam.ManagedPolicy.from_aws_managed_policy_name(
            "service-role/AWSLambdaBasicExecutionRole"
        )

        # Stock Collector role - needs DynamoDB access, CloudWatch metrics
        stock_collector_role = iam.Role(
            self,
            "StockCollectorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[lambda_base_policy],
            description="Role for stock data collector Lambda",
        )
        stock_collector_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "cloudwatch:PutMetricData",
                    "secretsmanager:GetSecretValue",
                ],
                resources=["*"],
            )
        )

        # News Collector role - needs DynamoDB access, Secrets Manager for API keys
        news_collector_role = iam.Role(
            self,
            "NewsCollectorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[lambda_base_policy],
            description="Role for news collector Lambda",
        )
        news_collector_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "cloudwatch:PutMetricData",
                    "secretsmanager:GetSecretValue",
                ],
                resources=["*"],
            )
        )

        # AI Analyzer role - needs DynamoDB access, Secrets Manager for OpenAI key
        ai_analyzer_role = iam.Role(
            self,
            "AiAnalyzerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[lambda_base_policy],
            description="Role for AI analyzer Lambda",
        )
        ai_analyzer_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "cloudwatch:PutMetricData",
                    "secretsmanager:GetSecretValue",
                ],
                resources=["*"],
            )
        )

        # API Handler role - needs DynamoDB, KMS for portfolio encryption, Cognito
        api_handler_role = iam.Role(
            self,
            "ApiHandlerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[lambda_base_policy],
            description="Role for API handler Lambda",
        )
        api_handler_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kms:Encrypt",
                    "kms:Decrypt",
                    "kms:GenerateDataKey",
                    "secretsmanager:GetSecretValue",
                    "cognito-idp:AdminGetUser",
                    "cognito-idp:AdminInitiateAuth",
                    "cognito-idp:SignUp",
                ],
                resources=["*"],
            )
        )

        # ─── Lambda Functions ────────────────────────────────────────────

        # Common environment variables for all Lambdas
        common_env = {
            "POWERTOOLS_SERVICE_NAME": "stock-monitoring",
            "LOG_LEVEL": "INFO",
            "STOCKARA_TABLE_NAME": data_table.table_name,
        }

        # Stock Collector Lambda - 512MB, 15 min timeout
        self.stock_collector_fn = _lambda.Function(
            self,
            "StockCollectorFunction",
            function_name="stock-monitoring-stock-collector",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.collectors.stock_collector.handler",
            code=_lambda.Code.from_asset(BACKEND_ASSET_PATH),
            memory_size=512,
            timeout=Duration.minutes(15),
            role=stock_collector_role,
            environment={
                **common_env,
                "POWERTOOLS_SERVICE_NAME": "stock-collector",
            },
            description="Collects daily stock OHLCV data from yfinance/Alpha Vantage",
        )

        # News Collector Lambda - 256MB, 5 min timeout
        self.news_collector_fn = _lambda.Function(
            self,
            "NewsCollectorFunction",
            function_name="stock-monitoring-news-collector",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.collectors.news_collector.handler",
            code=_lambda.Code.from_asset(BACKEND_ASSET_PATH),
            memory_size=256,
            timeout=Duration.minutes(5),
            role=news_collector_role,
            environment={
                **common_env,
                "POWERTOOLS_SERVICE_NAME": "news-collector",
            },
            description="Collects and summarizes stock-related news articles",
        )

        # AI Analyzer Lambda - 1024MB, 15 min timeout
        self.ai_analyzer_fn = _lambda.Function(
            self,
            "AiAnalyzerFunction",
            function_name="stock-monitoring-ai-analyzer",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.analysis.ai_analyzer.handler",
            code=_lambda.Code.from_asset(BACKEND_ASSET_PATH),
            memory_size=1024,
            timeout=Duration.minutes(15),
            role=ai_analyzer_role,
            environment={
                **common_env,
                "POWERTOOLS_SERVICE_NAME": "ai-analyzer",
            },
            description="Generates AI-driven BUY/HOLD/SELL recommendations",
        )

        # API Handler Lambda - 512MB, 30s timeout
        self.api_handler_fn = _lambda.Function(
            self,
            "ApiHandlerFunction",
            function_name="stock-monitoring-api-handler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.api.handler.handler",
            code=_lambda.Code.from_asset(BACKEND_ASSET_PATH),
            memory_size=512,
            timeout=Duration.seconds(30),
            role=api_handler_role,
            environment={
                **common_env,
                "POWERTOOLS_SERVICE_NAME": "api-handler",
                "USER_POOL_ID": self.user_pool.user_pool_id,
                "USER_POOL_CLIENT_ID": user_pool_client.user_pool_client_id,
                "COGNITO_USER_POOL_ID": self.user_pool.user_pool_id,
                "COGNITO_CLIENT_ID": user_pool_client.user_pool_client_id,
            },
            description="Handles REST API requests via API Gateway",
        )

        data_table.grant_read_write_data(self.stock_collector_fn)
        data_table.grant_read_write_data(self.news_collector_fn)
        data_table.grant_read_write_data(self.ai_analyzer_fn)
        data_table.grant_read_write_data(self.api_handler_fn)

        # ─── API Gateway ─────────────────────────────────────────────────

        # Cognito authorizer for protected routes
        cognito_authorizer = apigw.CognitoUserPoolsAuthorizer(
            self,
            "StockMonitoringAuthorizer",
            authorizer_name="stock-monitoring-cognito-authorizer",
            cognito_user_pools=[self.user_pool],
        )

        # REST API
        self.api = apigw.RestApi(
            self,
            "StockMonitoringApi",
            rest_api_name="Stock Monitoring API",
            description="REST API for the Stock Monitoring and Analysis System",
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                throttling_rate_limit=100,
                throttling_burst_limit=200,
            ),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=apigw.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization"],
            ),
        )

        # Lambda integration for the API handler
        api_integration = apigw.LambdaIntegration(
            self.api_handler_fn,
            proxy=True,
        )

        # Public routes (no auth required)
        api_resource = self.api.root.add_resource("api")

        # Health endpoint - public
        health_resource = api_resource.add_resource("health")
        health_resource.add_method("GET", api_integration)

        # Auth endpoints - public
        auth_resource = api_resource.add_resource("auth")
        auth_register = auth_resource.add_resource("register")
        auth_register.add_method("POST", api_integration)
        auth_login = auth_resource.add_resource("login")
        auth_login.add_method("POST", api_integration)

        # Protected routes (Cognito auth required)
        protected_method_kwargs = {
            "authorizer": cognito_authorizer,
            "authorization_type": apigw.AuthorizationType.COGNITO,
        }

        # Portfolio endpoints
        portfolio_resource = api_resource.add_resource("portfolio")
        portfolio_resource.add_method(
            "GET", api_integration, **protected_method_kwargs
        )
        portfolio_stocks = portfolio_resource.add_resource("stocks")
        portfolio_stocks.add_method(
            "PUT", api_integration, **protected_method_kwargs
        )
        portfolio_stock_ticker = portfolio_stocks.add_resource("{ticker}")
        portfolio_stock_ticker.add_method(
            "DELETE", api_integration, **protected_method_kwargs
        )

        # Suggestions endpoint
        suggestions_resource = api_resource.add_resource("suggestions")
        suggestions_resource.add_method(
            "GET", api_integration, **protected_method_kwargs
        )

        # Stocks endpoints
        stocks_resource = api_resource.add_resource("stocks")
        stocks_resource.add_method(
            "GET", api_integration, **protected_method_kwargs
        )
        stock_ticker_resource = stocks_resource.add_resource("{ticker}")
        stock_analysis = stock_ticker_resource.add_resource("analysis")
        stock_analysis.add_method(
            "GET", api_integration, **protected_method_kwargs
        )

        # Preferences endpoints
        preferences_resource = api_resource.add_resource("preferences")
        preferences_resource.add_method(
            "GET", api_integration, **protected_method_kwargs
        )
        preferences_resource.add_method(
            "PUT", api_integration, **protected_method_kwargs
        )

        # ─── EventBridge Scheduled Rules ─────────────────────────────────

        # Stock collection: daily at 21:00 UTC (after US market close)
        events.Rule(
            self,
            "StockCollectionSchedule",
            rule_name="stock-monitoring-stock-collection",
            description="Triggers stock data collection daily at 21:00 UTC",
            schedule=events.Schedule.cron(
                minute="0", hour="21", day="*", month="*", year="*"
            ),
            targets=[targets.LambdaFunction(self.stock_collector_fn)],
        )

        # News collection: every 15 minutes
        events.Rule(
            self,
            "NewsCollectionSchedule",
            rule_name="stock-monitoring-news-collection",
            description="Triggers news collection every 15 minutes",
            schedule=events.Schedule.rate(Duration.minutes(15)),
            targets=[targets.LambdaFunction(self.news_collector_fn)],
        )

        # AI analysis: daily at 22:00 UTC (1 hour after stock collection)
        events.Rule(
            self,
            "AiAnalysisSchedule",
            rule_name="stock-monitoring-ai-analysis",
            description="Triggers AI analysis daily at 22:00 UTC",
            schedule=events.Schedule.cron(
                minute="0", hour="22", day="*", month="*", year="*"
            ),
            targets=[targets.LambdaFunction(self.ai_analyzer_fn)],
        )
