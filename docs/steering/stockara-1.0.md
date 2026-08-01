# Stockara 1.0 Baseline

Stockara 1.0 is the first stable production checkpoint. It is a low-cost,
serverless daily stock research system: it collects market and optional event
data, scores a large watchlist cheaply, analyzes a bounded shortlist with AI,
reviews actionable BUY/SELL calls, and publishes static artifacts for the
public dashboard.

Stable Git reference: `stockara-1.0`.

## What 1.0 Ships

- A tracked universe of roughly 900-1,000 active tickers with production
  metadata drift detection and repair.
- Idempotent OHLCV collection, recent price-gap detection, bounded historical
  repair, and provider/ticker health reporting.
- News collection with quota-conscious NewsAPI/Finnhub/Alpha Vantage usage,
  ticker-aware collection, deduplication, and optional AI summaries.
- Earnings and dividend collection with provider fallback and readiness
  reporting.
- Cheap candidate scoring followed by bounded AI analysis and a stronger AI
  review gate for public BUY/SELL calls.
- Explicit publication tiers: `decision_grade`, `reduced_confidence`,
  `fallback_preview`, and `blocked`.
- Public static artifacts for top picks, urgent sell alerts, data readiness,
  and workflow status, served from S3/CloudFront.
- A React dashboard that explains stale publications, degraded runs, withheld
  calls, evidence gaps, freshness exclusions, and fallback previews.
- A public demo-trading surface with 100 simulated accounts, daily trading,
  1% commission, leaderboards, account detail, transactions, and performance.
- CloudWatch metrics, alarms, a Phase 1 dashboard, and an SNS alert topic.
- A daily AWS Step Functions Standard Workflow that owns the production run.

## Production Architecture

```text
EventBridge 21:05 UTC
        |
        v
Step Functions: stockara-daily-pipeline
  metadata -> manifest -> bounded workers -> price repair
  -> news -> calendars/evidence -> readiness
  -> shortlist analysis -> AI review -> publication
        |
        +--> DynamoDB: source data, manifests, task rows, analysis records
        +--> S3: public JSON artifacts and operational history
        +--> CloudWatch/SNS: metrics, alarms, operator notifications

CloudFront -> React dashboard + static JSON artifacts
API Gateway -> Lambda -> health and authenticated application APIs
EventBridge 23:15 UTC -> separate after-market price-gap maintenance
EventBridge three times daily -> quota-conscious news prefetch
EventBridge 22:30 UTC -> demo trade execution after analysis
```

The workflow is coarse-grained. Per-ticker work remains inside bounded Lambda
workers and manifest tasks; the state machine must not become a 900-ticker
state graph.

The daily workflow is the source of truth for publication. The retired
high-frequency analyzer, distributor, and stock schedules remain disabled
rollback paths in infrastructure. News prefetch and after-market gap scanning
are supporting jobs, not publication gates by themselves.

## Published Artifacts

- `top-picks/latest.json`
- `top-picks/history/{YYYY-MM-DD}.json`
- `sell-alerts/latest.json`
- `sell-alerts/history/{YYYY-MM-DD}.json`
- `data-readiness/latest.json`
- `data-readiness/history/{YYYY-MM-DD}.json`
- `workflow/latest.json`
- `workflow/history/{YYYY-MM-DD}.json`

The dashboard keeps the latest completed publication visible while a newer run
is waiting or running. A completed daily run can be `completed`,
`completed_degraded`, or `blocked`; an empty result is not by itself a system
failure and must be explained by readiness and review data.

## Trust And Safety Boundaries

- Public BUY/SELL calls require fresh identity/price/history data, AI analysis,
  and a passing stronger-model review.
- Missing optional evidence can produce a visibly lower-confidence suggestion,
  but never silently becomes a decision-grade pick.
- Fallback previews are for human research only and are excluded from automated
  and demo trading consumers.
- A valid reviewer rejection is not an incident. An absent, malformed, or
  unexplained review is an operational defect and must be retried or surfaced.
- Stockara is decision-support software, not financial advice or an autonomous
  trading system.

## Operations

Start with these sources, in order:

1. Step Functions execution for `stockara-daily-pipeline`.
2. `workflow/latest.json` and the dated workflow artifact.
3. `data-readiness/latest.json` for ticker/provider/repair detail.
4. CloudWatch dashboard and alarms.
5. Lambda logs and provider-specific runbooks.

Manual workflows are under `.github/workflows/` and include the daily workflow,
collection repair, AI retry, metadata sync, calendar/evidence collection, and
stable deployment paths. Prefer those workflows because they preserve the
production payload shape and capture diagnostic output.

## Deployment And Rollback

- Normal development happens directly on `main` while branch environments are
  disabled.
- A green commit on `main` deploys the active `prod` stage through CI/CD.
- Stable releases are immutable annotated tags named `stockara-X.Y`.
- `.github/workflows/deploy-stable.yml` deploys a selected stable tag to `prod`
  after running the normal validation and smoke-test path.
- To roll back, deploy the earlier stable tag. Never move, delete, or reuse a
  stable tag.

## Known 1.0 Limitations

- Optional evidence collection is still slower than the core workflow and can
  make a run degraded.
- Some reviewer rejections do not yet contain a structured explanation; this
  is the first post-1.0 stability task and must be fixed before treating all
  withheld calls as equally trustworthy.
- Full SEC filing substance extraction and durable fundamental/valuation
  evidence are not yet complete.
- Calendar historical backfill and provider coverage remain incomplete for
  backtesting.
- The existing alert topic requires an explicitly configured and confirmed
  subscriber before it can notify a human by email.

## Where To Continue

Read `docs/steering/work-queue.md` for priority, then the daily pipeline files:

- `docs/steering/features/daily-pipeline-stability/requirements.md`
- `docs/steering/features/daily-pipeline-stability/design.md`
- `docs/steering/features/daily-pipeline-stability/backlog.md`

The next implementation slice is the typed AI-review contract and bounded,
review-directed evidence repair loop. It should preserve the cost-conscious
once-daily model and the publication safety tiers.
