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
            "earnings_collector": resource_name(
                deployment_stage, "stockara-earnings-collector", "earnings-collector"
            ),
            "dividend_collector": resource_name(
                deployment_stage, "stockara-dividend-collector", "dividend-collector"
            ),
            "collection_distributor": resource_name(
                deployment_stage,
                "stockara-collection-distributor",
                "collection-distributor",
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
                comparison_operator=(
                    cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD
                ),
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            alarm.add_alarm_action(cw_actions.SnsAction(self.alerts_topic))

        threshold_alarms = [
            (
                "ArtifactPublishFailuresAlarm",
                "stockara-artifact-publish-failures",
                "artifact-publish-failures",
                "artifact_publish_failures",
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                "Static artifact publishing failed",
            ),
            (
                "StockCollectionPartialRunsAlarm",
                "stockara-stock-collection-partial",
                "stock-collection-partial",
                "stock_collection_partial_runs",
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                "Stock collection completed with partial ticker coverage",
            ),
            (
                "StockCollectionFailedRunsAlarm",
                "stockara-stock-collection-failed",
                "stock-collection-failed",
                "stock_collection_failed_runs",
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                "Stock collection failed for every selected ticker",
            ),
            (
                "StockCollectionLowCompletenessAlarm",
                "stockara-stock-collection-low-completeness",
                "stock-collection-low-completeness",
                "stock_collection_completeness_percent",
                90,
                cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
                "Stock collection completeness dropped below 90%",
            ),
            (
                "NewsCollectionPartialRunsAlarm",
                "stockara-news-collection-partial",
                "news-collection-partial",
                "news_collection_partial_runs",
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                "News collection completed with failed sources or article failures",
            ),
            (
                "NewsCollectionFailedRunsAlarm",
                "stockara-news-collection-failed",
                "news-collection-failed",
                "news_collection_failed_runs",
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                "News collection failed because all configured sources were unavailable",
            ),
            (
                "NewsCollectionLowCompletenessAlarm",
                "stockara-news-collection-low-completeness",
                "news-collection-low-completeness",
                "news_collection_completeness_percent",
                100,
                cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
                "News collection did not receive data from every configured source",
            ),
        ]

        for (
            construct_id,
            prod_alarm_name,
            staged_alarm_name,
            metric_name,
            threshold,
            comparison_operator,
            description,
        ) in threshold_alarms:
            alarm = cloudwatch.Alarm(
                self,
                construct_id,
                alarm_name=resource_name(
                    deployment_stage, prod_alarm_name, staged_alarm_name
                ),
                alarm_description=description,
                metric=cloudwatch.Metric(
                    namespace=(
                        "StockaraPhase1"
                        if metric_name == "artifact_publish_failures"
                        else "StockMonitoring"
                    ),
                    metric_name=metric_name,
                    statistic=(
                        "Minimum" if metric_name.endswith("_percent") else "Sum"
                    ),
                    period=Duration.hours(1),
                ),
                threshold=threshold,
                evaluation_periods=1,
                comparison_operator=comparison_operator,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            alarm.add_alarm_action(cw_actions.SnsAction(self.alerts_topic))

        manifest_health_alarms = [
            (
                "CollectionManifestStaleAlarm",
                "stockara-collection-manifest-stale",
                "collection-manifest-stale",
                "collection_manifest_age_minutes",
                "Maximum",
                Duration.hours(1),
                90,
                cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                "Daily collection manifest has not been refreshed recently",
            ),
            (
                "CollectionManifestIncompleteAlarm",
                "stockara-collection-manifest-incomplete",
                "collection-manifest-incomplete",
                "collection_manifest_incomplete_tasks",
                "Minimum",
                Duration.hours(4),
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                "Collection manifest has had incomplete tasks for multiple hours",
            ),
            (
                "CollectionManifestRetryExhaustedAlarm",
                "stockara-collection-manifest-retry-exhausted",
                "collection-manifest-retry-exhausted",
                "collection_manifest_retry_exhausted_tasks",
                "Maximum",
                Duration.hours(1),
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                "One or more collection tasks exhausted their retry budget",
            ),
            (
                "CollectionManifestLowCoverageGatesAlarm",
                "stockara-collection-manifest-low-coverage-gates",
                "collection-manifest-low-coverage-gates",
                "collection_manifest_low_coverage_gates",
                "Minimum",
                Duration.hours(4),
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                "Collection coverage gates have remained below threshold",
            ),
            (
                "CollectionManifestLowCoveragePercentAlarm",
                "stockara-collection-manifest-low-coverage-percent",
                "collection-manifest-low-coverage-percent",
                "collection_manifest_coverage_percent",
                "Minimum",
                Duration.hours(4),
                90,
                cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
                "Collection manifest coverage remained below 90%",
            ),
            (
                "CollectionProviderFailuresAlarm",
                "stockara-collection-provider-failures",
                "collection-provider-failures",
                "collection_provider_failure_tasks",
                "Maximum",
                Duration.hours(1),
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                "One or more collection tasks are blocked by provider failures",
            ),
        ]

        for (
            construct_id,
            prod_alarm_name,
            staged_alarm_name,
            metric_name,
            statistic,
            period,
            threshold,
            comparison_operator,
            description,
        ) in manifest_health_alarms:
            alarm = cloudwatch.Alarm(
                self,
                construct_id,
                alarm_name=resource_name(
                    deployment_stage, prod_alarm_name, staged_alarm_name
                ),
                alarm_description=description,
                metric=cloudwatch.Metric(
                    namespace="StockMonitoring",
                    metric_name=metric_name,
                    statistic=statistic,
                    period=period,
                ),
                threshold=threshold,
                evaluation_periods=1,
                comparison_operator=comparison_operator,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            alarm.add_alarm_action(cw_actions.SnsAction(self.alerts_topic))

        product_quality_alarms = [
            (
                "ZeroTopPicksPublishedAlarm",
                "stockara-zero-top-picks-published",
                "zero-top-picks-published",
                "top_picks_published",
                "StockaraPhase1",
                "Sum",
                Duration.hours(26),
                1,
                cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
                cloudwatch.TreatMissingData.BREACHING,
                "No top picks were published in the expected daily window",
            ),
            (
                "LowAnalyzedCandidateCountAlarm",
                "stockara-low-analyzed-candidate-count",
                "low-analyzed-candidate-count",
                "ai_candidates_analyzed",
                "StockaraPhase1",
                "Sum",
                Duration.hours(26),
                1,
                cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
                cloudwatch.TreatMissingData.BREACHING,
                "No AI candidate analyses completed in the expected daily window",
            ),
            (
                "PublicationSuppressedAlarm",
                "stockara-publication-suppressed",
                "publication-suppressed",
                "publication_suppressed",
                "StockaraPhase1",
                "Sum",
                Duration.hours(26),
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                cloudwatch.TreatMissingData.NOT_BREACHING,
                "Daily publication was suppressed",
            ),
            (
                "FallbackAnalysisUsageAlarm",
                "stockara-fallback-analysis-usage",
                "fallback-analysis-usage",
                "fallback_analyses",
                "StockaraPhase1",
                "Sum",
                Duration.hours(26),
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                cloudwatch.TreatMissingData.NOT_BREACHING,
                "One or more candidates used heuristic fallback analysis",
            ),
            (
                "NewsProviderSourceOutageAlarm",
                "stockara-news-provider-source-outage",
                "news-provider-source-outage",
                "news_sources_failed",
                "StockMonitoring",
                "Sum",
                Duration.hours(1),
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                cloudwatch.TreatMissingData.NOT_BREACHING,
                "One or more configured news providers failed",
            ),
            (
                "ExcessiveTickerFailuresAlarm",
                "stockara-excessive-ticker-failures",
                "excessive-ticker-failures",
                "stock_collection_failed_tickers",
                "StockMonitoring",
                "Sum",
                Duration.hours(1),
                25,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                cloudwatch.TreatMissingData.NOT_BREACHING,
                "Stock collection failed for an excessive number of tickers",
            ),
            (
                "StockPriceGapsDetectedAlarm",
                "stockara-stock-price-gaps-detected",
                "stock-price-gaps-detected",
                "stock_price_gaps_detected",
                "StockMonitoring",
                "Sum",
                Duration.hours(26),
                1,
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                cloudwatch.TreatMissingData.NOT_BREACHING,
                "Recent OHLCV gaps were detected and need backfill follow-up",
            ),
        ]

        for (
            construct_id,
            prod_alarm_name,
            staged_alarm_name,
            metric_name,
            namespace,
            statistic,
            period,
            threshold,
            comparison_operator,
            treat_missing_data,
            description,
        ) in product_quality_alarms:
            alarm = cloudwatch.Alarm(
                self,
                construct_id,
                alarm_name=resource_name(
                    deployment_stage, prod_alarm_name, staged_alarm_name
                ),
                alarm_description=description,
                metric=cloudwatch.Metric(
                    namespace=namespace,
                    metric_name=metric_name,
                    statistic=statistic,
                    period=period,
                ),
                threshold=threshold,
                evaluation_periods=1,
                comparison_operator=comparison_operator,
                treat_missing_data=treat_missing_data,
            )
            alarm.add_alarm_action(cw_actions.SnsAction(self.alerts_topic))

        missing_metric_alarms = [
            (
                "TopPicksMissingMetricAlarm",
                "stockara-top-picks-missing-metric",
                "top-picks-missing-metric",
                "top_picks_published",
                "StockaraPhase1",
                Duration.hours(26),
                "No top-picks publication metric has arrived in the expected window",
            ),
            (
                "SellAlertsMissingMetricAlarm",
                "stockara-sell-alerts-missing-metric",
                "sell-alerts-missing-metric",
                "sell_alerts_published",
                "StockaraPhase1",
                Duration.hours(26),
                "No sell-alert publication metric has arrived in the expected window",
            ),
            (
                "StockCollectionMissingMetricAlarm",
                "stockara-stock-collection-missing-metric",
                "stock-collection-missing-metric",
                "stock_collection_completeness_percent",
                "StockMonitoring",
                Duration.hours(2),
                "No stock collection completeness metric has arrived in the expected window",
            ),
            (
                "NewsCollectionMissingMetricAlarm",
                "stockara-news-collection-missing-metric",
                "news-collection-missing-metric",
                "news_collection_completeness_percent",
                "StockMonitoring",
                Duration.hours(26),
                "No news collection completeness metric has arrived in the expected window",
            ),
            (
                "CollectionManifestMissingMetricAlarm",
                "stockara-collection-manifest-missing-metric",
                "collection-manifest-missing-metric",
                "collection_manifest_incomplete_tasks",
                "StockMonitoring",
                Duration.hours(2),
                "No collection manifest health metric has arrived in the expected window",
            ),
        ]

        for (
            construct_id,
            prod_alarm_name,
            staged_alarm_name,
            metric_name,
            namespace,
            period,
            description,
        ) in missing_metric_alarms:
            alarm = cloudwatch.Alarm(
                self,
                construct_id,
                alarm_name=resource_name(
                    deployment_stage, prod_alarm_name, staged_alarm_name
                ),
                alarm_description=description,
                metric=cloudwatch.Metric(
                    namespace=namespace,
                    metric_name=metric_name,
                    statistic="SampleCount",
                    period=period,
                ),
                threshold=1,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
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
                title="Collector completeness",
                left=[
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="stock_collection_completeness_percent",
                        statistic="Minimum",
                        period=Duration.hours(1),
                    ),
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="news_collection_completeness_percent",
                        statistic="Minimum",
                        period=Duration.hours(1),
                    ),
                ],
            ),
            cloudwatch.GraphWidget(
                title="Collector failures",
                left=[
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="stock_collection_failed_tickers",
                        statistic="Sum",
                        period=Duration.hours(1),
                    ),
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="news_sources_failed",
                        statistic="Sum",
                        period=Duration.hours(1),
                    ),
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="news_article_failures",
                        statistic="Sum",
                        period=Duration.hours(1),
                    ),
                ],
            ),
            cloudwatch.GraphWidget(
                title="Collection manifest health",
                left=[
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="collection_manifest_incomplete_tasks",
                        statistic="Maximum",
                        period=Duration.hours(1),
                    ),
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="collection_manifest_retry_exhausted_tasks",
                        statistic="Maximum",
                        period=Duration.hours(1),
                    ),
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="collection_provider_failure_tasks",
                        statistic="Maximum",
                        period=Duration.hours(1),
                    ),
                ],
                right=[
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="collection_manifest_coverage_percent",
                        statistic="Minimum",
                        period=Duration.hours(1),
                    ),
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="collection_manifest_age_minutes",
                        statistic="Maximum",
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
            cloudwatch.GraphWidget(
                title="Publication freshness and suppression",
                left=[
                    cloudwatch.Metric(
                        namespace="StockaraPhase1",
                        metric_name="top_picks_published",
                        statistic="SampleCount",
                        period=Duration.hours(26),
                    ),
                    cloudwatch.Metric(
                        namespace="StockaraPhase1",
                        metric_name="publication_suppressed",
                        statistic="Sum",
                        period=Duration.hours(26),
                    ),
                    cloudwatch.Metric(
                        namespace="StockaraPhase1",
                        metric_name="artifact_publish_failures",
                        statistic="Sum",
                        period=Duration.hours(1),
                    ),
                ],
            ),
            cloudwatch.GraphWidget(
                title="Fallback and review gate usage",
                left=[
                    cloudwatch.Metric(
                        namespace="StockaraPhase1",
                        metric_name="fallback_analyses",
                        statistic="Sum",
                        period=Duration.hours(26),
                    ),
                    cloudwatch.Metric(
                        namespace="StockaraPhase1",
                        metric_name="fallback_publication_suppressed",
                        statistic="Sum",
                        period=Duration.hours(26),
                    ),
                    cloudwatch.Metric(
                        namespace="StockaraPhase1",
                        metric_name="review_publication_suppressed",
                        statistic="Sum",
                        period=Duration.hours(26),
                    ),
                ],
            ),
            cloudwatch.GraphWidget(
                title="Backfill and gap health",
                left=[
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="stock_price_gaps_detected",
                        statistic="Sum",
                        period=Duration.hours(26),
                    ),
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="stock_price_backfill_records_inserted",
                        statistic="Sum",
                        period=Duration.hours(26),
                    ),
                    cloudwatch.Metric(
                        namespace="StockMonitoring",
                        metric_name="collection_manifest_incomplete_tasks",
                        statistic="Maximum",
                        period=Duration.hours(1),
                    ),
                ],
            ),
        )
