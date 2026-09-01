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
- [ ] EARN-2.2 Define provider-observation and canonical-event schemas with
  confidence, fiscal-period identity, supersession, and conflict provenance.
- [ ] EARN-2.3 Merge Finnhub and Alpha Vantage observations without
  last-write-wins; add exact-match, single-source, and conflicting-date tests.
- [ ] EARN-2.4 Add bounded yfinance/company-source confirmation for conflicting
  events occurring within the next seven days.
- [ ] EARN-2.5 Expose conflict and coverage metrics, artifact warnings, and UI
  confidence badges.
- [ ] EARN-2.6 Deploy and verify representative near-term tickers against at
  least two sources; record unresolved conflicts rather than hiding them.

## 3. Historical earnings foundation

- [ ] EARN-3.1 Audit per-ticker quarterly history coverage and publish a dated
  coverage artifact with quota/budget skips counted as incomplete.
- [ ] EARN-3.2 Make history backfill resumable and fair across the watchlist;
  never report a quota-skipped chunk as fully successful.
- [ ] EARN-3.3 Store fiscal period, EPS/revenue estimates and actuals, surprise,
  guidance evidence, revisions, source URLs, and observation timestamps.
- [ ] EARN-3.4 Backfill at least eight quarters for supported active tickers and
  verify coverage thresholds in production.
- [ ] EARN-3.5 Add monitoring for history coverage regression and provider quota
  exhaustion.

## 4. Event-study implementation

- [ ] EARN-4.1 Add timing-aware session mapping for before-open, after-close,
  unknown, weekends, and market holidays.
- [ ] EARN-4.2 Compute raw, SPY-adjusted, and sector-adjusted multi-window returns
  plus abnormal volume using split-adjusted prices.
- [ ] EARN-4.3 Add deterministic fixtures for splits, missing sessions, unknown
  timing, benchmark gaps, and delisted symbols.
- [ ] EARN-4.4 Publish per-event reaction artifacts and a per-ticker historical
  reaction summary suitable for UI and model features.
- [ ] EARN-4.5 Deploy and reconcile sampled calculations against independently
  calculated values.

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
