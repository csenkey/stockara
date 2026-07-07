# Stockara Work Queue

This is the canonical priority queue for Codex and Claude. It exists to prevent overlapping or conflicting backlog documents.

Other backlog-like files are reference material unless this file links to them as the active source for a specific feature.

## Authority Rules

- Use this file to decide what is currently on top.
- Use `docs/steering/features/*/backlog.md` for executable feature-specific task breakdowns.
- Use `BACKLOG.md`, `docs/PHASE1_MUST_HAVE_BACKLOG.md`, and `docs/NEXT_SESSION_HANDOFF.md` as historical/reference material only.
- When priorities change, update this file first.
- Do not add new work queues elsewhere. Add feature-level task details under `docs/steering/features/`.

## Current Priority Order

### 1. Backtest Support With Shadowed Portfolios

Status: Active implementation. Offline framework foundation implemented; decision-grade data ingestion and historical recommendation replay remain open.

Goal: Compare versioned `AnalysisStrategy` snapshots using deterministic historical simulations, shadow portfolios, ETF baselines, and S3 artifacts.

Canonical feature backlog:

- `docs/steering/features/backtest-support-with-shadowed-portfolios/backlog.md`

First milestone:

- One bounded 365-day benchmark window, initially 2022 for a stress/bearish year.
- 20 deterministic portfolios starting with USD 10,000.00 for budget-controlled comparison runs.
- Freeze current hardcoded analyzer behavior as `analysis_strategy_current`.
- Compare one candidate `AnalysisStrategy` against the current baseline.
- Store all artifacts in S3.

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

### 2. Calendar and Historical Evidence Foundation

Status: Active supporting priority.

Goal: Make earnings/dividend data reliable enough for both daily analysis and decision-grade backtesting.

Source detail:

- Historical detailed backlog: `BACKLOG.md`, `Critical calendar follow-up`

Next executable items:

- Roll out dividend-calendar backfill safely after Alpha Vantage throttling.
- Improve calendar provider-health diagnostics.
- Build an S3 calendar data lake for earnings and dividends.
- Index normalized calendar events into DynamoDB for analysis.
- Backfill historical earnings and dividend events.

Why this matters:

- Backtest promotion should not rely on reduced-evidence runs.
- Earnings/dividend context is required by the S3-backed backtest evidence snapshot.

### 3. Phase 1 UI and Static Artifact Refinement

Status: Proposed/secondary.

Goal: Improve public/read-only decision-support surfaces after the analysis pipeline and data artifacts are reliable.

Source detail:

- Historical detailed backlog: `BACKLOG.md`, section `Phase 1: Daily Top Picks and Risk Alerts`

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

### Phase 1 Must-Have Backlog

File: `docs/PHASE1_MUST_HAVE_BACKLOG.md`

Status: Historical completed checklist. P0, P1, P2, and P3 items shown there are marked done as of the latest file contents.

Use it for:

- Understanding why current data-quality rules exist.
- Finding references to tests and modules that enforce Phase 1 quality.

Do not use it for:

- Choosing the next active task.

### Next Session Handoff

File: `docs/NEXT_SESSION_HANDOFF.md`

Status: Historical handoff dated 2026-07-05.

Use it for:

- Reconstructing the past news-source hardening/deployment context.

Do not use it for:

- Current priority ordering.

## Maintenance Checklist

When a feature or priority changes:

- Update this file.
- Update the relevant feature backlog under `docs/steering/features/`.
- Keep `BACKLOG.md` as historical detail unless intentionally retiring sections.
- Avoid creating new top-level backlog files.
