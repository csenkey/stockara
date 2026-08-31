# Stockara Steering Docs

These files are the canonical, tool-neutral steering layer for Stockara. Codex and Claude should both read these before making product or architecture changes.

Legacy Kiro planning documents have been removed. Do not recreate Kiro-compatible artifacts unless Istvan explicitly asks for that format.

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
- Deleted historical backlog and handoff files are not active sources. Follow `docs/steering/work-queue.md`.

## Stable Release Operations

- Stable production versions are immutable annotated Git tags named `stockara-X.Y`, such as `stockara-1.0`.
- A tag is a rollback point only after its commit has passed the normal deployment checks.
- Use `.github/workflows/deploy-stable.yml` from the GitHub Actions UI to deploy a selected stable tag to the `prod` stage.
- Do not move, delete, or reuse a stable tag. Create a new tag for a new stable version.
- Normal development continues on `main`; pushes to `main` continue to deploy the active production version automatically.

## Current Feature Steering

- `stockara-1.0.md` — shipped baseline, runtime architecture, operations, and limitations.
- `work-queue.md`
- `features/daily-pipeline-stability/requirements.md`
- `features/daily-pipeline-stability/design.md`
- `features/daily-pipeline-stability/backlog.md`
- `features/on-demand-holding-review/requirements.md`
- `features/on-demand-holding-review/design.md`
- `features/on-demand-holding-review/backlog.md`
- `features/earnings/requirements.md`
- `features/earnings/design.md`
- `features/earnings/backlog.md`
- `features/backtest-support-with-shadowed-portfolios/requirements.md`
- `features/backtest-support-with-shadowed-portfolios/design.md`
- `features/backtest-support-with-shadowed-portfolios/backlog.md`
- `analysis-strategies/README.md`
- `analysis-strategies/analysis_strategy_schema.md`
- `analysis-strategies/strategy_registry.md`
