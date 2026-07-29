"""Tests for the FrontendStack CDK stack."""

import aws_cdk as cdk
import aws_cdk.assertions as assertions

from stacks.frontend_stack import FrontendStack


def test_s3_bucket_created_with_versioning_and_block_public_access():
    """Verify S3 bucket is private, versioned, and blocks public access."""
    app = cdk.App()
    stack = FrontendStack(app, "TestFrontend")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "VersioningConfiguration": {"Status": "Enabled"},
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        },
    )


def test_cloudfront_distribution_created():
    """Verify CloudFront distribution exists with HTTPS redirect."""
    app = cdk.App()
    stack = FrontendStack(app, "TestFrontend")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": {
                "DefaultRootObject": "index.html",
                "DefaultCacheBehavior": {
                    "ViewerProtocolPolicy": "redirect-to-https",
                },
            },
        },
    )


def test_cloudfront_ipv6_is_disabled():
    """Verify CloudFront does not publish IPv6 addresses."""
    app = cdk.App()
    stack = FrontendStack(app, "TestFrontend")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {"DistributionConfig": {"IPV6Enabled": False}},
    )


def test_cloudfront_custom_error_responses_for_spa():
    """Verify 403 and 404 redirect to index.html for SPA routing."""
    app = cdk.App()
    stack = FrontendStack(app, "TestFrontend")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": {
                "CustomErrorResponses": [
                    {
                        "ErrorCode": 403,
                        "ResponseCode": 200,
                        "ResponsePagePath": "/index.html",
                    },
                    {
                        "ErrorCode": 404,
                        "ResponseCode": 200,
                        "ResponsePagePath": "/index.html",
                    },
                ],
            },
        },
    )


def test_frontend_deployment_does_not_prune_bucket_data():
    """Verify frontend deploy does not delete generated or operational data."""
    app = cdk.App()
    stack = FrontendStack(app, "TestFrontend")
    template = assertions.Template.from_stack(stack)

    template.has_resource_properties(
        "Custom::CDKBucketDeployment",
        {
            "Prune": False,
            "Exclude": [
                "top-picks/*",
                "sell-alerts/*",
                "data-health/*",
                "data-readiness/*",
                "news/*",
                "price-gaps/*",
            ],
        },
    )


def test_outputs_exist():
    """Verify stack exports bucket name and distribution domain."""
    app = cdk.App()
    stack = FrontendStack(app, "TestFrontend")
    template = assertions.Template.from_stack(stack)

    template.has_output("BucketName", {})
    template.has_output("DistributionDomainName", {})
    template.has_output("DistributionId", {})
