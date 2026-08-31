"""CDK stack for frontend hosting resources."""

import os

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
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
        custom_domain_name: str | None = None,
        hosted_zone_id: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        normalized_domain = (custom_domain_name or "").strip().lower().rstrip(".")
        normalized_zone_id = (hosted_zone_id or "").strip()
        use_custom_domain = bool(normalized_domain and normalized_zone_id)

        hosted_zone = None
        certificate = None
        domain_names: list[str] = []
        if use_custom_domain:
            hosted_zone = route53.HostedZone.from_hosted_zone_attributes(
                self,
                "CustomDomainHostedZone",
                hosted_zone_id=normalized_zone_id,
                zone_name=normalized_domain,
            )
            domain_names = [normalized_domain, f"www.{normalized_domain}"]
            certificate = acm.Certificate(
                self,
                "SiteCertificate",
                domain_name=normalized_domain,
                subject_alternative_names=[f"www.{normalized_domain}"],
                validation=acm.CertificateValidation.from_dns(hosted_zone),
            )

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
            domain_names=domain_names or None,
            certificate=certificate,
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
        self.site_url = (
            f"https://{normalized_domain}"
            if use_custom_domain
            else f"https://{self.distribution.distribution_domain_name}"
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

        if hosted_zone is not None:
            for record_name in [normalized_domain, f"www.{normalized_domain}"]:
                route53.ARecord(
                    self,
                    f"AliasRecord{record_name.replace('.', '')}",
                    zone=hosted_zone,
                    record_name=record_name,
                    target=route53.RecordTarget.from_alias(
                        route53_targets.CloudFrontTarget(self.distribution)
                    ),
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

        if use_custom_domain:
            CfnOutput(
                self,
                "CustomDomainName",
                value=normalized_domain,
                description="Custom frontend domain name",
            )
