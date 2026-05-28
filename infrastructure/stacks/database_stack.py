"""CDK stack for database, authentication, and encryption resources."""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    CfnOutput,
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_cognito as cognito,
    aws_kms as kms,
)
from constructs import Construct


class DatabaseStack(Stack):
    """Defines the data layer for the Stock Monitoring System.

    Includes RDS Serverless v2 (PostgreSQL), Cognito User Pool, and KMS key.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- VPC for RDS ---
        self.vpc = ec2.Vpc(
            self,
            "StockMonitoringVpc",
            max_azs=2,
            nat_gateways=0,  # Cost optimization: no NAT gateways
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                ),
            ],
        )

        # --- RDS Serverless v2 Aurora PostgreSQL ---
        self.db_security_group = ec2.SecurityGroup(
            self,
            "DatabaseSecurityGroup",
            vpc=self.vpc,
            description="Security group for Stock Monitoring RDS instance",
            allow_all_outbound=False,
        )

        self.db_cluster = rds.DatabaseCluster(
            self,
            "StockMonitoringDb",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_15_4,
            ),
            serverless_v2_min_capacity=0.5,
            serverless_v2_max_capacity=2,
            writer=rds.ClusterInstance.serverless_v2(
                "Writer",
                auto_minor_version_upgrade=True,
            ),
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
            ),
            security_groups=[self.db_security_group],
            default_database_name="stock_monitoring",
            storage_encrypted=True,
            removal_policy=RemovalPolicy.SNAPSHOT,
            backup=rds.BackupProps(retention=Duration.days(7)),
        )

        # --- Cognito User Pool ---
        self.user_pool = cognito.UserPool(
            self,
            "StockMonitoringUserPool",
            user_pool_name="stock-monitoring-users",
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
            # Threat protection for account lockout after 5 failed attempts
            standard_threat_protection_mode=cognito.StandardThreatProtectionMode.FULL_FUNCTION,
        )

        # User pool client with auth flows and token validity
        self.user_pool_client = cognito.UserPoolClient(
            self,
            "StockMonitoringUserPoolClient",
            user_pool=self.user_pool,
            user_pool_client_name="stock-monitoring-web-client",
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
            ),
            id_token_validity=Duration.minutes(30),
            access_token_validity=Duration.minutes(30),
            refresh_token_validity=Duration.days(7),
            prevent_user_existence_errors=True,  # Generic error messages (Req 7.4)
        )

        # --- KMS Key for Portfolio Encryption (AES-256) ---
        self.portfolio_encryption_key = kms.Key(
            self,
            "PortfolioEncryptionKey",
            alias="stock-monitoring/portfolio-encryption",
            description="AES-256 encryption key for user portfolio data",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
            key_spec=kms.KeySpec.SYMMETRIC_DEFAULT,  # AES-256
            key_usage=kms.KeyUsage.ENCRYPT_DECRYPT,
        )

        # --- Outputs ---
        CfnOutput(
            self,
            "DatabaseClusterEndpoint",
            value=self.db_cluster.cluster_endpoint.hostname,
            description="RDS cluster endpoint hostname",
        )

        CfnOutput(
            self,
            "DatabaseClusterPort",
            value=str(self.db_cluster.cluster_endpoint.port),
            description="RDS cluster endpoint port",
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
