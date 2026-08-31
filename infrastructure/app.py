#!/usr/bin/env python3
"""AWS CDK application entry point for the Stock Monitoring System."""

import os

import aws_cdk as cdk

from stacks.api_stack import ApiStack
from stacks.database_stack import DatabaseStack
from stacks.frontend_stack import FrontendStack
from stacks.monitoring_stack import MonitoringStack
from stacks.naming import sanitize_stage, stack_id

app = cdk.App()

deployment_stage = sanitize_stage(
    app.node.try_get_context("deploymentStage")
    or os.environ.get("DEPLOYMENT_STAGE")
    or "prod"
)

env = cdk.Environment(
    account=app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=(
        app.node.try_get_context("region")
        or os.environ.get("AWS_REGION")
        or os.environ.get("CDK_DEFAULT_REGION")
        or "us-east-1"
    ),
)

database_stack = DatabaseStack(
    app,
    stack_id("StockMonitoringDatabase", deployment_stage),
    deployment_stage=deployment_stage,
    env=env,
)
frontend_stack = FrontendStack(
    app,
    stack_id("StockMonitoringFrontend", deployment_stage),
    deployment_stage=deployment_stage,
    custom_domain_name=(
        app.node.try_get_context("customDomainName")
        or os.environ.get("CUSTOM_DOMAIN_NAME")
    ),
    hosted_zone_id=(
        app.node.try_get_context("route53HostedZoneId")
        or os.environ.get("ROUTE53_HOSTED_ZONE_ID")
    ),
    env=env,
)
api_stack = ApiStack(
    app,
    stack_id("StockMonitoringApi", deployment_stage),
    data_table=database_stack.table,
    artifact_bucket=frontend_stack.site_bucket,
    site_url=frontend_stack.site_url,
    google_oauth_client_id=(
        app.node.try_get_context("googleOAuthClientId")
        or os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    ),
    google_oauth_client_secret_name=(
        app.node.try_get_context("googleOAuthClientSecretName")
        or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET_NAME")
    ),
    facebook_oauth_client_id=(
        app.node.try_get_context("facebookOAuthClientId")
        or os.environ.get("FACEBOOK_OAUTH_CLIENT_ID")
    ),
    facebook_oauth_client_secret_name=(
        app.node.try_get_context("facebookOAuthClientSecretName")
        or os.environ.get("FACEBOOK_OAUTH_CLIENT_SECRET_NAME")
    ),
    apple_oauth_client_id=(
        app.node.try_get_context("appleOAuthClientId")
        or os.environ.get("APPLE_OAUTH_CLIENT_ID")
    ),
    apple_oauth_team_id=(
        app.node.try_get_context("appleOAuthTeamId")
        or os.environ.get("APPLE_OAUTH_TEAM_ID")
    ),
    apple_oauth_key_id=(
        app.node.try_get_context("appleOAuthKeyId")
        or os.environ.get("APPLE_OAUTH_KEY_ID")
    ),
    apple_oauth_private_key_secret_name=(
        app.node.try_get_context("appleOAuthPrivateKeySecretName")
        or os.environ.get("APPLE_OAUTH_PRIVATE_KEY_SECRET_NAME")
    ),
    deployment_stage=deployment_stage,
    env=env,
)
api_stack.add_dependency(frontend_stack)
monitoring_stack = MonitoringStack(
    app,
    stack_id("StockMonitoringMonitoring", deployment_stage),
    monitored_functions={
        "workflow_reporter": api_stack.workflow_reporter_fn,
        "stock_collector": api_stack.stock_collector_fn,
        "stooq_zip_extractor": api_stack.stooq_zip_extractor_fn,
        "stock_gap_scanner": api_stack.stock_gap_scanner_fn,
        "watchlist_seed": api_stack.watchlist_seed_fn,
        "news_collector": api_stack.news_collector_fn,
        "evidence_collector": api_stack.evidence_collector_fn,
        "earnings_collector": api_stack.earnings_collector_fn,
        "dividend_collector": api_stack.dividend_collector_fn,
        "collection_distributor": api_stack.collection_distributor_fn,
        "publisher": api_stack.ai_analyzer_fn,
        "health_api": api_stack.api_handler_fn,
    },
    daily_workflow=api_stack.daily_workflow,
    deployment_stage=deployment_stage,
    env=env,
)

app.synth()
