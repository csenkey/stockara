# CLAUDE.md

This file initializes Claude sessions for Stockara. It intentionally mirrors `AGENTS.md` at a high level and points Claude to the shared, tool-neutral steering files.

## First Read

Before making product, architecture, or implementation changes, read:

1. `docs/steering/README.md`
2. `docs/steering/project-context.md`
3. `docs/steering/engineering-rules.md`
4. `docs/steering/work-queue.md`
5. `docs/steering/analysis-strategies/` when changing analyzer logic
6. Relevant feature files under `docs/steering/features/`

`.kiro/specs/` is legacy reference material only. Do not create or update Kiro specs unless Istvan explicitly asks for that format.

## Working Agreement

- Follow the existing codebase style and module boundaries.
- Keep changes scoped to the requested behavior.
- Prefer targeted tests while iterating, then run relevant full suites before handoff.
- Mock external providers in tests.
- Preserve Decimal-style precision for financial calculations.
- Do not store real user portfolio data in plaintext.
- Keep shared planning updates in `docs/steering/` and high-level priority updates in `docs/steering/work-queue.md`.

## Current Priority Memory

Stockara's Phase 1 north star is reliable, decision-grade stock analysis. Recommendations must be evidence-backed, use fresh enough data, expose partial coverage, suppress stale or under-supported tickers, and respect the stronger AI review gate for public BUY/SELL publication.

The next portfolio-related planning work is `docs/steering/features/backtest-support-with-shadowed-portfolios/`, which defines an S3-backed historical simulator with shadow portfolios, ETF baselines, and versioned `AnalysisStrategy` comparison.
