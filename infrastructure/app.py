#!/usr/bin/env python3
"""AWS CDK application entry point for the Stock Monitoring System."""

import aws_cdk as cdk

from stacks.api_stack import ApiStack
from stacks.database_stack import DatabaseStack
from stacks.frontend_stack import FrontendStack
from stacks.monitoring_stack import MonitoringStack

app = cdk.App()

env = cdk.Environment(
    account=app.node.try_get_context("account"),
    region=app.node.try_get_context("region") or "us-east-1",
)

database_stack = DatabaseStack(app, "StockMonitoringDatabase", env=env)
api_stack = ApiStack(app, "StockMonitoringApi", env=env)
frontend_stack = FrontendStack(app, "StockMonitoringFrontend", env=env)
monitoring_stack = MonitoringStack(app, "StockMonitoringMonitoring", env=env)

app.synth()
