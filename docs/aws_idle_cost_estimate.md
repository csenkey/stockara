# AWS Idle Cost Estimate

Date: 2026-06-15

This document estimates the monthly idle cost for Stockara when the application is not serving meaningful user traffic but still runs its scheduled workloads:

- Daily stock data collection.
- News article collection every 15 minutes.
- Daily AI stock analysis.
- Daily demo-trading execution.
- Static frontend hosting and baseline observability.

Prices vary by AWS region, account free-tier status, data volume, and OpenAI model pricing. Treat these numbers as planning estimates, not billing guarantees.

## Current Infrastructure Shape

The current CDK design is serverless and does not define a VPC, NAT Gateway, VPN, EC2 instance, RDS database, load balancer, or container service.

Main deployed resources:

- Lambda functions:
  - `stock-monitoring-stock-collector`, 512 MB, daily.
  - `stock-monitoring-news-collector`, 256 MB, every 15 minutes.
  - `stock-monitoring-ai-analyzer`, 1024 MB, daily.
  - `stock-monitoring-demo-trade-executor`, 512 MB, daily.
  - `stock-monitoring-api-handler`, 512 MB, request-driven.
- EventBridge scheduled rules for batch jobs.
- DynamoDB single table in on-demand billing mode, with point-in-time recovery enabled.
- Cognito user pool.
- KMS customer-managed key for portfolio encryption.
- S3 and CloudFront for the React frontend.
- CloudWatch logs, custom metrics, alarms, and dashboard.

## AI Workloads

The daily stock analyzer uses `OPENAI_MODEL`, defaulting in code to `gpt-4o-mini`. It makes one OpenAI call per active stock using recent OHLCV data, technical indicators, and related news summaries.

The news collector also uses `gpt-4o-mini` to summarize each new, deduplicated article and identify related tickers.

Baseline assumptions:

- 1000 active stocks.
- 30 days per month.
- 1 AI analysis call per stock per day.
- Analysis prompt estimate: 2000 input tokens and 300 output tokens per stock.
- News summary estimate: 300 input tokens and 100 output tokens per new article.

Daily stock analysis volume:

```text
1000 stocks/day * 30 days = 30,000 analysis calls/month
30,000 calls * 2,000 input tokens = 60M input tokens/month
30,000 calls * 300 output tokens = 9M output tokens/month
```

Estimated daily stock analysis cost:

| Model pricing assumption | Input cost | Output cost | Monthly estimate |
| --- | ---: | ---: | ---: |
| Legacy `gpt-4o-mini` style pricing: $0.15/M input, $0.60/M output | $9.00 | $5.40 | ~$14.40 |
| Current listed mini-model style pricing: $0.75/M input, $4.50/M output | $45.00 | $40.50 | ~$85.50 |

Estimated news summarization cost per 1000 new articles/day:

| Model pricing assumption | Monthly estimate |
| --- | ---: |
| Legacy `gpt-4o-mini` style pricing | ~$3/month |
| Current listed mini-model style pricing | ~$20/month |

AI cost is the main variable in the idle bill.

## AWS-Only Estimate

For the current serverless design and low/no user traffic, AWS-only idle cost is expected to be roughly:

```text
$3-$10/month
```

Approximate breakdown:

| Area | Estimated monthly cost | Notes |
| --- | ---: | --- |
| Lambda scheduled jobs | $0 with free tier; ~$4-$5 without free tier if jobs run near max duration | Lambda is request and duration based. |
| EventBridge schedules | ~$0 | Only a few thousand schedule invocations per month. |
| DynamoDB on-demand reads/writes/storage/PITR | <$1-$3 early on | Grows with retained stock, news, analysis, snapshots, and PITR size. |
| KMS key | ~$1 | Customer-managed key baseline. Request cost should be tiny while idle. |
| CloudWatch logs, custom metrics, alarms, dashboard | ~$2-$6 | Custom metrics can cost more than the scheduled compute. |
| S3 + CloudFront | <$1 | Static frontend with little traffic. |
| API Gateway | $0 while idle | No minimum charge. |
| Cognito | $0 for no active users | Can change once users actively sign in, especially with higher security tiers/features. |
| NAT Gateway / VPN | $0 currently | Not present in the CDK design. |

## Total Idle Estimate

Expected total monthly idle cost:

| Scenario | Estimated monthly total |
| --- | ---: |
| Current AWS design + legacy `gpt-4o-mini` style pricing | ~$20-$35 |
| Current AWS design + current listed mini-model style pricing | ~$110-$130 |
| Current AWS design without AI calls | ~$3-$10 |

## NAT Gateway and VPC Warning

The current app does not need NAT Gateway or VPN because the Lambdas are not placed inside a VPC.

If Lambdas are later moved into private subnets and still need internet egress for OpenAI, NewsAPI, Finnhub, yfinance, or Alpha Vantage, NAT Gateway can become a large fixed cost:

```text
$0.045/hour * 730 hours = ~$32.85/month per NAT Gateway/AZ
```

Examples:

| NAT layout | Monthly baseline before data processing |
| --- | ---: |
| 1 NAT Gateway | ~$33 |
| 2 NAT Gateways / AZs | ~$66 |
| 3 NAT Gateways / AZs | ~$99 |

NAT data processing and internet data transfer are additional. Avoid adding NAT unless there is a strong architectural reason.

## Cost Risks and Notes

- The AI analyzer currently processes stocks sequentially inside a 15-minute Lambda. Cost is low, but completion reliability may become an issue with 1000+ OpenAI calls.
- `news_for_ticker` scans news items and filters by ticker. This is acceptable at small scale but can increase DynamoDB read costs as news history grows.
- CloudWatch custom metrics are not free forever; each distinct custom metric can add a small monthly cost.
- DynamoDB PITR cost grows with table size.
- Public demo account snapshots and transaction history add steady DynamoDB storage and write growth.
- Provider API plans are not included here. NewsAPI, Finnhub, Alpha Vantage, and yfinance availability/rate limits may require paid plans later.

## References

- AWS Lambda pricing: https://aws.amazon.com/lambda/pricing/
- Amazon DynamoDB pricing: https://aws.amazon.com/dynamodb/pricing/
- Amazon API Gateway pricing: https://aws.amazon.com/api-gateway/pricing/
- Amazon EventBridge pricing: https://aws.amazon.com/eventbridge/pricing/
- Amazon CloudWatch pricing: https://aws.amazon.com/cloudwatch/pricing/
- AWS KMS pricing: https://aws.amazon.com/kms/pricing/
- Amazon VPC NAT Gateway pricing: https://aws.amazon.com/vpc/pricing/
- Amazon Cognito pricing: https://aws.amazon.com/cognito/pricing/
- OpenAI API pricing: https://developers.openai.com/api/docs/pricing
