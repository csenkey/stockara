#!/usr/bin/env python3
"""AWS CDK application entry point for the Stock Monitoring System."""

import os

import aws_cdk as cdk

from stacks.api_stack import ApiStack
from stacks.database_stack import DatabaseStack
from stacks.demo_trading_stack import DemoTradingStack
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
api_stack = ApiStack(
    app,
    stack_id("StockMonitoringApi", deployment_stage),
    data_table=database_stack.table,
    user_pool=database_stack.user_pool,
    user_pool_client=database_stack.user_pool_client,
    deployment_stage=deployment_stage,
    env=env,
)
frontend_stack = FrontendStack(
    app,
    stack_id("StockMonitoringFrontend", deployment_stage),
    deployment_stage=deployment_stage,
    env=env,
)
monitoring_stack = MonitoringStack(
    app,
    stack_id("StockMonitoringMonitoring", deployment_stage),
    deployment_stage=deployment_stage,
    env=env,
)
demo_trading_stack = DemoTradingStack(
    app,
    stack_id("StockMonitoringDemoTrading", deployment_stage),
    data_table=database_stack.table,
    deployment_stage=deployment_stage,
    env=env,
)

app.synth()
