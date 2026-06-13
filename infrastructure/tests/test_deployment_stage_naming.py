"""Tests for branch-scoped CDK deployment naming."""

import aws_cdk as cdk
import aws_cdk.assertions as assertions

from stacks.database_stack import DatabaseStack
from stacks.naming import resource_name, sanitize_stage, stack_id


def test_sanitize_stage_normalizes_branch_names():
    assert sanitize_stage("main") == "prod"
    assert sanitize_stage("Feature/Add DynamoDB!") == "feature-add-dynamodb"
    assert sanitize_stage("codex/very-long-branch-name-that-keeps-going") == (
        "codex-very-long-branch-n"
    )


def test_prod_names_are_preserved():
    assert stack_id("StockMonitoringDatabase", "prod") == "StockMonitoringDatabase"
    assert resource_name("prod", "stockara", "data") == "stockara"


def test_feature_database_table_name_is_stage_scoped():
    app = cdk.App()
    stack = DatabaseStack(
        app,
        "StockMonitoringDatabase-codex-cicd",
        deployment_stage="codex-cicd",
    )
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "stockara-codex-cicd-data",
        },
    )
