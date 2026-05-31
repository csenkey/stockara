"""CDK stack for Demo Trading Accounts infrastructure."""

from aws_cdk import (
    Duration,
    Stack,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_logs as logs,
)
from constructs import Construct


class DemoTradingStack(Stack):
    """Defines the demo trading infrastructure for simulated account execution.

    Includes a Lambda function triggered daily at 22:30 UTC via EventBridge
    to execute trades for all 100 demo accounts based on AI recommendations.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ─── IAM Role (least-privilege) ──────────────────────────────────

        lambda_base_policy = iam.ManagedPolicy.from_aws_managed_policy_name(
            "service-role/AWSLambdaBasicExecutionRole"
        )

        demo_trade_executor_role = iam.Role(
            self,
            "DemoTradeExecutorRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[lambda_base_policy],
            description="Role for demo trade executor Lambda",
        )

        # Read access to analysis_results and stock_data tables
        # (via RDS/Secrets Manager for DB connection)
        demo_trade_executor_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "secretsmanager:GetSecretValue",
                ],
                resources=["*"],
                sid="ReadSecretsForDBConnection",
            )
        )

        # CloudWatch metrics for observability
        demo_trade_executor_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "cloudwatch:PutMetricData",
                ],
                resources=["*"],
                sid="PublishMetrics",
            )
        )

        # ─── CloudWatch Log Group ────────────────────────────────────────

        self.demo_trade_executor_log_group = logs.LogGroup(
            self,
            "DemoTradeExecutorLogGroup",
            log_group_name="/aws/lambda/demo-trade-executor",
            retention=logs.RetentionDays.ONE_MONTH,
        )

        # ─── Lambda Function ────────────────────────────────────────────

        self.demo_trade_executor_fn = _lambda.Function(
            self,
            "DemoTradeExecutorFunction",
            function_name="stock-monitoring-demo-trade-executor",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="src.services.demo_trade_handler.handler",
            code=_lambda.Code.from_asset("../backend"),
            memory_size=512,
            timeout=Duration.seconds(300),
            role=demo_trade_executor_role,
            environment={
                "POWERTOOLS_SERVICE_NAME": "demo-trade-executor",
                "LOG_LEVEL": "INFO",
            },
            description="Executes daily simulated trades for 100 demo accounts based on AI recommendations",
        )

        # ─── EventBridge Scheduled Rule ──────────────────────────────────

        # Demo trade execution: daily at 22:30 UTC (30 min after AI analysis)
        events.Rule(
            self,
            "DemoTradeExecutionSchedule",
            rule_name="stock-monitoring-demo-trade-execution",
            description="Triggers demo trade execution daily at 22:30 UTC, 30 minutes after AI analysis completes",
            schedule=events.Schedule.cron(
                minute="30", hour="22", day="*", month="*", year="*"
            ),
            targets=[targets.LambdaFunction(self.demo_trade_executor_fn)],
        )
