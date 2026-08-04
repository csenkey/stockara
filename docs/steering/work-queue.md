# Stockara Work Queue

This is the canonical priority queue for Codex and Claude. It exists to prevent overlapping or conflicting backlog documents.

Other backlog-like files are reference material unless this file links to them as the active source for a specific feature.

## Authority Rules

- Use this file to decide what is currently on top.
- Use `docs/steering/features/*/backlog.md` for executable feature-specific task breakdowns.
- Deleted historical backlog and handoff files are not active sources. Use this queue and the linked feature backlogs.
- When priorities change, update this file first.
- Do not add new work queues elsewhere. Add feature-level task details under `docs/steering/features/`.

## Current Priority Order

### 1. Daily Pipeline Stability And Evidence Recovery

Status: Active production resilience correction. The 2026-08-03 scheduled run
was blocked before analysis when `CollectNews` exhausted its 300-second Lambda
timeout. The correction is specified in Kiro style under the linked resilience
feature documents.

Goal: Make the once-daily Stockara publication stable, explainable, and useful even when optional data is degraded, by replacing independent schedule timing assumptions with one observable daily workflow.

Canonical feature docs:

- `docs/steering/features/daily-pipeline-stability/requirements.md`
- `docs/steering/features/daily-pipeline-stability/design.md`
- `docs/steering/features/daily-pipeline-stability/backlog.md`
- `docs/steering/features/daily-pipeline-resilience/requirements.md`
- `docs/steering/features/daily-pipeline-resilience/design.md`
- `docs/steering/features/daily-pipeline-resilience/backlog.md`

Completed stability work:

- Done: publish a daily data-readiness artifact that identifies exactly which tickers/data types are missing or degraded and why.
- Done: detect production metadata drift, especially when DynamoDB active metadata disagrees with the clean repository seed/audit.
- Done: keep empty daily publications explainable from the public dashboard without needing CloudWatch access.
- Done: provide operator-safe repair workflows for the detected missing or degraded data.
- Done: publish lower-confidence and fallback-preview suggestions separately from decision-grade picks.
- Done: make `stockara-daily-pipeline` the daily Step Functions orchestrator for publication.

Next executable item:

- Execute the Kiro-style daily-pipeline-resilience backlog in order: bound news
  collection, make optional collector failures degraded/non-blocking, improve
  status attribution, reconcile the active universe, and verify a current
  production publication end to end.
- Deploy the typed review contract and targeted evidence repair loop, then
  replay the affected malformed reviews and verify every withheld recommendation
  has a reviewer rationale or an explicit invalid-response incident.
- Add bounded SEC filing-text substance and durable source-backed fundamental
  collectors for gaps currently classified as `feature_missing`.
- Publish a compact incident artifact and optionally connect actionable alarms
  to SNS email after the production repair loop is verified.
- Optimize the optional evidence-repair critical path and degraded price-chunk
  retries without weakening the verified terminal-publication guarantees.
- Resume backtest-support work after these bounded stability optimizations, or
  explicitly promote backtesting if daily runtime is acceptable for Phase 1.

Why this matters:

- The current dashboard can show zero picks even when the pipeline ran, because fallback or review-gated actionable recommendations are withheld.
- Today’s production artifact reports unresolved metadata rows even though the repository metadata audit is clean, so the system needs drift detection and repair visibility.
- We analyze once per day; a single organizer is easier to reason about and cheaper than near-real-time polling plus repeated gated analyzer attempts.

### 2. Backtest Support With Shadowed Portfolios

Status: Paused behind daily pipeline stability. Offline framework foundation implemented; decision-grade data ingestion and historical recommendation replay remain open.

Goal: Compare versioned `AnalysisStrategy` snapshots using deterministic historical simulations, shadow portfolios, ETF baselines, and S3 artifacts.

Canonical feature backlog:

- `docs/steering/features/backtest-support-with-shadowed-portfolios/backlog.md`

Completed setup tasks:

- Define the first supported backtest configuration schema.
- Define analysis strategy steering and registry files.
- Define `AnalysisStrategy` manifest models.
- Freeze current hardcoded analyzer behavior as the first baseline strategy.
- Create the initial `backend/src/backtesting/` package with offline-only replay, accounting primitives, shadow helpers, S3 artifact path planning, fixture market data loader, and tests.

Next executable items:

- Implement full initial allocation from historical price-supported ticker universes.
- Add S3-backed historical OHLCV/ETF loader and artifact writer.
- Add cached recommendation artifact loading keyed by analysis strategy, ticker, date, model, prompt version, evidence hash, and schema version.

### 3. Calendar and Historical Evidence Foundation

Status: Active supporting priority.

Goal: Make earnings/dividend data reliable enough for both daily analysis and decision-grade backtesting.

Source detail:

- Calendar follow-up tasks are maintained in this queue until promoted to a dedicated feature backlog.

Next executable items:

- Roll out dividend-calendar backfill safely after Alpha Vantage throttling.
- Verify the deployed full-watchlist earnings query and bounded rotating
  fallback produce upcoming events; alert on continued zero-event degraded runs.
- Build an S3 calendar data lake for earnings and dividends.
- Index normalized calendar events into DynamoDB for analysis.
- Backfill historical earnings and dividend events.

Why this matters:

- Backtest promotion should not rely on reduced-evidence runs.
- Earnings/dividend context is required by the S3-backed backtest evidence snapshot.

Completed reliability work:

- Done locally: remove the fixed first-50 ticker scope from the daily earnings
  workflow, query the forward calendar against the full active watchlist, add a
  quota-bounded rotating per-ticker fallback, and expose provider diagnostics
  instead of reporting empty results as success.

### 4. Phase 1 UI and Static Artifact Refinement

Status: Active secondary priority; ticker-card related-news refinement is complete.

Goal: Improve public/read-only decision-support surfaces after the analysis pipeline and data artifacts are reliable.

Source detail:

- Active related-news work is tracked in
  `docs/steering/features/ticker-card-related-news/backlog.md`.
- Historical UI tasks remain reference context only; promote other active work
  into a feature backlog before implementation.

Representative open items:

- Update the frontend to render top picks and sell alerts from static JSON artifacts.
- Improve published recommendation detail cards for trust and usability.
- Give withheld AI recommendations the same decision-support context as published picks.
- Add frontend tests or build checks for rendering empty, loading, successful, and stale-artifact states.

## Completed Or Historical Sources

### Phase 1 Production Analysis Gating

Status: Done and deployed.

Goal: Daily analysis should run only after collection coverage gates are satisfied, then publish with explicit coverage and suppression behavior.

Deployment:

- Commit `087bab3` deployed successfully through GitHub Actions run `28867910920`.
- CI passed local-equivalent checks, CDK deploy, deployed API smoke test, and static artifact smoke test.

Why this mattered:

- Backtesting is valuable only if the production analyzer/data pipeline has clear gating, provenance, and publication semantics.
- This item also helped define the first baseline `AnalysisStrategy`.

## Maintenance Checklist

When a feature or priority changes:

- Update this file.
- Update the relevant feature backlog under `docs/steering/features/`.
- Keep active task detail in the linked feature backlogs; do not create replacement top-level backlog files.
- Avoid creating new top-level backlog files.

## Stable Release Operations

Status: Implemented.

- Stable production checkpoints use immutable annotated tags named `stockara-X.Y`.
- GitHub Actions workflow `.github/workflows/deploy-stable.yml` validates a selected stable tag and deploys it to `prod` through the same checks as the normal deployment.
- Rollbacks are performed by selecting an earlier stable tag in that workflow.
- The current stable checkpoint is `stockara-1.0`; future stable releases must use a new tag.
