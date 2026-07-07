# Analysis Strategies

An `AnalysisStrategy` is a versioned snapshot of how Stockara's analyzer produces recommendations. It is not an investor risk profile and it is not a simulated portfolio policy.

Use analysis strategies to compare analyzer business-logic versions:

- non-AI preselection flow
- predicates and filters
- candidate scoring weights
- evidence requirements
- prompt templates
- prompt evidence blocks
- AI recommendation model
- AI review model
- review gates
- publication and suppression rules
- fallback behavior
- cost limits

Every meaningful analyzer change should become a named analysis strategy before a backtest run. Backtest results should decide whether the candidate strategy is promoted, rejected, or kept experimental.

## Canonical Files

- `analysis_strategy_schema.md`
- `strategy_registry.md`
- `configs/analysis-strategies/analysis_strategy_current.yaml`

## Status Values

- `baseline`: currently accepted comparison baseline.
- `candidate`: proposed analyzer change under test.
- `promoted`: accepted strategy for future production use.
- `rejected`: tested and rejected because it regressed or failed gates.
- `archived`: kept for history, no longer active.

## Promotion Principle

Promote only when the strategy improves or preserves baseline quality across agreed benchmark windows without unacceptable increases in drawdown, AI cost, overtrading, stale-data exposure, or unexplained decision-shadow underperformance.

