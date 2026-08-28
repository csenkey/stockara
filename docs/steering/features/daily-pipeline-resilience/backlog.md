# Daily Pipeline Resilience Backlog

## 2026-08-27 Architecture Review Hardening

- [x] Verify current OpenAI model identifiers against the official catalog;
  retain valid Luna/Terra workload roles and the existing fallback alarm.
- [x] Re-raise fatal news/evidence exceptions and add handler regression tests.
- [x] Persist missing-worker manifest failures and count them as exhausted.
- [x] Guard minimal workflow reports and log silent backfill failures.
- [x] Bind monitoring to deployed CDK constructs and cover all twelve Lambdas.
- [x] Replace single-gap paging with an affected-universe percentage threshold.
- [x] Omit unsupported non-default GPT-5 Chat Completions temperatures, use
  word-boundary sentiment matching, expose actionable frontend fetch errors,
  and add calendar fallback behavior.
- [x] Fail deployment smoke testing on hard operational failures.
- [x] Replace the undeployable reserved-concurrency reporter with FIFO SQS
  serialization after production rejected the reservation at the account quota.
- [ ] Deploy through `main`, monitor CI/CD to green, and smoke test production.
- [ ] Follow a fresh production execution through terminal status and current
  publication; do not close the ongoing recovery incident on deployment alone.

## 2026-08-26 Recovery Release

- [x] Reproduce payload growth using the synthesized parallel-state data paths;
  bound branch inputs, strip integration metadata, and discard unused repair results.
- [x] Add an independent reporter, terminal-event subscription, scheduled
  reconciliation, serialized report ordering, and scoped IAM.
- [x] Test failure attribution, duplicate/delayed events, original run dates,
  deadline boundaries, and preservation of recommendation artifacts.
- [x] Show overdue updates when both artifacts are stale; distinguish failures
  before/after analysis; run frontend regression tests in deployment CI.
- [x] Inspect terminal error/history tail and notification configuration;
  expose explicitly requested status reconciliation without running analysis.
- [x] Complete final local suites and production packaging/synthesis checks:
  459 backend tests, 21 infrastructure tests, 6 frontend tests, lint/build,
  Docker-backed CDK synthesis, and rendered overdue-dashboard check passed.
- [ ] Deploy through `main` and pass deployed smoke tests.
- [ ] Verify independent reporting against the actual August 25 failed execution.
- [ ] Start a fresh execution on the corrected definition and verify current
  publication, accurate terminal status, and the public dashboard.

Do not close the incident based only on a green deployment. Do not backdate new
analysis or overwrite historical recommendations to fill the missing days.

This is the Kiro-style executable task plan for the requirements and design in
this feature directory. Tasks are ordered by dependency and should be completed
test-first where specified.

## 1. Reproduce And Lock The Failure Contract

- [x] 1.1 Add a news-collector test proving `repair_news` without explicit
  tickers currently ignores or mishandles the intended ticker bound.
  - Covers Requirement 1.1.
- [x] 1.2 Add tests for provider request budgets and a per-run new-article cap.
  - Covers Requirements 1.2 and 1.3.
- [x] 1.3 Add a fake Lambda context test that reaches the safety margin during
  article summarization and expects a typed partial result.
  - Covers Requirements 1.4–1.6.
- [x] 1.4 Add CDK tests proving an optional `CollectNews` failure reaches the
  analyzer while a required failure remains blocked.
  - Covers Requirements 2.1, 2.4, and 2.6.

## 2. Implement Bounded News Collection

- [x] 2.1 Add deterministic rotating active-ticker resolution for untargeted
  `repair_news` requests.
  - Depends on 1.1.
  - Covers Requirement 1.1.
- [x] 2.2 Normalize and enforce provider request budgets in each provider adapter.
  - Depends on 1.2.
  - Covers Requirement 1.2.
- [x] 2.3 Add a configurable new-article processing limit, deterministic ordering,
  and deferred counters.
  - Depends on 1.2.
  - Covers Requirements 1.3 and 1.6.
- [x] 2.4 Add remaining-time checks before model calls and return clean partial
  results with continuation metadata.
  - Depends on 1.3 and 2.3.
  - Covers Requirements 1.4–1.7.
- [x] 2.5 Add idempotent partial-rerun tests proving stored articles are not
  regenerated or overwritten.
  - Depends on 2.4.
  - Covers Requirement 1.7.

## 3. Make Optional Collection Non-Blocking

- [x] 3.1 Add typed degraded Pass states for news timeout/error paths.
  - Depends on 1.4.
  - Covers Requirements 2.1 and 3.1–3.2.
- [x] 3.2 Add independent degraded catches to earnings, dividend, and evidence
  branches so the Parallel state can complete.
  - Covers Requirement 2.2.
- [x] 3.3 Pass optional degradation summaries into analyzer data quality and
  readiness artifacts.
  - Depends on 3.1 and 3.2.
  - Covers Requirements 2.3 and 3.5.
- [x] 3.4 Add end-to-end workflow tests proving eligible tickers are analyzed and
  no-eligible-ticker runs remain suppressed.
  - Depends on 3.3.
  - Covers Requirements 2.4–2.6.

## 4. Improve Operational Truth

- [x] 4.1 Extend workflow status with execution status, business status,
  failed/degraded step names, retryability, and `analysis_reached`.
  - Depends on 3.1.
  - Covers Requirements 3.1–3.5.
- [x] 4.2 Update dashboard types and rendering to separate current-run metrics
  from latest-publication metrics.
  - Depends on 4.1.
  - Covers Requirements 5.1–5.5.
- [x] 4.3 Validate stale publication, optional degradation, and
  blocked-before-analysis states.
  - Depends on 4.2.
  - The frontend has no test runner; these states are contract-tested in the
    backend and infrastructure suites and type-checked by the frontend build.

## 5. Reconcile The Active Universe

- [x] 5.1 Add dry-run output listing exact `active_not_in_seed` tickers and
  projected active-universe counts.
  - Covers Requirements 4.1–4.3.
- [x] 5.2 Add an explicit idempotent apply flag that marks only confirmed
  out-of-scope rows inactive while preserving historical data.
  - Depends on 5.1.
  - Covers Requirements 4.4–4.5.
- [x] 5.3 Add tests proving canonical tickers and live collection fields cannot be
  changed by reconciliation.
  - Depends on 5.2.
- [x] 5.4 Verify manifest task counts and coverage denominators use the reconciled
  active universe.
  - Depends on 5.2.
  - Covers Requirement 4.6.

## 6. Verify And Release

- [x] 6.1 Run backend lint and the full backend test suite.
- [x] 6.2 Run frontend lint, tests where available, and production build.
- [x] 6.3 Run infrastructure tests and CDK synth.
  - Covers Requirement 6.1.
- [ ] 6.4 Review the production reconciliation dry run and apply only the confirmed
  out-of-scope ticker set.
  - Depends on Section 5.
- [ ] 6.5 Commit to `main`, deploy through CI/CD, and monitor deployment smoke
  tests to green.
- [ ] 6.6 Start and monitor a fresh production daily workflow through terminal
  publication.
  - Depends on 6.5.
  - Covers Requirements 6.2–6.5.
  - A 2026-08-27 shadow run succeeded and published; the subsequent scheduled
    run exposed an unguarded optional analyzer response path in the publication
    Choice state. Guard optional `workflow_decision` and `stage` fields before
    repeating terminal verification.
- [ ] 6.7 Validate current workflow, top-picks, sell-alerts, and data-readiness
  artifacts for matching dates and internally consistent counters.
  - Depends on 6.6.
- [ ] 6.8 Validate every current withheld recommendation has a complete review
  rationale or an explicit invalid-response incident.
  - Depends on 6.6.
  - Covers Requirement 6.6.
