"""CDK stack for frontend hosting resources."""

import os

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3_deployment
from constructs import Construct

FRONTEND_DIST_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
)


class FrontendStack(Stack):
    """Defines the frontend hosting for the Stock Monitoring System.

    Includes S3 bucket for React SPA and CloudFront distribution with OAC.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        deployment_stage: str = "prod",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # S3 bucket for React SPA static assets
        self.site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )

        # CloudFront distribution
        self.distribution = cloudfront.Distribution(
            self,
            "SiteDistribution",
            # Some client networks advertise IPv6 but cannot complete CloudFront
            # connections, causing browser timeouts before IPv4 fallback.
            enable_ipv6=False,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(
                    self.site_bucket
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
            ),
            default_root_object="index.html",
            # SPA routing: return index.html for 403/404 errors so React Router handles routes
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(0),
                ),
            ],
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
        )

        if os.path.isdir(FRONTEND_DIST_PATH):
            s3_deployment.BucketDeployment(
                self,
                "DeployFrontend",
                sources=[s3_deployment.Source.asset(FRONTEND_DIST_PATH)],
                destination_bucket=self.site_bucket,
                distribution=self.distribution,
                distribution_paths=["/*"],
                exclude=[
                    "top-picks/*",
                    "sell-alerts/*",
                    "data-health/*",
                    "data-readiness/*",
                    "news/*",
                    "price-gaps/*",
                ],
                prune=False,
            )

        # Outputs
        CfnOutput(
            self,
            "BucketName",
            value=self.site_bucket.bucket_name,
            description="S3 bucket for frontend static assets",
        )

        CfnOutput(
            self,
            "DistributionDomainName",
            value=self.distribution.distribution_domain_name,
            description="CloudFront distribution domain name",
        )

        CfnOutput(
            self,
            "DistributionId",
            value=self.distribution.distribution_id,
            description="CloudFront distribution ID",
        )
