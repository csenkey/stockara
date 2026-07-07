# Backtest Support With Shadowed Portfolios

Status: Proposed architecture and executable backlog.

Canonical steering artifacts:

- `docs/steering/features/backtest-support-with-shadowed-portfolios/requirements.md`
- `docs/steering/features/backtest-support-with-shadowed-portfolios/design.md`
- `docs/steering/features/backtest-support-with-shadowed-portfolios/backlog.md`

No Kiro-format backtest spec is maintained. Older `.kiro/specs/` files are legacy project history only.

## Purpose

This feature validates whether Stockara's recommendation system improves portfolio outcomes over time. It is not the real-user portfolio management feature. It is an offline-first backtesting and investigation system for simulated portfolios.

The central question is:

> Did Stockara's active decisions improve portfolio value versus doing nothing or buying a simple ETF baseline?

## Core Shape

- Generate deterministic simulated portfolios from a fixed seed. Budget-controlled strategy-comparison runs should start with 20 portfolios.
- Start each portfolio with USD 10,000.00.
- Vary initial concentration from 1 ticker to 10 tickers.
- Compare versioned `AnalysisStrategy` manifests for Stockara analyzer logic.
- Apply conservative, balanced, and aggressive `PortfolioPolicy` variants to recommendations.
- Charge commission on every buy and sell, defaulting to 1%.
- Compare main portfolios against:
  - Global hold shadows.
  - Decision shadows created whenever Stockara changes the portfolio.
  - Policy shadows using alternative strategy rules.
  - ETF baselines such as SPY or VOO.

## Shadow Semantics

Create a global hold shadow for every starting portfolio. It starts from the same initial allocation and never follows Stockara trades.

Create a decision shadow whenever Stockara makes a material portfolio-changing action. The decision shadow forks from the pre-trade state, ignores the triggering action, and is evaluated over fixed future windows: 7, 30, 90, 180, and 365 days.

Decision shadows are diagnostic probes. The first implementation should not recursively create more shadows from shadows.

## First Milestone

Build the smallest decision-grade simulator:

- Historical OHLCV replay for stocks and ETFs.
- Historical news with publication timestamps.
- Historical earnings and dividend events with known-at timestamps where available.
- Inactive or delisted ticker metadata to reduce survivorship bias.
- Point-in-time evidence provenance.
- ETF baseline with SPY or VOO.
- Deterministic portfolio generation.
- Commission-aware trade accounting.
- Global hold shadows.
- S3-backed JSON/CSV outputs.
- Property tests for cash, commission, holdings, and seed determinism.

If provider coverage is incomplete during development, runs must be clearly labeled as reduced-evidence and should not be treated as the final reliability verdict.

## Known Missing Inputs

- Five years of reliable OHLCV for all relevant stocks.
- Adjusted price semantics and split/dividend handling.
- Historical earnings and dividend events.
- Historical news with publication timestamps.
- ETF metadata and historical prices.
- Inactive/delisted ticker history to reduce survivorship bias.
- Point-in-time evidence provenance.

## S3 Artifacts

S3 is the canonical storage location:

```text
backtests/data/prices/instrument_type={stock|etf}/ticker={ticker}/...
backtests/data/news/publication_date={YYYY-MM-DD}/...
backtests/data/events/earnings/event_date={YYYY-MM-DD}/...
backtests/data/events/dividends/ex_date={YYYY-MM-DD}/...
backtests/data/instruments/latest.json
backtests/analysis-strategies/{analysis_strategy_id}/manifest.yaml
backtests/recommendations/analysis_strategy={analysis_strategy_id}/analysis_date={YYYY-MM-DD}/...
backtests/runs/{run_id}/config.json
backtests/runs/{run_id}/analysis_strategies/{analysis_strategy_id}.yaml
backtests/runs/{run_id}/portfolios.json
backtests/runs/{run_id}/transactions.csv
backtests/runs/{run_id}/snapshots.csv
backtests/runs/{run_id}/shadows.csv
backtests/runs/{run_id}/metrics.json
backtests/runs/{run_id}/investigation_queue.csv
backtests/runs/{run_id}/limitations.json
backtests/runs/{run_id}/comparison/analysis_strategy_leaderboard.csv
backtests/runs/{run_id}/comparison/analysis_strategy_pairwise.csv
```
