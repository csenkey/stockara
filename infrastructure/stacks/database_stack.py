"""CDK stack for DynamoDB, authentication, and encryption resources."""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput,
    aws_cognito as cognito,
    aws_dynamodb as dynamodb,
    aws_kms as kms,
)
from constructs import Construct

from .naming import resource_name


class DatabaseStack(Stack):
    """Defines the Stockara data layer.

    Includes a DynamoDB single-table store, Cognito User Pool, and KMS key.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        deployment_stage: str = "prod",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.table = dynamodb.Table(
            self,
            "StockaraTable",
            table_name=resource_name(deployment_stage, "stockara", "data"),
            partition_key=dynamodb.Attribute(
                name="PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="SK", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(
                name="GSI1PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="GSI1SK", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        self.table.add_global_secondary_index(
            index_name="GSI2",
            partition_key=dynamodb.Attribute(
                name="GSI2PK", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="GSI2SK", type=dynamodb.AttributeType.STRING
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        self.user_pool = cognito.UserPool(
            self,
            "StockMonitoringUserPool",
            user_pool_name=resource_name(
                deployment_stage, "stock-monitoring-users", "users"
            ),
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_uppercase=True,
                require_lowercase=True,
                require_digits=True,
                require_symbols=False,
                temp_password_validity=Duration.days(7),
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.RETAIN,
            standard_threat_protection_mode=cognito.StandardThreatProtectionMode.FULL_FUNCTION,
        )

        self.user_pool_client = cognito.UserPoolClient(
            self,
            "StockMonitoringUserPoolClient",
            user_pool=self.user_pool,
            user_pool_client_name=resource_name(
                deployment_stage, "stock-monitoring-web-client", "web-client"
            ),
            auth_flows=cognito.AuthFlow(user_password=True, user_srp=True),
            id_token_validity=Duration.minutes(30),
            access_token_validity=Duration.minutes(30),
            refresh_token_validity=Duration.days(7),
            prevent_user_existence_errors=True,
        )

        self.portfolio_encryption_key = kms.Key(
            self,
            "PortfolioEncryptionKey",
            alias=resource_name(
                deployment_stage,
                "stock-monitoring/portfolio-encryption",
                "portfolio-encryption",
            ),
            description="AES-256 encryption key for user portfolio data",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
            key_spec=kms.KeySpec.SYMMETRIC_DEFAULT,
            key_usage=kms.KeyUsage.ENCRYPT_DECRYPT,
        )

        CfnOutput(
            self,
            "DynamoTableName",
            value=self.table.table_name,
            description="DynamoDB single-table name",
        )
        CfnOutput(
            self,
            "UserPoolId",
            value=self.user_pool.user_pool_id,
            description="Cognito User Pool ID",
        )
        CfnOutput(
            self,
            "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
            description="Cognito User Pool Client ID",
        )
        CfnOutput(
            self,
            "PortfolioEncryptionKeyArn",
            value=self.portfolio_encryption_key.key_arn,
            description="KMS key ARN for portfolio encryption",
        )
