# Implementation Plan: Stock Monitoring and Analysis System

## Overview

Build a serverless stock monitoring system on AWS using Python (FastAPI) for the backend, React + TypeScript for the frontend, DynamoDB for data storage, and OpenAI GPT-4o-mini for AI analysis. The system collects daily stock data and news, generates buy/hold/sell recommendations, and provides personalized suggestions through a web dashboard.

## Tasks

- [x] 1. Set up project structure and shared infrastructure
  - [x] 1.1 Initialize backend Python project with FastAPI
    - Create `backend/` directory with `src/`, `tests/` structure
    - Create `backend/requirements.txt` with dependencies: fastapi, mangum, boto3, yfinance, openai, pydantic, structlog, pandas
    - Create `backend/src/__init__.py` and module structure for collectors, analysis, api, services, models, db
    - _Requirements: 10.1_

  - [x] 1.2 Initialize frontend React + TypeScript project with Vite
    - Create `frontend/` with Vite React-TS template
    - Install dependencies: tailwindcss, @shadcn/ui, react-router-dom, axios
    - Configure Tailwind CSS and base layout
    - _Requirements: 10.1_

  - [x] 1.3 Set up AWS CDK infrastructure project
    - Create `infrastructure/` directory with `app.py` and `stacks/` folder
    - Create `infrastructure/requirements.txt` with aws-cdk-lib
    - Define empty stack files: `api_stack.py`, `database_stack.py`, `frontend_stack.py`, `monitoring_stack.py`
    - _Requirements: 10.1, 10.2_

  - [x] 1.4 Create database schema and access layer
    - Create CDK-provisioned DynamoDB single-table schema for stocks, stock_data, news, analysis, users, portfolios, and preferences
    - Create `backend/src/db/connection.py` with DynamoDB table access helpers using `STOCKARA_TABLE_NAME`
    - _Requirements: 1.2, 2.2, 3.1, 4.6, 5.1, 5.4_

  - [x] 1.5 Define core Pydantic models and schemas
    - Create `backend/src/models/schemas.py` with models: Stock, StockData, NewsSummary, AnalysisResult, Portfolio, PortfolioHolding, UserPreferences, Suggestion
    - Include validation rules: positive quantities, valid tickers, enum constraints for recommendation/risk/size
    - _Requirements: 1.2, 2.2, 3.2, 3.3, 4.2, 4.3, 4.4, 5.1_

- [x] 2. Checkpoint - Ensure project structure compiles and passes lint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Implement Stock Data Collector
  - [x] 3.1 Implement stock data collection Lambda handler
    - Create `backend/src/collectors/stock_collector.py`
    - Fetch watchlist from DB, batch tickers in groups of 100, call yfinance for OHLCV data
    - Implement Alpha Vantage fallback for failed tickers
    - Implement retry logic: 3 retries with exponential backoff starting at 2 seconds
    - Skip duplicate records (same ticker + trading_date), discard malformed records with logging
    - Emit structured logs and CloudWatch custom metrics (stocks_collected)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x] 3.2 Write unit tests for stock data collector
    - Test batch fetching logic, retry behavior, fallback to Alpha Vantage
    - Test duplicate detection and malformed data handling
    - Mock yfinance and Alpha Vantage responses
    - _Requirements: 1.3, 1.6, 1.7_

- [x] 4. Implement News Collector
  - [x] 4.1 Implement news collection Lambda handler
    - Create `backend/src/collectors/news_collector.py`
    - Poll NewsAPI and Finnhub at configurable interval (default 15 min)
    - Deduplicate articles using title + source hash
    - For each new article, call OpenAI GPT-4o-mini to generate structured summary (≤500 chars)
    - Store with related tickers, mark unclassified if no tickers identified
    - Handle source unavailability gracefully, raise alert if all sources fail
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

  - [x] 4.2 Write unit tests for news collector
    - Test deduplication logic, summary generation, error handling
    - Mock NewsAPI and Finnhub responses
    - _Requirements: 2.4, 2.5, 2.6, 2.7_

- [x] 5. Implement Stock Monitor and Watchlist Management
  - [x] 5.1 Implement watchlist CRUD operations
    - Create `backend/src/api/stocks.py` with FastAPI router
    - GET /api/stocks — list monitored stocks with filters (sector, size)
    - POST /api/stocks — add stock (require sector + size, validate constraints)
    - DELETE /api/stocks/{ticker} — remove stock
    - PUT /api/stocks/{ticker} — update stock metadata
    - Load seed watchlist from `data/watchlist_seed.csv` via migration or init script
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [x] 5.2 Write unit tests for watchlist API
    - Test CRUD operations, validation errors, not-found handling
    - _Requirements: 3.4, 3.5, 3.6, 3.7_

- [x] 6. Implement AI Analyzer
  - [x] 6.1 Implement AI analysis Lambda handler
    - Create `backend/src/analysis/ai_analyzer.py`
    - Retrieve 30 days OHLCV data and 7 days news summaries per ticker
    - Calculate technical indicators (SMA-20, RSI-14, MACD) using pandas
    - Construct prompt and call OpenAI GPT-4o-mini with structured output schema
    - Parse response: short_term_recommendation, long_term_recommendation, risk_level, confidence_score, reasoning
    - Store results in analysis_results table
    - Batch stocks in groups of 50 to handle Lambda timeout
    - Log failures per stock and continue processing
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [x] 6.2 Write unit tests for AI analyzer
    - Test technical indicator calculations, prompt construction, response parsing
    - Test failure handling and batching logic
    - Mock OpenAI API responses
    - _Requirements: 4.5, 4.7_

- [x] 7. Checkpoint - Ensure all backend batch components work
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement Portfolio Manager and Encryption
  - [x] 8.1 Implement encryption service
    - Create `backend/src/services/encryption_service.py`
    - Implement AES-256-GCM encryption/decryption using AWS KMS data keys
    - Encrypt portfolio JSON → base64 string for storage
    - Decrypt in memory only, never expose partial data on failure
    - _Requirements: 5.1, 5.3, 5.4, 5.5_

  - [x] 8.2 Implement portfolio API endpoints
    - Create `backend/src/api/portfolio.py` with FastAPI router
    - GET /api/portfolio — decrypt and return portfolio for authenticated user
    - PUT /api/portfolio/stocks — add stock to portfolio (validate ticker exists in watchlist, positive quantity/price)
    - DELETE /api/portfolio/stocks/{ticker} — remove stock from portfolio
    - Enforce user authorization (only own portfolio accessible)
    - Return validation errors for invalid data without modifying stored portfolio
    - _Requirements: 5.1, 5.2, 5.3, 5.6, 5.7_

  - [x] 8.3 Write unit tests for portfolio manager
    - Test encryption/decryption round-trip, validation errors, authorization
    - Mock KMS calls
    - _Requirements: 5.1, 5.4, 5.5, 5.6, 5.7_

- [x] 9. Implement Suggestion Engine
  - [x] 9.1 Implement suggestion engine service
    - Create `backend/src/services/suggestion_engine.py`
    - Compare user portfolio against latest analysis to find SELL recommendations
    - Find BUY recommendations for stocks not in portfolio
    - Apply filters: sector, company size, risk level preferences
    - Rank BUY suggestions by confidence score descending
    - Handle missing portfolio (error) and stale analysis (use most recent, include date)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9_

  - [x] 9.2 Implement suggestions API endpoint
    - Create `backend/src/api/suggestions.py` with FastAPI router
    - GET /api/suggestions — return personalized suggestions with filters
    - GET /api/stocks/{ticker}/analysis — return latest analysis for a stock
    - _Requirements: 6.1, 6.2, 6.3_

  - [x] 9.3 Write unit tests for suggestion engine
    - Test filtering, ranking, error handling for missing portfolio/analysis
    - _Requirements: 6.4, 6.5, 6.6, 6.7, 6.8, 6.9_

- [x] 10. Implement Authentication
  - [x] 10.1 Implement Cognito authentication integration
    - Create `backend/src/api/auth.py` with FastAPI router
    - POST /api/auth/register — register user via Cognito (validate password policy)
    - POST /api/auth/login — authenticate via Cognito, return JWT
    - Configure Cognito password policy: 8-128 chars, uppercase, lowercase, digit
    - Configure account lockout after 5 failed attempts for 15 minutes
    - Configure session timeout at 30 minutes of inactivity
    - Create middleware/dependency for JWT validation on protected routes
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

  - [x] 10.2 Write unit tests for auth endpoints
    - Test registration validation, login errors, token verification
    - Mock Cognito responses
    - _Requirements: 7.2, 7.4, 7.5, 7.6_

- [x] 11. Implement User Preferences
  - [x] 11.1 Implement preferences API endpoints
    - Create routes in `backend/src/api/suggestions.py` or separate file
    - GET /api/preferences — return user preferences
    - PUT /api/preferences — update preferred sectors, sizes, max risk level
    - _Requirements: 6.4, 6.5, 6.6_

- [x] 12. Checkpoint - Ensure all backend APIs work end-to-end
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Implement Frontend - Authentication Pages
  - [x] 13.1 Implement login and registration pages
    - Create `frontend/src/pages/Login.tsx` with login/register forms
    - Implement form validation matching password policy (8+ chars, uppercase, lowercase, digit)
    - Display generic error messages on login failure
    - Display account lock message after 5 failed attempts
    - Integrate with backend auth API via axios service
    - Store JWT in memory/localStorage, set up axios interceptor for auth headers
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

- [x] 14. Implement Frontend - Portfolio Dashboard
  - [x] 14.1 Implement portfolio view component
    - Create `frontend/src/pages/Dashboard.tsx` as landing page after login
    - Create `frontend/src/components/PortfolioTable.tsx` showing holdings: ticker, company name, sector, size, buying price, profit/loss
    - Calculate profit/loss from buying price vs latest closing price
    - Display "Sell" badge next to stocks with SELL recommendation
    - _Requirements: 8.1, 8.4_

  - [x] 14.2 Implement add/remove stock functionality
    - Create `frontend/src/components/AddStockModal.tsx` with ticker input and validation
    - Implement delete confirmation dialog before removing stock
    - Show error messages for invalid ticker or duplicate stock
    - _Requirements: 8.2, 8.3, 8.7_

  - [x] 14.3 Implement buy suggestions panel
    - Create `frontend/src/components/BuySuggestions.tsx` showing up to 20 suggested stocks
    - Display ticker, company name, sector, size, risk level for each
    - Create `frontend/src/components/StockFilters.tsx` with sector, size, risk dropdowns
    - Show "no suggestions match" message when filters return empty
    - _Requirements: 8.5, 8.6, 8.8_

- [x] 15. Implement Frontend - Settings Page
  - [x] 15.1 Implement user preferences page
    - Create `frontend/src/pages/Settings.tsx`
    - Multi-select for preferred sectors, company sizes
    - Dropdown for max risk level
    - Save preferences via API
    - _Requirements: 6.4, 6.5, 6.6_

- [x] 16. Checkpoint - Ensure frontend builds and integrates with backend
  - Ensure all tests pass, ask the user if questions arise.

- [x] 17. Implement System Monitoring and Health
  - [x] 17.1 Implement health check endpoint and structured logging
    - Create GET /api/health endpoint returning component status and last batch timestamps
    - Configure structlog for JSON-formatted structured logs across all components
    - Add request duration logging middleware, log slow queries (>5s)
    - _Requirements: 9.1, 9.5, 9.7_

  - [x] 17.2 Configure CloudWatch monitoring in CDK
    - Define custom metrics: stocks_collected, news_articles_processed, analysis_generated
    - Create alarms: error rate >5% in 5-min window, batch job failure
    - Configure log retention: 30 days for logs, 90 days for metrics
    - Create CloudWatch dashboard for key metrics
    - _Requirements: 9.2, 9.3, 9.4, 9.6_

- [x] 18. Implement AWS CDK Infrastructure Stacks
  - [x] 18.1 Implement database and auth CDK stack
    - Define DynamoDB single-table resources in `infrastructure/stacks/database_stack.py`
    - Define Cognito User Pool with password policy and lockout settings
    - Define KMS key for portfolio encryption
    - _Requirements: 10.1, 10.2, 7.5, 7.6_

  - [x] 18.2 Implement API and compute CDK stack
    - Define Lambda functions for: stock_collector, news_collector, ai_analyzer, api_handler
    - Define API Gateway with Cognito authorizer
    - Define EventBridge rules: stock collection at 21:00 UTC, news every 15 min, AI analysis at 22:00 UTC
    - Configure Lambda memory, timeouts, and IAM roles (least-privilege)
    - _Requirements: 10.1, 10.2, 10.4_

  - [x] 18.3 Implement frontend hosting CDK stack
    - Define S3 bucket for React SPA
    - Define CloudFront distribution with S3 origin
    - Configure HTTPS and custom domain (optional)
    - _Requirements: 10.1, 10.3_

  - [x] 18.4 Implement monitoring CDK stack
    - Define CloudWatch alarms, log groups, dashboards, and metric filters
    - Define SNS topic for alert notifications
    - _Requirements: 9.2, 9.3, 9.4, 9.6_

- [x] 19. Implement CI/CD Pipeline
  - [x] 19.1 Create GitHub Actions workflow
    - Create `.github/workflows/deploy.yml`
    - Steps: lint → pytest → build frontend (vite build) → CDK synth → CDK deploy → deployed health smoke test
    - Trigger deployment for `main`, `feature/**`, and `codex/**` branch pushes
    - Configure AWS credentials via OIDC or secrets
    - _Requirements: 10.1_

- [x] 20. Final checkpoint - Ensure full system deploys and all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- The backend uses Python (FastAPI + Mangum for Lambda), frontend uses React + TypeScript
- CDK DynamoDB table/index changes should be synthesized before testing dependent components
- The seed watchlist at `data/watchlist_seed.csv` is preloaded during initial setup
- Unit tests mock external services (yfinance, OpenAI, Cognito, KMS) to run without credentials

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["1.4", "1.5"] },
    { "id": 2, "tasks": ["3.1", "4.1", "5.1"] },
    { "id": 3, "tasks": ["3.2", "4.2", "5.2", "6.1"] },
    { "id": 4, "tasks": ["6.2", "8.1"] },
    { "id": 5, "tasks": ["8.2", "9.1", "10.1"] },
    { "id": 6, "tasks": ["8.3", "9.2", "9.3", "10.2", "11.1"] },
    { "id": 7, "tasks": ["13.1"] },
    { "id": 8, "tasks": ["14.1", "14.2", "14.3", "15.1"] },
    { "id": 9, "tasks": ["17.1", "17.2"] },
    { "id": 10, "tasks": ["18.1", "18.2", "18.3", "18.4"] },
    { "id": 11, "tasks": ["19.1"] }
  ]
}
```
