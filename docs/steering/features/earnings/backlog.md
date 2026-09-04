# Earnings Charter Backlog

Tasks are ordered by dependency. Mark an item complete only after its code,
tests, deployment, and production verification requirements are satisfied.

## 1. Calendar correctness

- [x] EARN-1.1 Add regression tests proving the earnings calendar UI does not
  depend on `top-picks/latest.json` and resolves the dedicated earnings artifact.
- [x] EARN-1.2 Change the independent earnings schedule to scan the full active
  watchlist and prevent a first-50 alphabetical artifact overwrite.
- [x] EARN-1.3 Remove the 20-event publication truncation or label the retained
  top-picks field as a summary while keeping the dedicated calendar complete.
- [x] EARN-1.4 Update the frontend earnings tab to load the dedicated normalized
  artifact, display its freshness/status/warnings, and retain the most recent
  successful in-page dataset when collection is transiently degraded. Preserve
  the dividend tab's existing source until dividend aggregation is corrected.
- [ ] EARN-1.5 Add backend, frontend contract, and CDK regression tests for full
  coverage, uncapped rendering, partial-run publication safety, and stale data.
- [x] EARN-1.6 Deploy through `main`; verify the public calendar contains all
  collected watchlist events and no partial schedule replaces the full artifact.
  - Commit `d991981`; deployment run `33436735732` passed all CI/CD and smoke
    checks on 2026-08-31.
  - Full-watchlist refresh run `33438509558` selected 907 tickers, collected 123
    Finnhub events, and reported no failed tickers.
  - The public artifact reported `selected_ticker_count=907` and
    `event_count=123`; the deployed frontend bundle referenced
    `calendar/normalized/earnings/latest.json`.
  - The same verification found zero events through 2026-09-08, confirming that
    provider reconciliation in Section 2 remains the next correctness blocker.

## 2. Multi-provider upcoming-event reconciliation

- [x] EARN-2.1 Add the Alpha Vantage global `EARNINGS_CALENDAR` adapter using one
  budgeted request and preserve its raw S3 snapshot.
  - Commit `b7d17de`; deployment run `33471365380` passed CI/CD and both live
    smoke checks on 2026-09-01.
  - Full-watchlist verification run `33471636989` used one Alpha Vantage call,
    read 1,717 global rows, and retained 250 watchlist observations with fiscal
    period, estimate, currency, and report-time fields in the raw artifact.
  - The merged normalized artifact contains 373 events for 907 active tickers,
    including 36 events from 2026-09-01 through 2026-09-08. Provider-date
    conflicts remain explicit and are addressed by EARN-2.2 through EARN-2.5.
- [x] EARN-2.2 Define provider-observation and canonical-event schemas with
  confidence, fiscal-period identity, supersession, and conflict provenance.
  - `EarningsProviderObservation` retains immutable provider fields, fiscal
    identity, observation time, raw data, and supersession lineage.
  - `CanonicalEarningsEvent` requires observation provenance and rejects a
    selected date whenever provider dates remain conflicting.
- [x] EARN-2.3 Merge Finnhub and Alpha Vantage observations without
  last-write-wins; add exact-match, single-source, and conflicting-date tests.
  - Exact provider date matches collapse to one `confirmed` event while keeping
    both observation IDs. Dates from different providers within the configured
    14-day window remain explicit `conflicting` candidates; well-separated
    quarterly dates remain independent `single_source` events.
  - Reconciliation status, confidence, fiscal identity, estimates, canonical
    IDs, candidate dates, and observation provenance persist to DynamoDB and the
    normalized calendar artifact.
  - Commit `f6486c0`; deployment run `33500610382` and full-watchlist refresh
    run `33500977465` passed on 2026-09-01. The public artifact contained 371
    single-source rows and one two-date conflict (`ARQQ`, 2026-12-07 versus
    2026-12-09), with zero duplicate provider/date rows. Provider health
    correctly reported 250 Alpha Vantage and 123 Finnhub watchlist events.
- [x] EARN-2.4 Add bounded yfinance/company-source confirmation for conflicting
  events occurring within the next seven days.
  - Conflict confirmation deduplicates by ticker, queries yfinance only from
    today through seven days ahead, and caps the work at 10 tickers per run.
    Candidate support and the confirmation provider persist without silently
    resolving the disagreement; out-of-horizon conflicts trigger no calls.
  - Commit `442c240`; all 491 backend tests and Ruff passed locally, and
    deployment run `33530783438` passed the complete CI/CD and production smoke
    suite on 2026-09-01. Full-watchlist refresh run `33531210445` reported zero
    confirmation calls because the only live conflict (`ARQQ`, December 7 versus
    December 9) was outside the seven-day horizon; the public artifact retained
    both candidates and its conflict warning.
- [x] EARN-2.5 Expose conflict and coverage metrics, artifact warnings, and UI
  confidence badges.
  - Normalized artifacts publish canonical, confirmed, company-confirmed,
    single-source, conflicting-candidate, conflict-group, and unreconciled
    counts; conflicts also produce a user-visible artifact warning.
  - The collector emits reconciliation CloudWatch metrics, and the calendar
    displays the conflict-group count plus confirmed, conflicting,
    single-source, and unreconciled badges with candidate-date tooltips.
  - Commit `f14aea9`; deployment run `33504221427` passed the complete CI/CD and
    production smoke suite on 2026-09-01. Full-watchlist refresh run
    `33530052215` then published 373 rows representing 372 canonical events:
    371 single-source events and one two-candidate `ARQQ` conflict, with the
    expected user-visible conflict warning. The deployed frontend bundle was
    verified to contain the date-confidence metric, column, and badge labels.
- [x] EARN-2.6 Deploy and verify representative near-term tickers against at
  least two sources; record unresolved conflicts rather than hiding them.
  - Finnhub collection now reserves an independent first-seven-days request so
    its 1,500-row long-range response cap cannot exclude imminent events. The
    near and long ranges have separate health diagnostics and are deduplicated
    before reconciliation.
  - Commit `4d3d106`; all 492 backend tests and Ruff passed locally, and
    deployment run `33620327559` passed the complete CI/CD and production smoke
    suite on 2026-09-02.
  - Full-watchlist refresh run `33620734187` selected 907 tickers. The near-term
    Finnhub request returned 190 raw events and 33 watchlist observations, while
    the long-range request returned its 1,500-row cap and 123 watchlist rows.
    `AI` (September 2), `AMBA` (September 3), and `BRZE` (September 8) matched
    Alpha Vantage exactly and became high-confidence confirmed events with both
    observation IDs.
  - `FCEL` (September 2 versus September 7) and `CPRT` (September 3 versus
    September 9) remained explicit conflicts. Both candidate dates and source
    observation IDs are published; bounded yfinance confirmation returned no
    deciding evidence, and the artifact warns that two conflicts remain.

## 3. Historical earnings foundation

- [x] EARN-3.1 Audit per-ticker quarterly history coverage and publish a dated
  coverage artifact with quota/budget skips counted as incomplete.
  - Commits `d2176a4` and `050f141` add a DynamoDB range audit, per-ticker
    coverage/field counts, explicit collection outcomes, scoped task artifacts,
    full-watchlist detection, and CloudWatch coverage/skip metrics. A skipped
    per-ticker provider attempt is also counted as a failed manifest ticker.
  - All 498 backend tests passed locally. Deployment run `33622987731` passed
    backend, frontend, infrastructure, CDK deployment, API smoke, and static
    artifact smoke gates on 2026-09-02.
  - Production refresh run `33623360131` audited all 907 active tickers and
    published both the dated and latest artifacts. The live result reports 841
    complete, 2 partial, and 64 missing histories: 92.72% of the active universe
    has at least eight stored quarters. Field counters also make the current
    revenue-estimate gap explicit rather than treating those rows as complete
    evidence.
- [x] EARN-3.2 Make history backfill resumable and fair across the watchlist;
  never report a quota-skipped chunk as fully successful.
  - Commit `7ef0084` preserves provider-specific failure reasons on persistent
    manifest tasks, applies the longer Alpha Vantage quota retry delay, and
    proves that a delayed earnings chunk does not block later chunks. Commit
    `7edf337` adds the operator backfill checkpoint and explicit exit semantics.
  - The manual backfill publishes `offset`, `resume_offset`, `has_more`, provider
    attempts, collection outcomes, and coverage to dated and latest checkpoint
    artifacts. Writes remain idempotent, dry runs do not mutate storage, and a
    provider-budget skip exits non-zero instead of printing false success.
  - All 505 backend tests, frontend tests/lint/build, and Ruff passed locally;
    deployment runs `33624390909` and `33679710003` passed the complete CI/CD,
    CDK, API-smoke, and artifact-smoke path on 2026-09-02.
  - Controlled production run `33680281525` used a zero-call Alpha Vantage
    budget for `MSFT,SA,FCEL`, failed intentionally with all three tickers marked
    `budget_exhausted`, and retained `resume_offset=0`. Recovery run
    `33680392841` resumed the same scope with three calls, stored 61 events, and
    completed with all three tickers collected and `resume_offset=3`.
- [x] EARN-3.3 Store fiscal period, EPS/revenue estimates and actuals, surprise,
  guidance evidence, revisions, source URLs, and observation timestamps.
  - Commit `7edf337` adds typed guidance and estimate-revision evidence and
    preserves fiscal identity, EPS/revenue estimate and actual fields,
    independent surprise percentages, provider observation IDs, source URL
    sets, and observation/collection timestamps through collection, DynamoDB,
    analysis serialization, artifacts, and the frontend contract.
  - Empty provider fields remain empty: Alpha Vantage quarterly history supplies
    EPS and fiscal identity but no revenue, guidance, or revision history, so the
    system measures those gaps instead of inferring values.
  - Production recovery run `33680392841` published 61 scoped events. The live
    artifact was verified with fiscal period/quarter, EPS estimate and actual,
    source URL, observation timestamp, and stable provider observation ID; its
    scoped coverage artifact reports 20 distinct quarters each for FCEL, MSFT,
    and SA and explicit zero counts for unavailable revenue/guidance/revisions.
- [ ] EARN-3.4 Backfill at least eight quarters for supported active tickers and
  verify coverage thresholds in production.
  - Production full-watchlist audit run `33680766137` completed successfully on
    2026-09-02 after the first recovery batch. Coverage is now 844 complete, 2
    partial, and 61 missing histories: 93.05% of 907 active tickers meet the
    eight-quarter threshold. The remaining 63 are being processed in bounded,
    resumable batches rather than exceeding provider quotas.
  - Bounded run `33755323764` stored 384 reports for 20 incomplete tickers on
    2026-09-03; 19 reached the threshold and ARQQ remains honestly partial with
    seven available quarters. Full audit `33755596924` then reported 863
    complete, 3 partial, and 41 missing histories (95.15%). Run `33755879357`
    used the final four calls in that day's allowance to store another 80
    reports; all four scoped tickers reached the threshold. A new full audit is
    still required before recording the next global total.
- [x] EARN-3.5 Add monitoring for history coverage regression and provider quota
  exhaustion.
  - Commit `0c23119` emits a universe coverage percentage only for full-watchlist
    runs, separately counts actual provider rate/quota blocks, and keeps
    operator-configured budget exhaustion distinct. Targeted repair chunks
    cannot distort the universe coverage alarm.
  - CloudWatch alarms cover coverage below 90%, a missing 26-hour coverage
    signal, and one or more provider-quota-blocked tickers. The dashboard exposes
    coverage, incomplete ticker count, and quota-exhaustion count.
  - All 507 backend tests, 25 infrastructure tests, and the CI-pinned Ruff gate
    passed locally. Deployment run `33755419984` passed the full backend,
    frontend, infrastructure, CDK deployment, API smoke, and artifact smoke path
    on 2026-09-03.

## 4. Event-study implementation

- [x] EARN-4.1 Add timing-aware session mapping for before-open, after-close,
  unknown, weekends, and market holidays.
  - Commit `202464f` adds a typed session-boundary contract driven by actual
    ordered trading sessions rather than weekday arithmetic. Before-open maps to
    the same eligible session, after-close to the next session, and weekends or
    holidays advance naturally to the next observed session.
  - Unknown timing on a trading day retains both candidates and selects neither;
    unknown timing on a non-trading day collapses safely to one boundary.
    Missing future sessions are insufficient evidence, never a zero return.
  - All 514 backend tests and Ruff passed locally. Deployment run `33756120577`
    passed the full CI/CD, CDK, API smoke, and artifact smoke path on 2026-09-03.
- [x] EARN-4.2 Compute raw, SPY-adjusted, and sector-adjusted multi-window returns
  plus abnormal volume using split-adjusted prices.
  - Commit `f84ad8d` computes the five required return windows from adjusted
    close only, aligns SPY and named sector benchmarks on exact sessions, and
    measures event volume against exactly 20 preceding exchange sessions.
  - Missing benchmark data retains raw returns with reduced evidence quality;
    missing stock boundaries never shift to a nearby date or become zero.
- [x] EARN-4.3 Add deterministic fixtures for splits, missing sessions, unknown
  timing, benchmark gaps, and delisted symbols.
  - Deterministic tests cover split-divergent raw prices, sparse calendars,
    unresolved timing, independent benchmark gaps, and truncated histories.
    All 523 backend tests and Ruff passed locally; deployment run `33790821852`
    passed the complete CI/CD, CDK, API-smoke, and artifact-smoke path on
    2026-09-03.
- [x] EARN-4.4 Publish per-event reaction artifacts and a per-ticker historical
  reaction summary suitable for UI and model features.
  - Commit `47297f8` adds stable reaction IDs, dated/scoped per-event payloads,
    per-ticker histories, canonical-window summary statistics, and a manual
    DynamoDB-to-S3 workflow. Targeted, capped, and offset runs cannot replace
    the full-universe `latest`/`current` paths; dry runs cannot publish.
  - All 532 backend tests and Ruff passed locally. Deployment run `33791490196`
    passed the complete CI/CD and production smoke path on 2026-09-03.
  - Scoped production run `33791977506` exposed a missing-calendar defect where
    old reports could jump to the first stored session. Commit `be7aa01` limits
    inferred closures to seven calendar days and requires both timing candidates
    when an unknown-time report falls on a trading day. All 535 backend tests
    passed locally, and deployment run `33883318538` passed every CI/CD and
    smoke gate on 2026-09-04.
  - Corrected scoped run `33883751614` published 59 traceable AAPL/MSFT/NVDA
    events. All 59 are honestly `insufficient` because current production data
    lacks the historical session/timing evidence needed for these samples; an
    inspected 2021 MSFT payload has no event session, no calculated windows, and
    explicit `no_trading_session_on_or_after_report_date` provenance. Restoring
    those inputs is the production prerequisite for EARN-4.5, not a reason to
    publish guessed returns.
- [ ] EARN-4.5 Deploy and reconcile sampled calculations against independently
  calculated values.
  - Historical-price repair commit `60e72d6` makes missing or shallow
    `stock_history_start_date` metadata trigger a five-year restore and lowers
    that boundary when older rows arrive. Deployment run `33884401830` passed;
    targeted production run `33884819594` restored three S3 archives and
    inserted 2,163 AAPL/MSFT/NVDA rows with no failed ticker.
  - Post-repair event-study run `33911089113` passed and republished all 59
    scoped events. All remain explicitly insufficient because their stored
    historical earnings timing is unknown; the repaired price rows did not
    cause the engine to guess a session boundary.
  - An independent operator verifier now compares stored-price session
    boundaries, five raw return windows, and abnormal volume with the production
    engine. It requires timezone-aware HTTPS timing evidence, fails closed on
    mismatches or incomplete inputs, and can publish a traceable reconciliation
    artifact. Production deployment and execution against the SEC-timestamped
    AAPL 2024-08-01 sample are still required before this task is complete.

## 5. Predictive research and backtesting

- [ ] EARN-5.1 Register an immutable candidate `AnalysisStrategy` and feature
  schema for earnings-event prediction.
- [ ] EARN-5.2 Build cutoff-aware feature snapshots that exclude future results,
  revisions, filings, transcripts, news, and prices.
- [ ] EARN-5.3 Establish simple baselines: no-trade, announcement premium,
  historical-direction, consensus-revision, and market/sector-neutral variants.
- [ ] EARN-5.4 Train/evaluate separate surprise and abnormal-return models using
  expanding-window or walk-forward validation.
- [ ] EARN-5.5 Report calibration, Brier score, directional precision, expected
  return, drawdown, turnover, regime stability, and results after costs.
- [ ] EARN-5.6 Define sample-size, confidence, liquidity, expected-value, and
  drawdown gates for promotion; reject or retain experimental strategies that
  do not pass.

## 6. Shadow operation and product surface

- [ ] EARN-6.1 Run daily predictions for events in the next seven days and freeze
  immutable pre-event shadow artifacts.
- [ ] EARN-6.2 Score predictions after results and price windows become available;
  expose cumulative and rolling live performance without retroactive edits.
- [ ] EARN-6.3 Build the “Earnings opportunities — next 7 days” research page
  with date confidence, estimates, history, expected move, catalysts, risks,
  evidence gaps, and `TRADE`/`WATCH`/`AVOID` classification.
- [ ] EARN-6.4 Add UI tests for confirmed/conflicting dates, sparse history,
  negative expected value, stale predictions, and empty/loading/error states.
- [ ] EARN-6.5 Deploy the research-only page and verify that shadow outputs cannot
  affect top picks, holding reviews, demo trading, or automated consumers.

## 7. Promotion decision

- [ ] EARN-7.1 Review shadow sample size, calibration, net expected value,
  drawdown, regime behavior, provider reliability, and operational cost.
- [ ] EARN-7.2 Record an explicit promote, continue-shadowing, redesign, or reject
  decision with strategy and artifact links.
- [ ] EARN-7.3 If promoted, create a separate backlog for portfolio sizing,
  execution constraints, loss limits, and integration with existing review
  gates; do not silently enable trading from this charter.
