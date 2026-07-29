# Stockara Steering Docs

These files are the canonical, tool-neutral steering layer for Stockara. Codex and Claude should both read these before making product or architecture changes.

`.kiro/specs/` is legacy reference material only. Do not add new planning work there unless Istvan explicitly asks for Kiro-compatible artifacts.

## Read Order For Agents

1. `AGENTS.md` or `CLAUDE.md`, depending on the tool.
2. `docs/steering/project-context.md`.
3. `docs/steering/engineering-rules.md`.
4. `docs/steering/work-queue.md`.
5. `docs/steering/analysis-strategies/` when changing analyzer logic.
6. Any relevant feature folder under `docs/steering/features/`.

## Update Rules

- Keep living requirements, architecture, and executable backlog items in `docs/steering/`.
- Keep high-level work ordering in `docs/steering/work-queue.md`.
- When a feature changes materially, update its `requirements.md`, `design.md`, and `backlog.md` together when applicable.
- Mark completed backlog items with `[x]` only after the code, tests, docs, and verification expected by that item are actually done.
- Preserve traceability by naming the requirement or product rule behind each executable task.
- Prefer dated notes inside feature files over scattering session-specific context across chat history.
- If `.kiro` disagrees with `docs/steering/`, follow `docs/steering/`.
- If older backlog or handoff files disagree with `docs/steering/work-queue.md`, follow `docs/steering/work-queue.md`.

## Current Feature Steering

- `work-queue.md`
- `features/daily-pipeline-stability/requirements.md`
- `features/daily-pipeline-stability/design.md`
- `features/daily-pipeline-stability/backlog.md`
- `features/backtest-support-with-shadowed-portfolios/requirements.md`
- `features/backtest-support-with-shadowed-portfolios/design.md`
- `features/backtest-support-with-shadowed-portfolios/backlog.md`
- `analysis-strategies/README.md`
- `analysis-strategies/analysis_strategy_schema.md`
- `analysis-strategies/strategy_registry.md`
