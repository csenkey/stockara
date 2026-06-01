# Stockara — Stock Monitoring & Analysis System

A serverless stock monitoring platform that tracks 1000+ tickers, generates AI-powered trading recommendations, and simulates 100 demo trading accounts. Built with Python/FastAPI, React/TypeScript, and deployed on AWS.

## Features

- **Stock Data Collection** — Daily OHLCV data from Yahoo Finance with Alpha Vantage fallback
- **News Aggregation** — Automated collection and AI summarization from NewsAPI and Finnhub
- **AI Analysis** — GPT-4o-mini generates BUY/HOLD/SELL recommendations with confidence scores
- **Portfolio Management** — AES-256-GCM encrypted personal portfolios with sell indicators
- **Personalized Suggestions** — Filtered buy recommendations based on sector, size, and risk preferences
- **Demo Trading Accounts** — 100 superhero-named simulated accounts trading autonomously
- **Public Leaderboard** — Real-time rankings with sparkline charts and detailed account pages

## Architecture

```
AWS Lambda (Python 3.12) + API Gateway + PostgreSQL (RDS Serverless v2)
React SPA on S3/CloudFront | Cognito Auth | EventBridge Scheduling
```

Key services:
- **Stock Collector** — EventBridge → Lambda, daily at 21:00 UTC
- **News Collector** — EventBridge → Lambda, every 15 minutes
- **AI Analyzer** — EventBridge → Lambda, daily at 22:00 UTC
- **Demo Trade Executor** — EventBridge → Lambda, daily at 22:30 UTC

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, Pydantic, structlog |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, Recharts |
| Database | PostgreSQL (RDS Serverless v2) |
| Auth | AWS Cognito (JWT) |
| AI | OpenAI GPT-4o-mini |
| Infrastructure | AWS CDK (Python), GitHub Actions CI/CD |
| Testing | pytest, Hypothesis (property-based testing) |

## Project Structure

```
stocks/
├── backend/
│   ├── src/
│   │   ├── api/            # FastAPI routers (auth, portfolio, stocks, demo)
│   │   ├── collectors/     # Stock and news data collectors
│   │   ├── analysis/       # AI analyzer
│   │   ├── services/       # Business logic (demo accounts, encryption, suggestions)
│   │   ├── models/         # Pydantic schemas
│   │   ├── db/             # Connection pool + migrations
│   │   └── scripts/        # Seed scripts
│   └── tests/              # pytest + Hypothesis property tests
├── frontend/
│   ├── src/
│   │   ├── pages/          # Dashboard, Settings, DemoLeaderboard, DemoAccountDetail
│   │   ├── components/     # Layout, shared UI
│   │   └── services/       # API client (axios)
│   └── package.json
├── infrastructure/
│   ├── stacks/             # CDK stacks (API, DB, Frontend, Monitoring, DemoTrading)
│   └── app.py
└── .github/workflows/      # CI/CD pipeline
```

## Getting Started

### Prerequisites

- Python 3.12+
- Node.js 18+
- PostgreSQL (local or remote)
- AWS CLI (for deployment)

### Backend

```bash
cd backend
pip install -r requirements.txt
python -m pytest tests/ -v
```

### Frontend

```bash
cd frontend
npm install
npm run dev     # development server
npm run build   # production build
```

### Deploy

```bash
cd infrastructure
pip install -r requirements.txt
npm install -g aws-cdk
cdk deploy --all
```

## Demo Trading Accounts

The system includes 100 simulated trading accounts, each named after a superhero:

- Start with $10,000 bankroll
- Trade daily based on AI recommendations
- 1% commission on every transaction
- Public leaderboard at `/demo` (no auth required)
- Account detail pages with portfolio charts, holdings, and transaction history

### API Endpoints (Public, No Auth)

| Endpoint | Description |
|----------|-------------|
| `GET /api/demo/leaderboard` | All 100 accounts ranked by portfolio value |
| `GET /api/demo/accounts/{name}` | Account detail with holdings and allocation |
| `GET /api/demo/accounts/{name}/transactions` | Paginated transaction history |
| `GET /api/demo/accounts/{name}/performance` | Daily portfolio value time series |

## Testing

The project uses **property-based testing** (Hypothesis) to verify correctness invariants:

- Initial bankroll invariant (cash + holdings = $10,000)
- Commission is always exactly 1%
- Buy allocation capped at 10% of portfolio value
- Sell liquidates entire position
- Leaderboard sorted by portfolio value descending

Run all tests:
```bash
cd backend
python -m pytest tests/ -v
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `OPENAI_API_KEY` | OpenAI API key for AI analysis |
| `AWS_REGION` | AWS deployment region |
| `COGNITO_USER_POOL_ID` | Cognito user pool ID |
| `KMS_KEY_ID` | KMS key for portfolio encryption |

## License

Private — all rights reserved.
