"""CloudWatch monitoring for Stockara Phase 1."""

from aws_cdk import (
    Duration,
    Stack,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_logs as logs,
    aws_sns as sns,
)
from constructs import Construct

from .naming import resource_name


class MonitoringStack(Stack):
    """Low-cost alarms and dashboard for Phase 1 scheduled jobs."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        deployment_stage: str = "prod",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.alerts_topic = sns.Topic(
            self,
            "AlertsTopic",
            topic_name=resource_name(deployment_stage, "stockara-alerts", "alerts"),
            display_name="Stockara Phase 1 Alerts",
        )

        function_names = {
            "stock_collector": resource_name(
                deployment_stage, "stockara-stock-collector", "stock-collector"
            ),
            "news_collector": resource_name(
                deployment_stage, "stockara-news-collector", "news-collector"
            ),
            "publisher": resource_name(
                deployment_stage,
                "stockara-phase1-analyzer-publisher",
                "phase1-analyzer-publisher",
            ),
            "health_api": resource_name(
                deployment_stage, "stockara-health-api", "health-api"
            ),
        }

        for logical_name, function_name in function_names.items():
            logs.LogGroup(
                self,
                f"{logical_name}LogGroup",
                log_group_name=f"/aws/lambda/{function_name}",
                retention=logs.RetentionDays.ONE_MONTH,
            )
            alarm = cloudwatch.Alarm(
                self,
                f"{logical_name}FailureAlarm",
                alarm_name=resource_name(
                    deployment_stage,
                    f"stockara-{logical_name}-failure",
                    f"{logical_name}-failure",
                ),
                alarm_description=f"{function_name} has Lambda errors",
                metric=cloudwatch.Metric(
                    namespace="AWS/Lambda",
                    metric_name="Errors",
                    dimensions_map={"FunctionName": function_name},
                    statistic="Sum",
                    period=Duration.minutes(5),
                ),
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            alarm.add_alarm_action(cw_actions.SnsAction(self.alerts_topic))

        self.dashboard = cloudwatch.Dashboard(
            self,
            "Phase1Dashboard",
            dashboard_name=resource_name(
                deployment_stage, "StockaraPhase1Dashboard", "dashboard"
            ),
        )
        self.dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Published picks and alerts",
                left=[
                    cloudwatch.Metric(
                        namespace="StockaraPhase1",
                        metric_name="top_picks_published",
                        statistic="Sum",
                        period=Duration.hours(1),
                    ),
                    cloudwatch.Metric(
                        namespace="StockaraPhase1",
                        metric_name="sell_alerts_published",
                        statistic="Sum",
                        period=Duration.hours(1),
                    ),
                ],
            ),
            cloudwatch.GraphWidget(
                title="Candidate funnel",
                left=[
                    cloudwatch.Metric(
                        namespace="StockaraPhase1",
                        metric_name="candidates_scored",
                        statistic="Sum",
                        period=Duration.hours(1),
                    ),
                    cloudwatch.Metric(
                        namespace="StockaraPhase1",
                        metric_name="ai_candidates_analyzed",
                        statistic="Sum",
                        period=Duration.hours(1),
                    ),
                ],
            ),
        )
