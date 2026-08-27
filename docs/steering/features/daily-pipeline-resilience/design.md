# Daily Pipeline Resilience Design

## Overview

### 2026-08-27 Architecture Review Hardening

The optional collector contract now distinguishes typed partial results from
unexpected exceptions. Partial results return normally; unexpected news or
evidence failures are logged and re-raised so the existing Step Functions catch
states can publish accurate degraded-step context. Missing manifest worker
configuration is persisted through the optimistic task mutation path as a
terminal `worker_not_configured:*` failure.

Monitoring receives the actual `IFunction` and `IStateMachine` constructs from
`ApiStack`, creating a synthesis-time dependency on deployed resource identity
and covering all twelve Lambdas. Gap paging uses affected active-ticker percent;
the raw gap count remains available on the dashboard. Deployment smoke checks
fail on hard database or collection failures while retaining warnings for
recoverable degradation.

The reviewed GPT-5.6 Luna/Terra IDs remain unchanged because the current official
OpenAI catalog confirms both IDs and Chat Completions support. Existing fallback
analysis metrics, alarms, and dashboard widgets remain the operational guard.

### 2026-08-26 Payload And Reporting Correction

`CollectCalendarsAndEvidence` starts each branch with an empty context because
its collector requests are constants. It retains only each branch's own result.
`CollectReviewEvidence` receives only the bounded analysis repair plan, produces
small skipped/results objects, and discards its aggregate result: reanalysis
reads evidence already persisted by the collectors. Lambda integration results
keep `Payload`, removing unused SDK response metadata. Parent state used by later
readiness/status decisions remains intact.

An independent `stockara-workflow-reporter` Lambda handles normal status writes,
terminal Step Functions events, and reconciliation at 00:20, 01:20, and 06:20 UTC.
It has no database, provider, or AI-secret permissions. It can read only the
configured state machine's execution history and write only `workflow/*` artifacts.
All producers enqueue reports to one FIFO SQS message group, and the reporter
consumes one message per invocation. This serializes production status writes
without consuming scarce account-level reserved Lambda concurrency.

Normal workflow reporting and external reporting use the same compact contract.
External reporting reads `DescribeExecution` and a bounded reverse history tail
for progress/failure attribution; raw inputs and causes are not published.
Failure causes use safe summaries because AWS runtime errors can include input
data. Actual terminal observations take precedence over provisional in-workflow
reports. The reporter compares UTC run/start times before replacing daily/latest
artifacts, saves per-execution history, and leaves recommendation artifacts alone.

EventBridge delivery is backed by reconciliation, Lambda retries and a dead-letter
queue. Existing SNS-backed alarms cover workflow failures; reporter Lambda errors
and the overdue-report metric add coverage for the reporting path. Subscribing an
email recipient remains a separate operator authorization.

The frontend computes the expected completed UTC run using the same deadline,
refreshes its clock every minute, labels old recommendations, and reports whether
analysis was reached before a later step failed. No investment logic changes.

The correction separates required-data safety from optional-enrichment
availability. The analyzer remains strict per ticker, while optional collectors
become bounded producers of typed degraded context.

The design changes four paths:

1. Bound news work before and during execution.
2. Continue the workflow when optional collectors fail.
3. Publish precise operational and business status.
4. Reconcile production metadata with the canonical watchlist.

## Root Cause

The 2026-08-03 scheduled execution reached `CollectNews` after price collection
at approximately 21:29 UTC. The news Lambda timed out five minutes later.

The current implementation has three compounding behaviors:

- `repair_news` applies `max_tickers` only when an explicit ticker list exists.
- Provider budget values mostly enable providers; they do not bound all work.
- Every new fetched article may trigger a sequential OpenAI summary call, with
  no remaining-time guard or per-run article cap.

Step Functions catches `Sandbox.Timedout`, writes a global `blocked` decision,
and then completes successfully after publishing workflow status. This preserves
an artifact but hides the failed step behind a technically green execution.

## Architecture

### Bounded News Work Plan

Introduce a normalized internal work plan for every news invocation:

```text
NewsWorkPlan
  tickers
  ticker_limit
  article_limit
  provider_request_limits
  deadline_safety_margin_ms
  continuation
```

For `repair_news` without explicit tickers, resolve active, in-scope metadata,
sort it deterministically, and select a rotating window derived from the run
date. Rotation avoids permanently favoring alphabetically early symbols.

Provider budgets are normalized once and passed into provider-specific fetch
functions. The limits mean maximum requests or ticker requests for that
provider. Global endpoints such as NewsAPI count as one request; ticker fanout
such as Finnhub counts once per ticker.

After deduplication, sort new articles deterministically by publication time and
hash, process at most `article_limit`, and report the rest as deferred. Before
each AI summary, read `context.get_remaining_time_in_millis()`. Stop when the
remaining time is below a safety margin that covers one model call plus result
persistence.

Each article is stored immediately through the existing idempotent hash key. A
partial run therefore needs no rollback. Its continuation metadata is advisory:
the next invocation can safely refetch and deduplicate while using the cursor to
improve fairness.

### Optional Workflow Failure Paths

Replace the single global catch behavior for optional collectors with typed
fallback states:

```text
CollectNews
  success/partial -> CollectCalendarsAndEvidence
  error/timeout   -> RecordNewsDegraded -> CollectCalendarsAndEvidence

CollectCalendarsAndEvidence
  each branch success -> branch result
  each branch failure -> typed degraded branch result
  parallel completes  -> AnalyzeAndPublish
```

The degraded result schema is:

```json
{
  "status": "degraded",
  "step": "CollectNews",
  "required": false,
  "error_type": "Sandbox.Timedout",
  "message": "Task timed out after 300 seconds",
  "retryable": true,
  "occurred_at": "..."
}
```

Required workflow failures still route to the global blocked classifier. These
include failures that prevent metadata/manifest interpretation or leave no safe
way to evaluate individual ticker eligibility.

### Analyzer Contract

No recommendation-quality relaxation is required. Existing analyzer behavior
continues to:

- Exclude tickers with stale/missing price data or insufficient history.
- Record missing optional evidence.
- Reduce confidence and choose `reduced_confidence` when appropriate.
- Require valid AI review for public actionable recommendations.

The analyzer receives optional step summaries through the workflow context and
adds their warnings to data quality/readiness artifacts.

### Workflow Status Contract

Extend `workflow/latest.json` with:

```json
{
  "execution_status": "SUCCEEDED",
  "business_status": "degraded",
  "failed_step": null,
  "degraded_steps": ["CollectNews"],
  "analysis_reached": true
}
```

For a caught required failure, `failed_step` is populated and
`analysis_reached` is false when appropriate. Step summaries prefer typed catch
results over `unknown` placeholders.

### Active-Universe Reconciliation

Extend the existing metadata sync/repair path rather than creating a separate
database migration tool.

The operation has two phases:

1. Dry-run audit: return canonical, active, out-of-scope, and post-apply counts
   plus the exact out-of-scope ticker list.
2. Explicit apply: update only `is_active` and reconciliation provenance for
   confirmed `active_not_in_seed` rows. Historical OHLCV, news, analysis, and
   metadata fields remain intact.

The daily workflow may continue to audit drift, but destructive reconciliation
must be an explicit repair-mode input until production verification proves the
classification safe.

### Dashboard State Model

The page consumes two dates and two sets of metrics:

- Current workflow: run date, status, failed/degraded steps, analysis progress.
- Latest publication: publication date, generation time, analyzed count, picks,
  alerts, and recommendations.

When dates differ, publication cards receive an explicit “Latest completed
publication” label and date. Current-run scan metrics are shown only when the
workflow artifact contains current analyzer counters.

## Error Handling

- Provider request failures produce source-level partial results.
- Article summary failures increment failure counters and continue.
- Low remaining time produces a clean partial result.
- Unexpected news Lambda failure becomes optional degraded workflow state.
- Required collector or artifact failures retain blocked behavior.
- Invalid AI review payloads remain `invalid_response`, retry once, and never
  masquerade as valid rejection.

## Testing Strategy

### Backend

- Repair request without tickers respects `max_tickers`.
- Provider request limits are enforced.
- Article cap reports deferred work.
- Fake Lambda context forces a clean time-budget exit.
- Partial rerun deduplicates already stored articles.
- Optional degradation reaches per-ticker eligibility and analysis.

### Infrastructure

- `CollectNews` timeout routes to degraded continuation.
- Each optional parallel branch has an independent catch.
- Required failures still route to blocked status.
- Workflow status receives typed step information.

### Frontend

- Current and publication dates match.
- Current run is newer than publication.
- Optional degraded run reached analysis.
- Required failure blocked before analysis.
- Old publication metrics are never labeled as current scan progress.

## Deployment And Verification

1. Deploy through `main` after all local suites pass.
2. Run metadata reconciliation in dry-run mode and review all proposed rows.
3. Apply only the confirmed out-of-scope inactivation set.
4. Start a fresh production daily workflow.
5. Observe collector duration, deferred news count, eligible ticker count,
   analyzer progress, review validity, and final artifact dates.
6. Treat deployment as incomplete until the production workflow publishes a
   terminal current-day artifact and smoke checks validate it.
