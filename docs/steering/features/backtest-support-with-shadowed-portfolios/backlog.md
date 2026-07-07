# Implementation Plan: Backtest Support With Shadowed Portfolios

## Overview

Implement an offline-first historical portfolio simulator that tests versioned Stockara analysis strategies from 2022-01-01 onward. The first milestone should use S3-backed artifacts and include historical OHLCV, ETF, news, earnings, dividend, inactive/delisted ticker, point-in-time provenance inputs, immutable analysis strategy manifests, and portfolio policies for decision-grade runs.

## Tasks

- [x] 1. Spec alignment and scope guardrails
  - [x] 1.1 Link this feature from `docs/steering/work-queue.md` and agent steering docs as a future portfolio-evaluation initiative.
    - Emphasize that this is not real-user portfolio management.
    - Preserve traceability to current steering docs and product rules where trading/accounting rules overlap.
    - _Requirements: 10.1, 10.2, 10.3, 10.6_

  - [x] 1.2 Define the first supported backtest configuration schema.
    - Create `backend/src/backtesting/config.py`.
    - Include date range, seed, portfolio count, commission rate, analysis strategy IDs, portfolio policy IDs, ETF baselines, execution timing, S3 prefix, and shadow windows.
    - Validate defaults: start date `2022-01-01`, portfolio count `20`, initial capital `10000.00`, commission rate `0.01`.
    - Default to fixture-only recommendation replay and reduced-evidence labeling to avoid unintended AI cost.
    - _Requirements: 3.1, 3.2, 4.1, 4.6, 6.1, 7.4, 8.2, 9.2_

  - [x] 1.3 Define the analysis strategy steering and registry files.
    - Create `docs/steering/analysis-strategies/README.md`.
    - Create `docs/steering/analysis-strategies/analysis_strategy_schema.md`.
    - Create `docs/steering/analysis-strategies/strategy_registry.md`.
    - Document the distinction between `AnalysisStrategy` and `PortfolioPolicy`.
    - _Requirements: 4.1, 4.2, 4.10_

- [x] 2. Backtesting package and core models
  - [x] 2.1 Create `backend/src/backtesting/` package.
    - Add `__init__.py`, `models.py`, `simulator.py`, `analysis_strategy.py`, `portfolio_policies.py`, `shadows.py`, `metrics.py`, `reporting.py`, `data_loader.py`, `portfolio_generator.py`, and `cli.py`.
    - Keep implementation separate from authenticated portfolio services.
    - _Requirements: 11.1, 11.2, 11.3_

  - [x] 2.2 Define Pydantic or dataclass models for simulation state.
    - Define `BacktestPortfolio`, `BacktestHolding`, `BacktestTransaction`, `BacktestSnapshot`, `DecisionShadow`, `BacktestRunSummary`, and `BacktestMetricSummary`.
    - Use Decimal-compatible fields for cash, prices, commissions, and values.
    - _Requirements: 6.1, 6.5, 6.6, 7.2, 9.1_

  - [x] 2.3 Define `AnalysisStrategy` manifest models.
    - Include strategy ID, status, parent ID, git commit, preselection flow, predicates, filters, scoring weights, candidate limits, prompt templates, AI models, review gates, publication rules, and evidence usage.
    - Add manifest validation for required fields and stable IDs.
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 2.4 Freeze the current hardcoded analyzer behavior as the first baseline strategy.
    - Create `configs/analysis-strategies/analysis_strategy_current.yaml`.
    - Register it as baseline in steering docs.
    - Include current preselection, prompt, model, review, suppression, and publication behavior as accurately as current code allows.
    - _Requirements: 4.1, 4.2, 4.6, 4.10_

- [ ] 3. Historical price and ETF data loading
  - [x] 3.1 Implement a historical market data loader interface.
    - Load ticker metadata and daily OHLCV rows from existing repositories or local fixtures.
    - Require an `as_of` or decision date filter.
    - Return clear incomplete-data states.
    - _Requirements: 1.1, 1.5, 1.6, 1.7, 2.1_

  - [ ] 3.2 Add ETF instrument support.
    - Add metadata handling for ETF instruments separate from stock watchlist instruments.
    - Ensure at least SPY or VOO can be loaded as a baseline.
    - _Requirements: 1.3, 1.4, 7.2, 7.3_

  - [ ] 3.3 Add tests for price-window and missing-data behavior.
    - [x] Prove future price rows are excluded.
    - Prove stale/missing prices mark valuations incomplete.
    - Prove ETF data follows the same valuation rules as stocks.
    - _Requirements: 1.5, 1.6, 2.1, 9.6_

- [ ] 4. Deterministic starting portfolio generation
  - [ ] 4.1 Implement `BacktestPortfolioGenerator`.
    - Generate exactly 100 portfolios by default.
    - Include 1, 3, 5, and 10 ticker concentration variants.
    - Assign conservative, balanced, and aggressive portfolio policies.
    - Use deterministic random seed behavior.
    - _Requirements: 3.1, 3.3, 3.4, 3.5, 3.7_

  - [ ] 4.2 Implement initial allocation accounting.
    - Start each portfolio with exactly USD 10,000.00 before initial purchases.
    - Buy only active watchlist stocks with sufficient start-date price history.
    - Apply commission to initial purchases if configured.
    - Record rejected tickers and allocation method.
    - _Requirements: 3.2, 3.6, 3.7, 5.1, 5.2, 5.3_

  - [ ] 4.3 Add portfolio-generation tests.
    - Prove deterministic generation from the same seed.
    - Prove generated tickers are active and price-supported.
    - Prove concentration buckets are represented when the universe allows.
    - _Requirements: 3.1, 3.3, 3.5, 3.6, 9.7_

- [ ] 5. Commission-aware simulation engine
  - [x] 5.1 Implement buy, sell, hold, and valuation primitives.
    - Buy whole shares only.
    - Prevent negative cash.
    - Charge configurable commission on buys and sells.
    - Sell all shares for accepted liquidation signals.
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.8_

  - [x] 5.2 Implement daily portfolio snapshots.
    - Include cash, holdings value, total value, realized gain/loss, unrealized gain/loss, and commission paid to date.
    - Mark snapshot data quality when prices are incomplete.
    - _Requirements: 5.6, 5.7, 5.8_

  - [ ] 5.3 Add accounting property tests.
    - [x] Prove cash never becomes negative after valid execution with focused unit tests.
    - [x] Prove commission equals configured percentage of gross trade value with focused unit tests.
    - [x] Prove sell liquidation cannot leave negative holdings with focused unit tests.
    - [ ] Prove the same accounting invariants with property tests.
    - [ ] Prove daily snapshots reconcile cash plus holdings value with property tests.
    - _Requirements: 9.1, 9.2, 9.3, 9.8_

- [ ] 6. Analysis strategy replay and comparison
  - [ ] 6.1 Implement analysis strategy manifest loading.
    - Load manifests from repo config and S3.
    - Copy exact manifests into every run artifact.
    - Refuse runs with missing or invalid analysis strategy IDs.
    - _Requirements: 4.1, 4.2, 4.6, 10.9_

  - [ ] 6.2 Implement analysis-strategy-aware recommendation replay.
    - Key cached AI recommendations and reviews by analysis strategy ID, ticker, date, model, prompt version, evidence hash, and schema version.
    - Preserve candidate scores, rejected candidates, prompts, responses, review decisions, publication decisions, and suppression reasons per strategy.
    - _Requirements: 4.7, 4.9, 10.10_

  - [ ] 6.3 Implement analysis strategy comparison reports.
    - Compare candidate selection, AI cost, recommendation mix, review pass rate, publication rate, portfolio outcomes, drawdown, commission drag, and shadow underperformance.
    - Emit pairwise and leaderboard CSV/JSON artifacts.
    - _Requirements: 4.8, 9.9, 9.10_

  - [ ] 6.4 Add analysis strategy tests.
    - Prove manifests are immutable within a run.
    - Prove cache keys change when model, prompt, evidence hash, schema version, or analysis strategy ID changes.
    - Prove comparison artifacts include all tested strategies.
    - _Requirements: 10.9, 10.10_

- [ ] 7. Portfolio policies
  - [ ] 7.1 Implement conservative portfolio policy rules.
    - Require BUY, LOW risk, blue-chip size, and successful stronger AI review for buys.
    - Reject promising-but-review-failed recommendations.
    - Apply conservative position and turnover caps.
    - _Requirements: 5.1, 5.2, 5.5, 5.9_

  - [ ] 7.2 Implement balanced portfolio policy rules.
    - Allow blue-chip and mid-cap tickers.
    - Allow LOW and MEDIUM risk.
    - Prefer stronger AI review success.
    - Require replacement margin before switching holdings.
    - _Requirements: 5.3, 5.5, 5.6, 5.9_

  - [ ] 7.3 Implement aggressive portfolio policy rules.
    - Allow startup/high-growth tickers.
    - Allow promising review-failed recommendations when configured.
    - Record review-failed exposure and reason codes.
    - _Requirements: 5.4, 5.5, 5.9_

  - [ ] 7.4 Add portfolio policy unit tests.
    - Test accepted and rejected decisions for each portfolio policy.
    - Test HOLD produces no transaction.
    - Test SELL liquidation behavior for accepted sell signals.
    - _Requirements: 5.7, 5.8, 5.9_

- [ ] 8. Recommendation replay
  - [ ] 8.1 Define recommendation replay input/output schema.
    - Include ticker, date, recommendation, risk, confidence, score, AI review status, evidence coverage, analysis strategy ID, and reasoning.
    - Include analysis strategy ID, prompt template version, model ID, evidence hash, and publication status.
    - Support cached S3 fixtures before live historical AI replay.
    - _Requirements: 2.4, 2.5, 2.6, 4.7, 5.9_

  - [ ] 8.2 Implement point-in-time recommendation loader.
    - Load only recommendations generated for or before the simulated decision timestamp as configured.
    - Record missing recommendation reason by ticker/date.
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.7_

  - [ ] 8.3 Add recommendation replay tests.
    - Prove future recommendations and future evidence are excluded.
    - Prove reduced-evidence runs are labeled.
    - Prove recommendation caching keys include model/analyzer/evidence identity.
    - _Requirements: 2.1, 2.2, 2.6, 2.7, 10.6, 10.10_

- [ ] 9. Shadow portfolio support
  - [ ] 9.1 Implement global hold shadows.
    - Fork from each starting portfolio after initial allocation.
    - Never trade after initialization.
    - Value daily alongside the main portfolio.
    - _Requirements: 7.1, 8.1, 10.4_

  - [ ] 9.2 Implement decision shadows.
    - Fork from pre-trade state when a material action occurs.
    - Ignore the triggering action.
    - Evaluate 7, 30, 90, 180, and 365 day windows when data is available.
    - Prevent recursive shadow creation in first implementation.
    - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.7, 7.8_

  - [ ] 9.3 Implement policy shadows.
    - Replay the same recommendation stream using alternate portfolio policies.
    - Compare conservative, balanced, aggressive, and review-gate variants.
    - _Requirements: 7.6, 8.6_

  - [ ] 9.4 Add shadow tests.
    - Prove decision shadows fork from pre-trade state.
    - Prove global hold shadows do not trade.
    - Prove shadow windows mark incomplete data correctly.
    - _Requirements: 7.1, 7.3, 7.4, 7.8, 10.4, 10.5_

- [ ] 10. ETF and hold baselines
  - [ ] 10.1 Implement ETF baseline portfolio creation.
    - Use same starting capital and commission assumptions.
    - Start with SPY or VOO.
    - Support optional QQQ, VTI, VT, and sector ETFs later.
    - _Requirements: 8.2, 8.3, 8.4_

  - [ ] 10.2 Implement baseline comparison metrics.
    - Compare main portfolio against hold shadow and ETF baseline.
    - Identify cases where Stockara underperforms both.
    - _Requirements: 8.1, 8.5, 8.7_

- [ ] 11. Metrics and reporting
  - [ ] 11.1 Implement portfolio-level metrics.
    - Final value, total return, annualized return, max drawdown, volatility, trade count, turnover, commission drag, and cash utilization.
    - _Requirements: 8.5, 10.8_

  - [ ] 11.2 Implement aggregate metrics.
    - Aggregate by analysis strategy, portfolio policy, concentration, sector concentration, company-size mix, and evidence coverage.
    - _Requirements: 8.6_

  - [ ] 11.3 Implement investigation queue.
    - Rank worst main-vs-shadow deltas.
    - Include review-passed underperformance and review-failed outperformance.
    - Classify likely underperformance reasons.
    - _Requirements: 7.9, 9.3, 9.4, 9.5, 9.6_

  - [ ] 11.4 Emit JSON and CSV artifacts.
    - Write config, analysis strategy manifests, portfolios, transactions, daily snapshots, shadows, metrics, analysis strategy comparisons, and investigation queue.
    - Include limitations and evidence coverage in the run summary.
    - _Requirements: 4.6, 9.1, 9.2, 9.7, 9.8, 11.5_

- [ ] 12. CLI and local run workflow
  - [ ] 12.1 Implement `python -m src.backtesting.cli run`.
    - Accept start date, end date, portfolio count, seed, commission rate, baseline ETF, analysis strategy set, portfolio policy set, and S3 prefix.
    - Print run ID and artifact paths.
    - _Requirements: 4.8, 9.1, 9.2, 9.7_

  - [ ] 12.2 Implement summary, comparison, and investigation export commands.
    - Add `summarize`, `compare-analysis-strategies`, and `export-investigation-queue` commands.
    - _Requirements: 9.3, 9.4, 9.5, 9.7, 9.9_

  - [ ] 12.3 Add a small fixture-backed smoke run.
    - Run over a short synthetic or fixture date range in tests.
    - Verify expected artifact files are produced.
    - _Requirements: 9.1, 10.8_

- [ ] 13. Historical evidence expansion
  - [ ] 13.1 Add historical earnings and dividend event loaders.
    - Use only events known at or before the decision timestamp when provenance supports it.
    - Label revised or uncertain event fields.
    - _Requirements: 2.3, 2.5, 9.8_

  - [ ] 13.2 Add historical news loader.
    - Filter by publication timestamp.
    - Label missing-news date ranges as reduced-evidence runs.
    - _Requirements: 2.2, 2.4, 2.5_

  - [ ] 13.3 Integrate evidence coverage into recommendation replay and reports.
    - Report reduced-evidence coverage states and full-evidence coverage.
    - _Requirements: 2.4, 2.5, 2.7, 9.2, 9.8_

- [ ] 14. S3 artifact storage
  - [ ] 14.1 Define S3 artifact paths for backtest data and runs.
    - Store input snapshots under `backtests/data/...`.
    - Store analysis strategy manifests under `backtests/analysis-strategies/{analysis_strategy_id}/manifest.yaml`.
    - Store recommendation caches under `backtests/recommendations/...`.
    - Store run outputs under `backtests/runs/{run_id}/`.
    - Keep artifacts immutable by run ID.
    - _Requirements: 4.6, 9.1, 9.2_

  - [ ] 14.2 Add S3 read/write support to the CLI and reporting layer.
    - Upload config, analysis strategy manifests, portfolios, transactions, snapshots, shadows, metrics, limitations, strategy comparisons, and investigation queue.
    - Allow local export as a developer convenience, but keep S3 as the canonical run record.
    - _Requirements: 4.6, 9.1, 9.7_

## Recommended First Milestone

- One bounded 365-day benchmark window, initially 2022 for a stress/bearish year.
- 20 deterministic portfolios starting with USD 10,000.00 for budget-controlled comparison runs.
- Freeze current hardcoded analyzer behavior as `analysis_strategy_current`.
- Compare one candidate `AnalysisStrategy` against the current baseline.
- Conservative, balanced, and aggressive portfolio policy shells.
- SPY or VOO ETF baseline.
- Global hold shadows.
- Commission-aware trades with 1% default commission.
- S3-backed JSON/CSV reports, including all strategy manifests and AI artifacts.
- Property tests for accounting and deterministic generation.

This milestone is useful only if reduced-evidence limitations are explicit. Decision-grade promotion should wait for the required historical evidence snapshot.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "2.1", "2.2", "2.3", "2.4"] },
    { "id": 1, "tasks": ["3.1", "3.2", "4.1", "4.2"] },
    { "id": 2, "tasks": ["3.3", "4.3", "5.1", "5.2"] },
    { "id": 3, "tasks": ["5.3", "6.1", "6.2", "6.3", "7.1"] },
    { "id": 4, "tasks": ["6.4", "7.2", "7.3", "8.1", "10.1"] },
    { "id": 5, "tasks": ["8.2", "8.3", "9.1", "9.2", "10.2", "11.1"] },
    { "id": 6, "tasks": ["9.3", "9.4", "11.2", "11.3", "11.4", "12.1", "12.2", "12.3"] },
    { "id": 7, "tasks": ["13.1", "13.2", "13.3", "14.1", "14.2"] }
  ]
}
```
