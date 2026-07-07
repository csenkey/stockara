# Analysis Strategy Registry

This registry tracks named Stockara analyzer strategy snapshots. Add a row whenever analyzer business logic is frozen for backtesting.

| Strategy ID | Status | Parent | Config | Purpose |
|---|---|---|---|---|
| `analysis_strategy_current` | `baseline` | none | `configs/analysis-strategies/analysis_strategy_current.yaml` | Placeholder for the currently hardcoded analyzer behavior. Fill this accurately before the first strategy-comparison backtest. |

## Update Rules

- Do not mutate a strategy manifest after it has been used in a backtest run.
- Create a new strategy ID for any meaningful change to preselection, filters, scoring, prompts, AI models, review gates, publication rules, or cost limits.
- Keep rejected strategies in the registry with a short reason and links to backtest artifacts when available.
- Use the same shared data snapshot and portfolio set when comparing strategy versions.

