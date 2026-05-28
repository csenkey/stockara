"""CDK stack for monitoring and observability resources."""

from aws_cdk import (
    Duration,
    Stack,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_logs as logs,
    aws_sns as sns,
)
from constructs import Construct


class MonitoringStack(Stack):
    """Defines the monitoring infrastructure for the Stock Monitoring System.

    Includes CloudWatch alarms, log groups, dashboards, and SNS notifications.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- SNS Topic for Alert Notifications ---
        self.alerts_topic = sns.Topic(
            self,
            "AlertsTopic",
            topic_name="stock-monitoring-alerts",
            display_name="Stock Monitoring System Alerts",
        )

        # --- CloudWatch Log Groups with 30-day retention ---
        self.stock_collector_log_group = logs.LogGroup(
            self,
            "StockCollectorLogGroup",
            log_group_name="/aws/lambda/stock-collector",
            retention=logs.RetentionDays.ONE_MONTH,
        )

        self.news_collector_log_group = logs.LogGroup(
            self,
            "NewsCollectorLogGroup",
            log_group_name="/aws/lambda/news-collector",
            retention=logs.RetentionDays.ONE_MONTH,
        )

        self.ai_analyzer_log_group = logs.LogGroup(
            self,
            "AiAnalyzerLogGroup",
            log_group_name="/aws/lambda/ai-analyzer",
            retention=logs.RetentionDays.ONE_MONTH,
        )

        self.api_handler_log_group = logs.LogGroup(
            self,
            "ApiHandlerLogGroup",
            log_group_name="/aws/lambda/api-handler",
            retention=logs.RetentionDays.ONE_MONTH,
        )

        # --- Custom Metrics ---
        # Namespace for all custom metrics
        metrics_namespace = "StockMonitoring"

        self.stocks_collected_metric = cloudwatch.Metric(
            namespace=metrics_namespace,
            metric_name="stocks_collected",
            statistic="Sum",
            period=Duration.minutes(5),
        )

        self.news_articles_processed_metric = cloudwatch.Metric(
            namespace=metrics_namespace,
            metric_name="news_articles_processed",
            statistic="Sum",
            period=Duration.minutes(5),
        )

        self.analysis_generated_metric = cloudwatch.Metric(
            namespace=metrics_namespace,
            metric_name="analysis_generated",
            statistic="Sum",
            period=Duration.minutes(5),
        )

        # --- Alarms ---

        # Error rate alarm: API Lambda errors > 5% in 5-minute window
        api_errors_metric = cloudwatch.Metric(
            namespace="AWS/Lambda",
            metric_name="Errors",
            dimensions_map={"FunctionName": "api-handler"},
            statistic="Sum",
            period=Duration.minutes(5),
        )

        api_invocations_metric = cloudwatch.Metric(
            namespace="AWS/Lambda",
            metric_name="Invocations",
            dimensions_map={"FunctionName": "api-handler"},
            statistic="Sum",
            period=Duration.minutes(5),
        )

        # Use math expression for error rate percentage
        self.api_error_rate_alarm = cloudwatch.Alarm(
            self,
            "ApiErrorRateAlarm",
            alarm_name="stock-monitoring-api-error-rate",
            alarm_description="API Lambda error rate exceeds 5% in a 5-minute window",
            metric=cloudwatch.MathExpression(
                expression="(errors / invocations) * 100",
                using_metrics={
                    "errors": api_errors_metric,
                    "invocations": api_invocations_metric,
                },
                period=Duration.minutes(5),
            ),
            threshold=5,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.api_error_rate_alarm.add_alarm_action(
            cw_actions.SnsAction(self.alerts_topic)
        )

        # Batch job failure alarm: stock_collector Lambda errors >= 1
        self.stock_collector_failure_alarm = cloudwatch.Alarm(
            self,
            "StockCollectorFailureAlarm",
            alarm_name="stock-monitoring-stock-collector-failure",
            alarm_description="Stock collector Lambda function has errors",
            metric=cloudwatch.Metric(
                namespace="AWS/Lambda",
                metric_name="Errors",
                dimensions_map={"FunctionName": "stock-collector"},
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.stock_collector_failure_alarm.add_alarm_action(
            cw_actions.SnsAction(self.alerts_topic)
        )

        # Batch job failure alarm: ai_analyzer Lambda errors >= 1
        self.ai_analyzer_failure_alarm = cloudwatch.Alarm(
            self,
            "AiAnalyzerFailureAlarm",
            alarm_name="stock-monitoring-ai-analyzer-failure",
            alarm_description="AI analyzer Lambda function has errors",
            metric=cloudwatch.Metric(
                namespace="AWS/Lambda",
                metric_name="Errors",
                dimensions_map={"FunctionName": "ai-analyzer"},
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        self.ai_analyzer_failure_alarm.add_alarm_action(
            cw_actions.SnsAction(self.alerts_topic)
        )

        # --- CloudWatch Dashboard ---
        self.dashboard = cloudwatch.Dashboard(
            self,
            "StockMonitoringDashboard",
            dashboard_name="StockMonitoringDashboard",
        )

        # Row 1: Custom business metrics
        self.dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Stocks Collected",
                left=[self.stocks_collected_metric],
                width=8,
            ),
            cloudwatch.GraphWidget(
                title="News Articles Processed",
                left=[self.news_articles_processed_metric],
                width=8,
            ),
            cloudwatch.GraphWidget(
                title="Analysis Generated",
                left=[self.analysis_generated_metric],
                width=8,
            ),
        )

        # Row 2: Lambda error and invocation metrics
        self.dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="API Handler - Errors & Invocations",
                left=[api_invocations_metric],
                right=[api_errors_metric],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="Batch Jobs - Errors",
                left=[
                    cloudwatch.Metric(
                        namespace="AWS/Lambda",
                        metric_name="Errors",
                        dimensions_map={"FunctionName": "stock-collector"},
                        statistic="Sum",
                        period=Duration.minutes(5),
                    ),
                    cloudwatch.Metric(
                        namespace="AWS/Lambda",
                        metric_name="Errors",
                        dimensions_map={"FunctionName": "ai-analyzer"},
                        statistic="Sum",
                        period=Duration.minutes(5),
                    ),
                ],
                width=12,
            ),
        )

        # Row 3: Lambda duration metrics
        self.dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="API Response Times",
                left=[
                    cloudwatch.Metric(
                        namespace="AWS/Lambda",
                        metric_name="Duration",
                        dimensions_map={"FunctionName": "api-handler"},
                        statistic="Average",
                        period=Duration.minutes(5),
                    ),
                ],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="Batch Job Durations",
                left=[
                    cloudwatch.Metric(
                        namespace="AWS/Lambda",
                        metric_name="Duration",
                        dimensions_map={"FunctionName": "stock-collector"},
                        statistic="Average",
                        period=Duration.minutes(5),
                    ),
                    cloudwatch.Metric(
                        namespace="AWS/Lambda",
                        metric_name="Duration",
                        dimensions_map={"FunctionName": "ai-analyzer"},
                        statistic="Average",
                        period=Duration.minutes(5),
                    ),
                ],
                width=12,
            ),
        )

        # Row 4: Alarm status
        self.dashboard.add_widgets(
            cloudwatch.AlarmStatusWidget(
                title="Alarm Status",
                alarms=[
                    self.api_error_rate_alarm,
                    self.stock_collector_failure_alarm,
                    self.ai_analyzer_failure_alarm,
                ],
                width=24,
            ),
        )
