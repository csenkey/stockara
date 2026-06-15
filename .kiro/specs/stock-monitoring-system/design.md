# Technical Design Document

## Overview

This document describes the architecture and technology choices for the Stock Monitoring and Analysis System. The design prioritizes minimal hosting cost for low user volume (10-100 users), serverless-first approach, and simplicity of deployment.

## Technology Stack

### Backend: Python (FastAPI)
- **Why**: Rich ecosystem for financial data (yfinance, pandas), strong AI/ML library support, excellent serverless compatibility, fast development speed
- **Framework**: FastAPI — async-native, auto-generates OpenAPI docs, lightweight

### Frontend: React + TypeScript (Vite)
- **Why**: Component-based UI fits the dashboard pattern well, large ecosystem, TypeScript for safety, Vite for fast builds
- **UI Library**: Tailwind CSS + shadcn/ui for clean, responsive design without heavy dependencies

### Cloud Provider: AWS
- **Why**: Best serverless pricing at low scale, Lambda free tier (1M requests/month), most mature serverless ecosystem

### Database: DynamoDB (single-table, on-demand)
- **Why**: Serverless pay-per-request storage fits low-volume scheduled workloads, avoids always-on database capacity, and supports keyed stock, user, portfolio, analysis, news, and demo-account access patterns with GSIs

### AI/LLM: OpenAI API (GPT-4o-mini)
- **Why**: Cost-effective for summarization and analysis (~$0.15/1M input tokens), no infrastructure to manage, good at structured output for BUY/HOLD/SELL classification

### Stock Data Provider: Yahoo Finance (yfinance) + Alpha Vantage (free tier)
- **Why**: yfinance is free and covers 1000+ tickers easily, Alpha Vantage as backup (500 calls/day free)

### News Feed: NewsAPI.org (free developer tier) + Finnhub (free tier)
- **Why**: NewsAPI gives 100 requests/day free with financial news, Finnhub provides market-specific news with free tier

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         AWS Cloud                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────┐     ┌──────────────────┐     ┌─────────────┐ │
│  │  CloudFront  │────▶│  S3 (Frontend)   │     │  EventBridge│ │
│  │  (CDN)       │     │  React SPA       │     │  (Scheduler)│ │
│  └──────────────┘     └──────────────────┘     └──────┬──────┘ │
│                                                         │        │
│  ┌──────────────────────────────────────────────────────┼──────┐│
│  │                    API Gateway                        │      ││
│  └───────────────────────┬──────────────────────────────┘      ││
│                           │                              │       ││
│  ┌────────────────────────┼──────────────────────────────┼─────┐││
│  │                  Lambda Functions                      │     │││
│  │                                                       │     │││
│  │  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐ │     │││
│  │  │  Auth API   │  │ Portfolio   │  │ Suggestions  │ │     │││
│  │  │  (Cognito)  │  │ API         │  │ API          │ │     │││
│  │  └─────────────┘  └─────────────┘  └──────────────┘ │     │││
│  │                                                       │     │││
│  │  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐ │     │││
│  │  │  Stock Data │  │  News       │  │  AI Analysis │◀┘     │││
│  │  │  Collector  │  │  Collector  │  │  (Daily)     │       │││
│  │  └─────────────┘  └─────────────┘  └──────────────┘       │││
│  └────────────────────────────────────────────────────────────┘││
│                           │                                      ││
│  ┌────────────────────────┼──────────────────────────────────┐  ││
│  │              DynamoDB (single-table)                │  ││
│  │  ┌──────────┐ ┌───────────┐ ┌────────────┐ ┌──────────┐ │  ││
│  │  │ stocks   │ │ news_     │ │ portfolios │ │ analysis │ │  ││
│  │  │ _data    │ │ summaries │ │ (encrypted)│ │ _results │ │  ││
│  │  └──────────┘ └───────────┘ └────────────┘ └──────────┘ │  ││
│  └───────────────────────────────────────────────────────────┘  ││
│                                                                   ││
│  ┌────────────────────────────────────────────────────────────┐  ││
│  │              Monitoring (CloudWatch)                        │  ││
│  │  Logs │ Metrics │ Alarms │ Dashboards                      │  ││
│  └────────────────────────────────────────────────────────────┘  ││
└─────────────────────────────────────────────────────────────────┘│
```

## Component Design

### 1. Stock Data Collector (Lambda + EventBridge)

**Trigger**: EventBridge scheduled rule — daily at 21:00 UTC (after US market close)

**Flow**:
1. Lambda invoked by EventBridge
2. Fetches watchlist from DB (1000+ tickers)
3. Calls yfinance in batches of 100 tickers (yfinance supports batch requests)
4. Falls back to Alpha Vantage for any failed tickers
5. Stores OHLCV data in `stock_data` table
6. Logs success/failure metrics to CloudWatch

**Lambda config**: 512MB RAM, 15-minute timeout, Python 3.12 runtime

### 2. News Collector (Lambda + EventBridge)

**Trigger**: EventBridge scheduled rule — every 15 minutes

**Flow**:
1. Lambda invoked by EventBridge
2. Polls NewsAPI and Finnhub for recent articles
3. Deduplicates against existing articles in DB (title + source hash)
4. For each new article, calls OpenAI GPT-4o-mini to generate structured summary
5. Stores summary with related tickers in `news_summaries` table

**Lambda config**: 256MB RAM, 5-minute timeout

### 3. AI Analyzer (Lambda + EventBridge)

**Trigger**: EventBridge scheduled rule — daily at 22:00 UTC (1 hour after stock collection)

**Flow**:
1. Lambda invoked by EventBridge
2. For each monitored stock (batched):
   - Retrieves last 30 days of OHLCV data
   - Retrieves last 7 days of news summaries related to ticker
   - Constructs prompt with technical indicators (SMA, RSI, MACD calculated via pandas)
   - Calls OpenAI GPT-4o-mini with structured output schema
   - Receives: short_term_recommendation, long_term_recommendation, risk_level, confidence_score, reasoning
3. Stores results in `analysis_results` table

**Lambda config**: 1024MB RAM, 15-minute timeout. May need Step Functions for 1000+ stocks to handle Lambda timeout — split into batches of 50 stocks per invocation.

**Prompt Strategy**:
```
Given the following data for {ticker}:
- 30-day OHLCV history: {data}
- Technical indicators: SMA(20), RSI(14), MACD
- Recent news summaries: {news}
- Sector: {sector}, Size: {size}

Classify this stock as BUY, HOLD, or SELL for:
1. Short-term (1-30 days)
2. Long-term (30+ days)

Assign a risk level: LOW, MEDIUM, or HIGH.
Provide a confidence score (0-100) and brief reasoning.
```

### 4. Portfolio Manager

**Encryption**: AES-256-GCM via AWS KMS
- Portfolio JSON serialized → encrypted with KMS data key → stored as base64 string in DB
- Decryption happens in Lambda memory only
- KMS key policy restricts access to Lambda execution role only

**Schema** (portfolios table):
```sql
CREATE TABLE portfolios (
    user_id UUID PRIMARY KEY REFERENCES users(id),
    encrypted_data TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

**Portfolio JSON structure** (before encryption):
```json
{
  "holdings": [
    {"ticker": "AAPL", "quantity": 50, "buying_price": 175.20, "added_date": "2025-03-15"},
    {"ticker": "MSFT", "quantity": 30, "buying_price": 420.00, "added_date": "2025-04-01"}
  ]
}
```

### 5. Authentication (AWS Cognito)

- User pool with email/password sign-up
- JWT tokens for API authorization
- Cognito handles: registration, login, password policy, account lockout, session management
- API Gateway authorizer validates JWT on every request
- Cost: Free tier covers 50,000 MAU

### 6. Web GUI (React SPA on S3 + CloudFront)

**Pages**:
- `/login` — Login/Register forms
- `/dashboard` — Portfolio view (landing page after login)
  - Portfolio table with sell indicators
  - "Buy suggestions" panel with filters
  - Add/remove stock controls
- `/settings` — User preferences (sector, size, risk filters)

**Key components**:
- `PortfolioTable` — Lists holdings with sell badges
- `BuySuggestions` — Filterable list of recommended buys
- `StockFilters` — Sector, size, risk dropdowns
- `AddStockModal` — Ticker input with validation

### 7. System Monitoring (CloudWatch)

**Metrics tracked**:
- Lambda invocation count, duration, errors (automatic)
- Custom metrics: stocks_collected, news_articles_processed, analysis_generated
- API Gateway: request count, latency, 4xx/5xx rates

**Alarms**:
- Batch job failure (stock collector or AI analyzer Lambda errors)
- Error rate > 5% on API endpoints
- DynamoDB throttling or elevated latency

**Logs**: Structured JSON logs via Python `structlog`, shipped to CloudWatch Logs

**Health endpoint**: `/api/health` returns component status + last successful batch timestamps

## Database Schema

```text
Single-table DynamoDB entities:
- STOCK#{ticker}/META: watchlist metadata and active flag
- STOCKDATA#{ticker}/DATE#{trading_date}: one OHLCV record per ticker/date
- NEWS#{title_source_hash}/META: deduplicated article summaries
- ANALYSIS#{ticker}/DATE#{analysis_date}: AI recommendations
- USER#{user_id}/PROFILE: Cognito user mirror
- USER#{user_id}/PORTFOLIO: encrypted portfolio string
- USER#{user_id}/PREFERENCES: suggestion filters
- DEMO_ACCOUNT#{account_id}/META plus GSI2 name lookup
- DEMO_HOLDING#{account_id}/TICKER#{ticker}
- DEMO_TXN#{account_id}/TS#{timestamp}#{id}
- DEMO_SNAPSHOT#{account_id}/DATE#{snapshot_date}

GSIs:
- GSI1: type/date or type/name lookups
- GSI2: alternate key lookups such as demo account name
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/auth/register | Register new user |
| POST | /api/auth/login | Login (delegates to Cognito) |
| GET | /api/portfolio | Get decrypted portfolio for current user |
| PUT | /api/portfolio/stocks | Add stock to portfolio |
| DELETE | /api/portfolio/stocks/{ticker} | Remove stock from portfolio |
| GET | /api/suggestions | Get personalized BUY/SELL suggestions |
| GET | /api/stocks | List monitored stocks (with filters) |
| GET | /api/stocks/{ticker}/analysis | Get latest analysis for a stock |
| GET | /api/preferences | Get user preferences |
| PUT | /api/preferences | Update user preferences |
| GET | /api/health | System health check |

## Deployment

**Infrastructure as Code**: AWS CDK (Python)
- Defines all resources: Lambda, API Gateway, DynamoDB, S3, CloudFront, Cognito, EventBridge, CloudWatch
- Single `cdk deploy` for full stack

**CI/CD**: GitHub Actions
- On push to `main`, `feature/**`, or `codex/**`: lint → test → build frontend → CDK synth → CDK deploy → `/api/health` smoke test
- `main` deploys the stable `prod` stage; feature branches deploy isolated branch-scoped stages

## Cost Estimate (Monthly)

| Service | Usage | Est. Cost |
|---------|-------|-----------|
| Lambda | ~50K invocations/month (batch + API) | $0 (free tier) |
| API Gateway | ~100K requests/month | $0.35 |
| DynamoDB on-demand | low-volume reads and writes | $1-5 |
| S3 + CloudFront | Frontend hosting, minimal traffic | $1-2 |
| Cognito | <50K users | $0 (free tier) |
| CloudWatch | Logs + metrics + alarms | $3-5 |
| OpenAI API | ~1000 stocks × 30 days × ~2K tokens | $15-25 |
| KMS | 1 key + decrypt calls | $1 |
| EventBridge | Scheduled rules | $0 |
| **Total (minimum load)** | | **~$65-77/month** |

### Cost Optimization Options

1. **Use DynamoDB on-demand**: default path, avoids minimum database capacity and keeps storage serverless
2. **Use Claude Haiku instead of GPT-4o-mini**: Similar cost, potentially better structured output
4. **Reduce analysis frequency**: Analyze only stocks with significant price/news changes

### Recommended Budget Path (≤$30/month):

| Service | Est. Cost |
|---------|-----------|
| Lambda + API Gateway | $0.35 |
| DynamoDB on-demand | $1-5 |
| S3 + CloudFront | $1-2 |
| Cognito | $0 |
| CloudWatch (basic) | $2-3 |
| OpenAI API (GPT-4o-mini) | $15-25 |
| KMS | $1 |
| **Total** | **~$20-30/month** |

If access patterns outgrow the single-table model, add targeted GSIs or evaluate a purpose-built analytics store for historical data.

## Security Considerations

- All data encrypted at rest (DynamoDB encryption, S3 encryption)
- All traffic over HTTPS (CloudFront + API Gateway)
- Portfolio data: AES-256-GCM via KMS, decrypted only in Lambda memory
- API authentication: JWT via Cognito
- Least-privilege IAM roles for each Lambda
- No secrets in code — all via AWS Secrets Manager or environment variables from CDK

## Project Structure

```
stocks/
├── infrastructure/          # AWS CDK
│   ├── app.py
│   └── stacks/
│       ├── api_stack.py
│       ├── database_stack.py
│       ├── frontend_stack.py
│       └── monitoring_stack.py
├── backend/
│   ├── src/
│   │   ├── collectors/
│   │   │   ├── stock_collector.py
│   │   │   └── news_collector.py
│   │   ├── analysis/
│   │   │   └── ai_analyzer.py
│   │   ├── api/
│   │   │   ├── auth.py
│   │   │   ├── portfolio.py
│   │   │   ├── suggestions.py
│   │   │   └── stocks.py
│   │   ├── services/
│   │   │   ├── portfolio_service.py
│   │   │   ├── suggestion_engine.py
│   │   │   └── encryption_service.py
│   │   ├── models/
│   │   │   └── schemas.py
│   │   └── db/
│   │       └── connection.py
│   ├── tests/
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── services/
│   │   └── App.tsx
│   ├── package.json
│   └── vite.config.ts
└── .kiro/
    └── specs/
        └── stock-monitoring-system/
            ├── requirements.md
            └── design.md
```
