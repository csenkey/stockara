"""CDK stack for frontend hosting resources."""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from constructs import Construct


class FrontendStack(Stack):
    """Defines the frontend hosting for the Stock Monitoring System.

    Includes S3 bucket for React SPA and CloudFront distribution with OAC.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
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

        # CloudFront Origin Access Control for secure S3 access
        oac = cloudfront.CfnOriginAccessControl(
            self,
            "SiteOAC",
            origin_access_control_config=cloudfront.CfnOriginAccessControl.OriginAccessControlConfigProperty(
                name=f"{construct_id}-OAC",
                origin_access_control_origin_type="s3",
                signing_behavior="always",
                signing_protocol="sigv4",
            ),
        )

        # CloudFront distribution
        self.distribution = cloudfront.Distribution(
            self,
            "SiteDistribution",
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
