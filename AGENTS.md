# AGENTS.md

This document initializes coding-agent sessions for Stockara. It is distilled from the Kiro specs in `.kiro/specs/stock-monitoring-system/` and `.kiro/specs/demo-trading-accounts/`. When product behavior is unclear, read those spec files first and preserve their requirement traceability.

## Project Purpose

Stockara is a low-cost, serverless stock monitoring and analysis system. It tracks 1000+ stocks, collects OHLCV market data and news, generates AI BUY/HOLD/SELL recommendations, manages encrypted user portfolios, and exposes personalized suggestions through a React dashboard.

The project also includes a public demo-trading feature: 100 superhero-named simulated accounts start with $10,000, trade daily from AI recommendations, pay 1% commission on every transaction, and publish leaderboard/detail pages without authentication.

## Stack

- Backend: Python 3.12, FastAPI, Mangum, Pydantic, structlog, pandas
- Frontend: React 18, TypeScript, Vite, Tailwind CSS, Recharts, lucide-react
- Database: PostgreSQL with migrations in `backend/src/db/migrations/`
- Infrastructure: AWS CDK in Python, Lambda, API Gateway, EventBridge, Cognito, KMS, S3, CloudFront, CloudWatch
- External services: yfinance, Alpha Vantage, NewsAPI, Finnhub, OpenAI GPT-4o-mini
- Testing: pytest plus Hypothesis-style property tests for trading invariants

## Repository Map

- `backend/src/api/`: FastAPI routers for auth, portfolio, preferences, stocks, suggestions, demo, health, and Lambda handler wiring
- `backend/src/collectors/`: scheduled stock and news collection
- `backend/src/analysis/ai_analyzer.py`: daily AI recommendation generation
- `backend/src/services/`: encryption, suggestions, demo account management, demo trade execution
- `backend/src/models/`: Pydantic schemas, including demo schemas
- `backend/src/db/`: connection helpers and SQL migrations
- `backend/tests/`: unit and property tests
- `frontend/src/pages/`: login, dashboard, settings, public demo leaderboard, public demo account detail
- `frontend/src/components/`: portfolio and suggestion UI components
- `frontend/src/services/`: axios API/auth clients
- `infrastructure/stacks/`: CDK stacks for API, database, frontend, monitoring, and demo trading
- `.kiro/specs/`: product requirements, technical designs, and task plans

## Common Commands

Backend:

```bash
cd backend
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
cdk deploy --all
```

Prefer targeted tests while iterating, then run the relevant full suite before handing off. External provider calls should be mocked in tests; do not require real OpenAI, yfinance, Cognito, KMS, NewsAPI, Finnhub, or Alpha Vantage credentials for unit tests.

## How We Work

- Do all development on feature branches. Do not commit implementation work directly on `main`.
- Every commit on a feature branch is expected to trigger an AWS deployment through CI/CD.
- Before committing, run all relevant local tests and builds. A commit is not ready for deployment until local tests pass.
- If the AWS deployment fails, fetch the failure details automatically from GitHub Actions logs or related AWS logs, diagnose the issue, correct it, amend the feature-branch commit, and retry deployment.
- Continue the fix, amend, and retry loop until the feature-branch deployment is green.
- After each successful AWS deployment, run a smoke test against the deployed environment to verify the core application is working.
- Treat a green deploy plus passing smoke test as the minimum bar before opening or updating a pull request for review.

## Core Product Rules

- Stock data collection stores one OHLCV record per ticker/trading date and skips duplicates without overwriting existing data.
- Stock data provider failures retry up to 3 times with exponential backoff starting at 2 seconds.
- Malformed stock records are discarded with warnings while the batch continues.
- News summaries must include title, source, publication date, related tickers, and summary text no longer than 500 characters.
- News articles are deduplicated by title and source.
- Stocks must have exactly one sector and one company size: `blue_chip`, `mid_cap`, or `startup`.
- AI analysis considers at least 30 calendar days of stock data and 7 calendar days of news.
- AI recommendations use only `BUY`, `HOLD`, or `SELL`; risk uses only `LOW`, `MEDIUM`, or `HIGH`.
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

- Stock collector: EventBridge daily at 21:00 UTC.
- News collector: EventBridge every 15 minutes by default.
- AI analyzer: EventBridge daily at 22:00 UTC.
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
- Prefer migrations for database changes; do not silently mutate schema in application code.
- Preserve `.kiro` requirements as the source for acceptance criteria and task traceability.

## Useful Source Specs

- `.kiro/specs/stock-monitoring-system/requirements.md`
- `.kiro/specs/stock-monitoring-system/design.md`
- `.kiro/specs/stock-monitoring-system/tasks.md`
- `.kiro/specs/demo-trading-accounts/requirements.md`
- `.kiro/specs/demo-trading-accounts/design.md`
- `.kiro/specs/demo-trading-accounts/tasks.md`
