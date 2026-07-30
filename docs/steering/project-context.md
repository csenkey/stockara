# Stockara Project Context

## Purpose

Stockara is a low-cost, serverless stock monitoring and analysis system. It tracks 1000+ stocks, collects OHLCV market data and news, generates AI BUY/HOLD/SELL recommendations, manages encrypted user portfolios, and exposes personalized suggestions through a React dashboard.

Phase 1 is an accurate, reliable stock analyzer intended to support real business and investment decisions. It is not a throwaway MVP, demo, or proof of concept. Later phases add portfolio-management depth around a trustworthy analysis core; they do not defer data correctness, provider resilience, or recommendation quality.

Phase 1 should publish promising, evidence-backed opportunities among tickers whose own data is fresh and reliable enough for decision-grade analysis. It does not need to claim the absolute top 5 or top 10 opportunities across the entire watchlist when universe coverage is partial, but partial coverage must be explicit and stale or under-supported tickers must be suppressed.

The project also includes a public demo-trading feature: 100 superhero-named simulated accounts start with USD 10,000.00, trade daily from AI recommendations, pay 1% commission on every transaction, and publish leaderboard/detail pages without authentication.

## Stack

- Backend: Python 3.12, FastAPI, Mangum, Pydantic, structlog, pandas
- Frontend: React 18, TypeScript, Vite, Tailwind CSS, Recharts, lucide-react
- Database: DynamoDB single-table design provisioned by CDK
- Infrastructure: AWS CDK in Python, Lambda, API Gateway, EventBridge, Cognito, KMS, S3, CloudFront, CloudWatch
- External services: yfinance, Alpha Vantage, NewsAPI, Finnhub, OpenAI mini analysis/news models plus stronger review model
- Testing: pytest plus Hypothesis-style property tests for trading invariants

## Repository Map

- `backend/src/api/`: FastAPI routers for auth, portfolio, preferences, stocks, suggestions, demo, health, and Lambda handler wiring
- `backend/src/collectors/`: scheduled stock, news, earnings, dividend, and evidence collection
- `backend/src/analysis/`: daily AI recommendation and Phase 1 pipeline logic
- `backend/src/services/`: provider health, manifests, artifacts, demo account management, and supporting services
- `backend/src/models/`: Pydantic schemas
- `backend/src/db/`: DynamoDB table access helpers and repository functions
- `backend/tests/`: unit, integration, and property tests
- `frontend/src/pages/`: dashboard, calendar, data health, and future public/user pages
- `frontend/src/components/`: reusable UI components
- `frontend/src/services/`: frontend API clients and helpers
- `infrastructure/stacks/`: CDK stacks for API, database, frontend, monitoring, and scheduled workloads
- `docs/steering/`: canonical product, architecture, and backlog steering for Codex and Claude
- `.kiro/specs/`: legacy source material only

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
- Each demo account starts with exactly USD 10,000.00.
- Initial account cash must remain between USD 500.00 and USD 9,500.00.
- Initial and daily holdings must use active watchlist stocks.
- Every buy or sell transaction charges exactly 1% commission.
- BUY actions allocate at most 10% of account portfolio value and buy only whole shares.
- SELL actions liquidate the entire held position for that ticker.
- HOLD recommendations produce no transaction.
- Transactions are permanent and include account, ticker, action, quantity, price, total value, commission, cash after, and timestamp.
- Daily snapshots support efficient portfolio time-series views.
- Public demo APIs under `/api/demo/*` require no authentication and should respond within 2 seconds under normal load.
- Leaderboards are sorted by descending portfolio value; transaction history is sorted newest first.

## Scheduling And Deployment Expectations

- Daily production workflow: Step Functions state machine `stockara-daily-pipeline`, started once daily before the analysis window, is the source of truth for static metadata sync, manifest dispatch, final price readiness, gap repair, news readiness, calendar/evidence collection, AI analysis/review, publication, and workflow status.
- Stock collector: frequent standalone EventBridge collection is disabled as a rollback path; the daily workflow owns final pre-analysis price readiness.
- News collector: EventBridge runs three times per day during development/testing as quota-conscious prefetching; the daily workflow owns final pre-analysis news readiness.
- AI analyzer: invoked by the daily workflow, not by an independent production schedule.
- Stock gap scan: runs at 23:15 UTC as separate after-market maintenance and must not be treated as the same-day publication gate.
- Demo trade executor: EventBridge daily at 22:30 UTC, after AI analysis.
- Backend Lambdas should emit structured JSON logs and useful custom metrics.
- The health endpoint should report component status and last successful batch timestamps; daily operational diagnosis should start from Step Functions execution status plus `workflow/latest.json`.
- Infrastructure should stay serverless-first and cost-conscious for roughly 10-100 users.
