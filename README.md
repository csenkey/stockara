# Stockara — Daily Top Picks and Risk Alerts

Stockara Phase 1 is a low-cost, serverless catalyst scanner. It collects market/news/event signals, analyzes only the highest-signal candidates, and publishes static daily top-pick and sell-alert artifacts for the website.

## Phase 1 Scope

- **Daily top picks**: 5-10 promising near-term opportunities.
- **Urgent sell alerts**: high-severity negative signals from a configurable watchlist.
- **Cheap first-pass scanning**: price/volume, news, earnings, dividends, options, analyst, insider, institutional, social/news momentum, and sector-relative signals.
- **Bounded AI usage**: OpenAI runs only on the shortlisted candidates.
- **Static read model**: public reads use S3/CloudFront JSON artifacts, not live database queries.

## Architecture

```text
EventBridge -> stock/news collectors -> DynamoDB
EventBridge -> Phase 1 analyzer/publisher -> S3 JSON artifacts
CloudFront -> React static site + /top-picks/latest.json + /sell-alerts/latest.json
API Gateway -> Lambda -> /api/health only
```

Key jobs:

- **Stock Collector**: daily at 21:00 UTC.
- **News Collector**: daily at 20:30 UTC.
- **Phase 1 Analyzer/Publisher**: daily at 22:00 UTC.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.12, FastAPI, Pydantic, structlog |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS |
| Storage | DynamoDB on-demand, S3, CloudFront |
| AI | OpenAI GPT-4o-mini with deterministic fallback |
| Infrastructure | AWS CDK in Python, GitHub Actions |
| Testing | pytest |

## Project Structure

```text
backend/src/api/          Public health API
backend/src/collectors/   Stock and news collectors
backend/src/analysis/     Phase 1 scanner, analyzer, publisher
backend/src/db/           DynamoDB access helpers
backend/src/models/       Phase 1 Pydantic schemas
frontend/src/pages/       Static artifact dashboard
infrastructure/stacks/    DynamoDB, API/jobs, frontend, monitoring
scripts/seed_watchlist.py DynamoDB tracked-universe bootstrap
```

## Local Commands

Backend:

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Frontend:

```bash
cd frontend
npm ci
npm run lint
npm run build
npm run dev
```

Infrastructure:

```bash
cd infrastructure
python -m pip install -r requirements.txt
python -m pytest tests/ -v
cdk synth -c deploymentStage=prod
```

## Bootstrap

After deploying, seed the tracked universe:

```bash
STOCKARA_TABLE_NAME=<table-name> \
STOCKARA_SELL_ALERT_TICKERS=AAPL,MSFT,NVDA \
python -m scripts.seed_watchlist
```

Then run the collectors and analyzer/publisher once manually for the first static artifacts, or wait for the daily schedules.

Published artifacts:

```text
/top-picks/latest.json
/top-picks/history/YYYY-MM-DD.json
/sell-alerts/latest.json
/sell-alerts/history/YYYY-MM-DD.json
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `STOCKARA_TABLE_NAME` | DynamoDB table name |
| `STOCKARA_ARTIFACT_BUCKET` | S3 bucket for static frontend and generated JSON artifacts |
| `OPENAI_API_KEY` | Optional OpenAI key; deterministic fallback is used if absent |
| `NEWSAPI_KEY` | Optional NewsAPI key |
| `FINNHUB_KEY` | Optional Finnhub key |
| `ALPHA_VANTAGE_API_KEY` | Optional Alpha Vantage fallback key |
| `AWS_REGION` | AWS deployment region |

## GitHub Actions

Pushes to `main`, `feature/**`, or `codex/**` run tests, frontend build, CDK synth/deploy, and a `/api/health` smoke test.

## License

Private — all rights reserved.
