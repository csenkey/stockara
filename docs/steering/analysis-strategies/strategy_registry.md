# Analysis Strategy Registry

This registry tracks named Stockara analyzer strategy snapshots. Add a row whenever analyzer business logic is frozen for backtesting.

| Strategy ID | Status | Parent | Config | Purpose |
|---|---|---|---|---|
| `analysis_strategy_current` | `baseline` | none | `configs/analysis-strategies/analysis_strategy_current.yaml` | Placeholder for the currently hardcoded analyzer behavior. Fill this accurately before the first strategy-comparison backtest. |
| `analysis_strategy_2026_09_05_earnings_event_v1` | `candidate` | `analysis_strategy_current` | `configs/analysis-strategies/analysis_strategy_2026_09_05_earnings_event_v1.yaml` | First candidate for seven-day earnings-event prediction (EARN-5.1). Shadow-only research: models result surprise separately from post-report abnormal return, uses no language model, and cannot influence production consumers. Its `promotion_gates` values are `proposed` and still need ratification in EARN-5.6. |

## Update Rules

- Do not mutate a strategy manifest after it has been used in a backtest run.
- Create a new strategy ID for any meaningful change to preselection, filters, scoring, prompts, AI models, review gates, publication rules, or cost limits.
- Keep rejected strategies in the registry with a short reason and links to backtest artifacts when available.
- Use the same shared data snapshot and portfolio set when comparing strategy versions.

