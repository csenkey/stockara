# Daily Pipeline Resilience Requirements

## Introduction

The daily Stockara workflow must publish useful opportunities from eligible
tickers even when optional collectors are slow or unavailable. A bounded
collector failure must not prevent the analyzer from evaluating otherwise
decision-ready stocks, and the public dashboard must accurately distinguish the
current workflow result from the latest completed recommendation publication.

This specification addresses the production failure observed on 2026-08-03:
`CollectNews` exhausted its 300-second Lambda timeout, the analyzer was never
invoked, Step Functions ended technically successful after catching the error,
and the dashboard continued to display the 2026-07-31 publication.

## Glossary

- **Eligible ticker**: An active, in-scope ticker with decision-ready identity,
  fresh price data, and sufficient analysis history.
- **Optional collector**: News, calendar, or supplementary evidence collection.
- **Required data**: Identity metadata, fresh price data, and sufficient price
  history for an individual ticker.
- **Business status**: The product outcome: `success`, `degraded`, `waiting`, or
  `blocked`.
- **Execution status**: The AWS Step Functions status, such as `SUCCEEDED` or
  `FAILED`.
- **Current workflow artifact**: `workflow/latest.json` for the newest daily run.
- **Latest publication artifact**: `top-picks/latest.json` for the newest
  completed analysis publication.

## Requirements

### 2026-08-26 Incident: Payload Limits And Missing Reports

The August 11–25 executions failed after the August 10 publication. AWS confirmed
`States.DataLimitExceeded` in `CollectReviewEvidence` on August 11 and August 25.
Parallel branch results retained copies of their input workflow context. The
`States.ALL` catch did not catch that terminal error, leaving both public dates
stale. The following requirements extend, rather than replace, the earlier work.

- Parallel branches receive only required inputs. Success, skipped, and degraded
  outputs cannot retain recursive copies of the parent workflow. Repeated repair
  passes must remain bounded below the 256 KiB service limit with headroom.
- Detailed evidence remains in its existing durable storage. Recommendation
  eligibility, confidence adjustments, review gates, and repair budgets remain
  unchanged.
- Actual `FAILED`, `TIMED_OUT`, and `ABORTED` executions produce independent
  workflow reports even when the workflow's own status step cannot run. Successful
  execution events confirm completion and can repair missing final reports.
- Reports preserve the original UTC run date, actual execution status, failure
  attribution, and available analysis progress. They never expose raw execution
  inputs, secrets, or overwrite recommendation artifacts.
- Duplicate and delayed reports cannot replace newer daily/latest results;
  per-execution reports remain available. Reporting is serialized.
- A scheduled reconciliation after the 21:05 UTC start plus three-hour timeout
  and 15-minute grace detects missing executions and recovers missed terminal
  events. Reconciliation failures and overdue reports are alarmed.
- Dashboard freshness uses that deadline independently of artifact-date equality;
  failures after analysis must not be described as analysis never reached.
- Diagnostic workflows show terminal errors and the tail of execution history,
  and inspect public status even when the execution failed.

### Requirement 1: Bounded News Collection

**User story:** As an operator, I want news collection to finish within its
Lambda budget so that an optional enrichment step cannot stop daily analysis.

#### Acceptance criteria

1. WHEN `repair_news` is invoked without explicit tickers and with
   `max_tickers`, THE News Collector SHALL resolve a deterministic active,
   in-scope ticker batch no larger than `max_tickers`.
2. WHEN a provider budget is supplied, THE News Collector SHALL enforce it as
   a real request/work limit rather than treating a positive number only as an
   enabled flag.
3. WHEN fetched new articles exceed the configured per-run article limit, THE
   News Collector SHALL process only the bounded subset and report the deferred
   count.
4. WHILE processing articles, THE News Collector SHALL check the Lambda
   remaining execution time before starting each summary operation.
5. WHEN remaining time reaches the configured safety margin, THE News Collector
   SHALL stop cleanly, preserve already stored articles, and return a typed
   `partial` result instead of timing out.
6. WHEN a bounded run is partial, THE result SHALL expose processed, failed,
   duplicate, deferred, and continuation details sufficient for a later
   idempotent invocation.
7. THE News Collector SHALL never regenerate or overwrite an existing article
   summary solely because a previous batch ended early.

### Requirement 2: Optional Evidence Must Degrade, Not Block

**User story:** As a user, I want opportunities from stocks with fresh required
data even if optional news or calendar services are temporarily degraded.

#### Acceptance criteria

1. WHEN the news collector returns `partial`, `failed`, or times out, THE daily
   workflow SHALL record degraded news and continue to the analyzer.
2. WHEN an earnings, dividend, or supplementary evidence branch fails, THE
   workflow SHALL record the affected branch and continue after the optional
   evidence parallel state.
3. WHEN optional evidence is unavailable, THE analyzer SHALL apply existing
   missing-evidence confidence adjustments and publication tiers.
4. THE workflow SHALL continue to block an individual ticker that lacks required
   metadata, fresh price data, or sufficient price history.
5. IF no ticker passes the individual required-data checks, THEN publication
   SHALL be suppressed with `no_eligible_tickers`.
6. THE workflow SHALL not convert an optional collector failure into a global
   `blocked` result when at least one eligible ticker can still be analyzed.

### Requirement 3: Accurate Workflow Failure Attribution

**User story:** As an operator, I want the status artifact to identify the exact
failed or degraded step so that I can diagnose a run without reconstructing the
Step Functions history.

#### Acceptance criteria

1. WHEN a workflow step fails or times out, THE workflow result SHALL include
   the step name, error type, message, timestamp, retryability, and whether the
   step is required or optional.
2. WHEN `CollectNews` fails before producing a Lambda response, THE published
   status SHALL report news as failed/degraded rather than `unknown`.
3. THE workflow artifact SHALL expose execution status separately from business
   status.
4. WHEN Step Functions catches an error and completes technically successfully,
   THE dashboard SHALL not present that execution as a successful business run.
5. THE workflow status SHALL retain the collection and analyzer progress that
   completed before the failure.

### Requirement 4: Canonical Active Universe

**User story:** As an operator, I want collection and coverage calculated only
for Stockara’s intended watchlist so that unrelated production rows do not
consume runtime or distort health metrics.

#### Acceptance criteria

1. THE metadata reconciliation path SHALL compare active production rows with
   the canonical repository watchlist.
2. WHEN a production ticker is conclusively absent from the canonical watchlist,
   THE reconciliation report SHALL classify it as `active_not_in_seed` before
   any mutation.
3. THE operator path SHALL support a dry run that lists the exact tickers and
   expected active-universe count after reconciliation.
4. WHEN reconciliation is explicitly applied, THE system SHALL mark confirmed
   out-of-scope rows inactive without deleting historical data.
5. THE reconciliation operation SHALL be idempotent and SHALL not deactivate a
   canonical ticker or overwrite live collection data.
6. AFTER reconciliation, manifest task counts and coverage denominators SHALL
   use only active, in-scope tickers.

### Requirement 5: Current Versus Stale Dashboard State

**User story:** As a user, I want to understand whether metrics belong to today’s
run or an older completed publication.

#### Acceptance criteria

1. WHEN the current run date differs from the latest publication date, THE
   dashboard SHALL label the publication metrics as stale and show their date.
2. THE dashboard SHALL not display an old `analyzed/eligible` count as if it
   belonged to the current run.
3. WHEN the current run is degraded, THE summary SHALL name the degraded step
   and state that eligible-ticker analysis continued.
4. WHEN the current run is blocked before analysis, THE summary SHALL state that
   analysis was not reached and identify the blocking step.
5. Existing recommendations from the latest completed publication SHALL remain
   visible, but SHALL be clearly separated from current-run health.

### Requirement 6: Production Recovery Verification

**User story:** As the product owner, I want evidence that the correction works
in production and produces a current, explainable result.

#### Acceptance criteria

1. BEFORE deployment, all backend, frontend, and infrastructure tests relevant
   to the change SHALL pass locally.
2. AFTER deployment, a fresh production workflow SHALL reach the analyzer even
   if an optional collector is degraded.
3. THE new workflow and publication artifacts SHALL contain matching current
   dates and internally consistent counters.
4. THE production readiness artifact SHALL show stale or missing-price tickers
   as individually excluded.
5. THE production workflow SHALL expose partial aggregate price/news coverage as
   degraded rather than globally blocked.
6. IF no recommendation passes the AI review gate, THEN every withheld current
   recommendation SHALL contain a valid rationale or an explicit
   `invalid_response` incident.
