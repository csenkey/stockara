# AGENTS.md

This document initializes coding-agent sessions for Stockara. Shared, tool-neutral steering lives in `docs/steering/` and should be treated as canonical for both Codex and Claude. Legacy Kiro planning documents have been removed; do not recreate that format unless Istvan explicitly asks for it.

## First Read

Before making product, architecture, or implementation changes, read:

1. `docs/steering/README.md`
2. `docs/steering/project-context.md`
3. `docs/steering/engineering-rules.md`
4. `docs/steering/work-queue.md`
5. `docs/steering/analysis-strategies/` when changing analyzer logic
6. Relevant feature files under `docs/steering/features/`

## Project Purpose

Stockara is a low-cost, serverless stock monitoring and analysis system. It tracks 1000+ stocks, collects OHLCV market data and news, generates AI BUY/HOLD/SELL recommendations, manages encrypted user portfolios, and exposes personalized suggestions through a React dashboard.

Phase 1 is an accurate, reliable stock analyzer intended to support real business and investment decisions. It is not a throwaway MVP, demo, or proof of concept. Later phases add portfolio-management features around a trustworthy analysis core; they do not defer data correctness, provider resilience, or recommendation quality.

Phase 1 should publish promising, evidence-backed opportunities among tickers whose own data is fresh and reliable enough for decision-grade analysis. It does not need to claim the absolute top 5 or top 10 stocks across the whole watchlist when universe coverage is partial, but it must make partial coverage explicit and suppress stale or under-supported tickers.

The project also includes a public demo-trading feature: 100 superhero-named simulated accounts start with $10,000, trade daily from AI recommendations, pay 1% commission on every transaction, and publish leaderboard/detail pages without authentication.

## Stack

- Backend: Python 3.12, FastAPI, Mangum, Pydantic, structlog, pandas
- Frontend: React 18, TypeScript, Vite, Tailwind CSS, Recharts, lucide-react
- Database: DynamoDB single-table design provisioned by CDK
- Infrastructure: AWS CDK in Python, Lambda, API Gateway, EventBridge, Cognito, KMS, S3, CloudFront, CloudWatch
- External services: yfinance, Alpha Vantage, NewsAPI, Finnhub, OpenAI mini analysis/news models plus stronger review model
- Testing: pytest plus Hypothesis-style property tests for trading invariants

## Repository Map

- `backend/src/api/`: FastAPI routers for auth, portfolio, preferences, stocks, suggestions, demo, health, and Lambda handler wiring
- `backend/src/collectors/`: scheduled stock and news collection
- `backend/src/analysis/ai_analyzer.py`: daily AI recommendation generation
- `backend/src/services/`: encryption, suggestions, demo account management, demo trade execution
- `backend/src/models/`: Pydantic schemas, including demo schemas
- `backend/src/db/`: DynamoDB table access helpers and repository functions
- `backend/tests/`: unit and property tests
- `frontend/src/pages/`: login, dashboard, settings, public demo leaderboard, public demo account detail
- `frontend/src/components/`: portfolio and suggestion UI components
- `frontend/src/services/`: axios API/auth clients
- `infrastructure/stacks/`: CDK stacks for API, database, frontend, monitoring, and demo trading
- `docs/steering/`: canonical product, architecture, and backlog steering for Codex and Claude

## Common Commands

Backend:

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Frontend:

```bash
cd frontend
npm run lint
npm run build
npm run dev
```

Infrastructure:

```bash
cd infrastructure
python -m pytest tests/ -v
cdk deploy --all -c deploymentStage=prod
```

Prefer targeted tests while iterating, then run the relevant full suite before handing off. External provider calls should be mocked in tests; do not require real OpenAI, yfinance, Cognito, KMS, NewsAPI, Finnhub, or Alpha Vantage credentials for unit tests.

## How We Work

- During early Phase 1 development, commit implementation work directly on `main`; Istvan is the only developer and branch-scoped environments are intentionally disabled for now.
- Every commit on `main` is expected to trigger an AWS deployment through CI/CD.
- `main` deploys the active `prod` stage. Do not create or deploy feature/codex branch-scoped AWS stages unless this policy is explicitly re-enabled.
- Before committing, run all relevant local tests and builds. A commit is not ready for deployment until local tests pass.
- If the AWS deployment fails, fetch the failure details automatically from GitHub Actions logs or related AWS logs, diagnose the issue, correct it, amend the feature-branch commit, and retry deployment.
- Continue the fix, amend, and retry loop until the feature-branch deployment is green.
- After each successful AWS deployment, run a smoke test against the deployed environment to verify the core application is working.
- Treat a green deploy plus passing smoke test as the minimum bar before opening or updating a pull request for review.
- When branch-based development is re-enabled later, squash merge completed feature branches into `main`, then delete the feature branch and its branch-scoped AWS resources.

## Core Product Rules

- Stock data collection stores one OHLCV record per ticker/trading date and skips duplicates without overwriting existing data.
- Stock data provider failures retry up to 3 times with exponential backoff starting at 2 seconds.
- Malformed stock records are discarded with warnings while the batch continues.
- News summaries must include title, source, publication date, related tickers, and summary text no longer than 500 characters.
- News articles are deduplicated by title and source.
- Stocks must have exactly one sector and one company size: `blue_chip`, `mid_cap`, or `startup`.
- AI analysis considers at least 30 calendar days of stock data and 7 calendar days of news.
- AI recommendations use only `BUY`, `HOLD`, or `SELL`; risk uses only `LOW`, `MEDIUM`, or `HIGH`.
- Public BUY/SELL recommendations should pass the stronger AI review gate; review failures or rejections suppress publication.
- If current-day analysis is missing, suggestions use the most recent analysis and expose the analysis date.
- User portfolios must be encrypted as a single stored string and decrypted only in memory.
- Portfolio writes validate ticker existence, positive integer quantity, and positive buying price before replacing stored data.
- Authorization must ensure users can access only their own portfolio data.

## Demo Trading Rules

- Exactly 100 demo accounts are created with unique superhero names.
- Each demo account starts with exactly `$10,000.00`.
- Initial account cash must remain between `$500.00` and `$9,500.00`.
- Initial and daily holdings must use active watchlist stocks.
- Every buy or sell transaction charges exactly 1% commission.
- BUY actions allocate at most 10% of account portfolio value and buy only whole shares.
- SELL actions liquidate the entire held position for that ticker.
- HOLD recommendations produce no transaction.
- Transactions are permanent and include account, ticker, action, quantity, price, total value, commission, cash after, and timestamp.
- Daily snapshots support efficient portfolio time-series views.
- Public demo APIs under `/api/demo/*` require no authentication and should respond within 2 seconds under normal load.
- Leaderboards are sorted by descending portfolio value; transaction history is sorted newest first.

## API Surface

Authenticated/core endpoints:

- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/portfolio`
- `PUT /api/portfolio/stocks`
- `DELETE /api/portfolio/stocks/{ticker}`
- `GET /api/suggestions`
- `GET /api/stocks`
- `GET /api/stocks/{ticker}/analysis`
- `GET /api/preferences`
- `PUT /api/preferences`
- `GET /api/health`

Public demo endpoints:

- `GET /api/demo/leaderboard`
- `GET /api/demo/accounts/{name}`
- `GET /api/demo/accounts/{name}/transactions`
- `GET /api/demo/accounts/{name}/performance`

## Scheduling and Deployment Expectations

- Stable baseline: `stockara-1.0`; read `docs/steering/stockara-1.0.md` for the shipped architecture.
- Daily production owner: Step Functions state machine `stockara-daily-pipeline`, started by EventBridge at 21:05 UTC.
- The workflow owns metadata sync, manifest dispatch, price readiness/gap repair, news, calendars/evidence, AI analysis/review, and publication.
- News prefetch runs three times daily during development/testing; the daily workflow owns final news readiness.
- Stock gap scan: EventBridge at 23:15 UTC as separate after-market maintenance.
- AI analysis is invoked by the workflow, not by an independent production schedule.
- Demo trade executor: EventBridge daily at 22:30 UTC, after AI analysis.
- Backend Lambdas should emit structured JSON logs and useful custom metrics.
- The health endpoint should report component status and last successful batch timestamps.
- Infrastructure should stay serverless-first and cost-conscious for roughly 10-100 users.

## Security and Data Handling

- Never store plaintext portfolio data in the database.
- Use AES-256-GCM/KMS-backed encryption semantics for portfolio data.
- Keep secrets out of source code; expect environment variables, AWS Secrets Manager, or CDK-provided configuration.
- Preserve HTTPS/JWT/Cognito assumptions for authenticated APIs.
- Use least-privilege IAM in CDK changes.
- Public demo endpoints may expose simulated account data only; do not leak user portfolio or auth-protected data there.

## Engineering Guidance

- Follow existing module boundaries and naming before adding new abstractions.
- Keep API schemas in Pydantic models and frontend types aligned with response shapes.
- For financial calculations, preserve Decimal-style precision on the backend where existing code uses it.
- Keep property tests for demo-account invariants when changing trading/account logic.
- Use structured logs for batch and API operations, especially partial failures.
- Keep public demo routes outside authenticated frontend layout and backend auth middleware.
- Prefer CDK table/index changes for database schema access patterns; do not silently mutate data shapes in application code.
- Preserve `docs/steering/` requirements as the source for acceptance criteria and task traceability.

## Useful Source Specs

- `docs/steering/README.md`
- `docs/steering/project-context.md`
- `docs/steering/engineering-rules.md`
- `docs/steering/work-queue.md`
- `docs/steering/analysis-strategies/README.md`
- `docs/steering/analysis-strategies/analysis_strategy_schema.md`
- `docs/steering/analysis-strategies/strategy_registry.md`
- `docs/steering/features/backtest-support-with-shadowed-portfolios/requirements.md`
- `docs/steering/features/backtest-support-with-shadowed-portfolios/design.md`
- `docs/steering/features/backtest-support-with-shadowed-portfolios/backlog.md`
