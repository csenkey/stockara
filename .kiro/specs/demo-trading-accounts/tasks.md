# Implementation Plan: Demo Trading Accounts

## Overview

Implement 100 simulated trading accounts with daily automated trading based on AI recommendations, a public API layer, and a React frontend with leaderboard and account detail pages. The backend uses Python/FastAPI with PostgreSQL; the frontend uses React/TypeScript with Recharts for charting.

## Tasks

- [x] 1. Database schema and data models
  - [x] 1.1 Create database migration `002_demo_trading_accounts.sql`
    - Add `demo_accounts`, `demo_holdings`, `demo_transactions`, and `demo_daily_snapshots` tables
    - Add all indexes and constraints as specified in the design
    - Reference existing `stocks` table for foreign key on `demo_holdings.ticker`
    - _Requirements: 1.6, 3.1, 3.3, 3.4_

  - [x] 1.2 Create Pydantic models for demo accounts
    - Create `backend/src/models/demo_schemas.py`
    - Define `DemoAccount`, `DemoHolding`, `DemoTransaction`, `DailySnapshot`, `LeaderboardEntry`, `LeaderboardResponse`, `AccountDetailResponse`, `AllocationEntry`, `PaginatedTransactionsResponse`, `PerformanceResponse`
    - _Requirements: 3.2, 4.2, 5.1, 6.1, 6.2, 6.3, 7.1–7.4_

- [x] 2. DemoAccountManager service
  - [x] 2.1 Implement `DemoAccountManager` class
    - Create `backend/src/services/demo_account_manager.py`
    - Implement `create_accounts()`: create 100 accounts with superhero names, random allocation between $500–$9500 cash, initial stock purchases from active watchlist with 1% commission
    - Implement `get_account()`, `get_leaderboard()`, `get_transactions()`, `get_performance_series()`
    - Implement `record_transaction()` and `take_daily_snapshot()`
    - Reject creation if fewer than 10 active stocks, log error
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 3.1, 3.2, 3.3, 4.1, 4.2_

  - [x] 2.2 Create superhero seed data
    - Create `backend/src/services/demo_superhero_names.py` with a list of 100 unique superhero names
    - _Requirements: 1.1_

  - [x] 2.3 Write property tests for account creation (Properties 1, 2, 3)
    - **Property 1: Initial bankroll invariant** — cash + sum(qty × price × 1.01) == $10,000
    - **Property 2: Holdings only from active watchlist** — all tickers in holdings are active
    - **Property 3: Commission is always exactly 1%** — commission_fee == 0.01 × total_value for initial purchases
    - **Validates: Requirements 1.2, 1.3, 1.4, 1.5**

- [x] 3. DemoTradeExecutor service
  - [x] 3.1 Implement `DemoTradeExecutor` class
    - Create `backend/src/services/demo_trade_executor.py`
    - Implement `execute_daily_trades()`: fetch latest AI recommendations, iterate all 100 accounts
    - Implement `_evaluate_account()`: determine buy/sell actions based on recommendations
    - Implement `_execute_buy()`: allocate up to 10% of portfolio value, calculate max whole shares at closing price minus commission, skip if insufficient cash
    - Implement `_execute_sell()`: sell all shares at closing price, credit proceeds minus commission
    - Record each transaction with all required fields
    - Take daily snapshots after processing each account
    - Process in batches of 25 for partial progress on timeout
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3.3_

  - [x] 3.2 Write property tests for trade execution (Properties 4, 5, 6, 7, 8, 9)
    - **Property 4: Buy allocation capped at 10% of portfolio value**
    - **Property 5: Sell liquidates entire position**
    - **Property 6: Buy quantity = floor(budget / (price × 1.01))**
    - **Property 7: Sell credit = quantity × price × 0.99**
    - **Property 8: Insufficient cash prevents buy**
    - **Property 9: HOLD produces no transaction**
    - **Validates: Requirements 2.2, 2.3, 2.5, 2.6, 2.7, 2.9**

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Public API endpoints
  - [x] 5.1 Create public demo API router
    - Create `backend/src/api/demo.py` with `APIRouter(prefix="/api/demo")`
    - Implement `GET /leaderboard` — returns all 100 accounts ranked by portfolio value
    - Implement `GET /accounts/{name}` — returns account detail with holdings and allocation
    - Implement `GET /accounts/{name}/transactions` — returns paginated transaction history (default page_size=20)
    - Implement `GET /accounts/{name}/performance` — returns daily portfolio value time series
    - Return 404 with descriptive message for non-existent accounts
    - Register router in main FastAPI app without auth middleware
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 5.2 Write unit tests for public API endpoints
    - Test leaderboard returns 200 with correct structure
    - Test account detail returns 200 for valid name
    - Test 404 for non-existent account name
    - Test pagination parameters work correctly
    - Test no auth required on all endpoints
    - **Property 12: Leaderboard sorted by portfolio value descending**
    - **Property 13: Transaction history sorted by date descending**
    - **Validates: Requirements 4.1, 6.3, 7.1–7.6**

- [x] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Frontend - Leaderboard page
  - [x] 7.1 Create DemoLeaderboard page component
    - Create `frontend/src/pages/DemoLeaderboard.tsx`
    - Fetch leaderboard data from `/api/demo/leaderboard`
    - Render table with columns: rank, superhero name, portfolio value, cash balance, gain/loss %, transaction count
    - Add sparkline chart per row using Recharts `LineChart` (condensed, no axes)
    - Display last updated timestamp
    - Click row navigates to `/demo/:name`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 5.5_

  - [x] 7.2 Add routing for demo pages
    - Add `/demo` route for `DemoLeaderboard` in `App.tsx`
    - Add `/demo/:name` route for `DemoAccountDetail` in `App.tsx`
    - These routes should be outside the authenticated Layout (public access)
    - _Requirements: 4.3, 6.5_

- [x] 8. Frontend - Account Detail page
  - [x] 8.1 Create DemoAccountDetail page component
    - Create `frontend/src/pages/DemoAccountDetail.tsx`
    - Fetch account detail from `/api/demo/accounts/:name`
    - Display header: superhero name, portfolio value, cash balance, total gain/loss
    - Render portfolio value line chart (Recharts `LineChart`) with $10,000 reference line using `ReferenceLine`
    - Render portfolio composition pie chart (Recharts `PieChart`) showing stock allocations and cash
    - Display current holdings table with ticker, quantity, purchase price, current price, unrealized P&L
    - Display paginated transaction history table (date, ticker, action, quantity, price, total value, commission)
    - Add back-to-leaderboard navigation link
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [x] 9. CDK infrastructure update
  - [x] 9.1 Add EventBridge rule and Lambda configuration
    - Update CDK stack to add EventBridge rule triggering at 22:30 UTC daily
    - Configure Lambda to invoke `DemoTradeExecutor.execute_daily_trades()`
    - Grant Lambda read access to `analysis_results` and `stock_data` tables
    - Grant Lambda read/write access to demo tables
    - _Requirements: 2.1_

- [x] 10. Seed script and integration wiring
  - [x] 10.1 Create seed script for initial demo account setup
    - Create `backend/src/scripts/seed_demo_accounts.py`
    - Call `DemoAccountManager.create_accounts(100)` with proper DB connection
    - Log results (accounts created, initial allocations summary)
    - Handle error case: fewer than 10 active stocks
    - _Requirements: 1.1, 1.7_

  - [x] 10.2 Write property test for transaction persistence round-trip (Property 10)
    - **Property 10: Transaction persistence round-trip** — recorded transaction matches queried transaction on all fields
    - **Validates: Requirements 2.8, 3.1, 3.2**

  - [x] 10.3 Write property test for daily snapshot completeness (Property 11)
    - **Property 11: Daily snapshot exists for each trading day** — if any transaction on a day, all accounts have a snapshot
    - **Validates: Requirements 3.3**

- [x] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document using Hypothesis
- Unit tests validate specific examples and edge cases using pytest
- Backend is Python/FastAPI; Frontend is React/TypeScript with Recharts
- The public demo pages do not require authentication and are separate from the authenticated Layout

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "2.2"] },
    { "id": 1, "tasks": ["2.1"] },
    { "id": 2, "tasks": ["2.3", "3.1"] },
    { "id": 3, "tasks": ["3.2", "5.1"] },
    { "id": 4, "tasks": ["5.2", "7.1", "7.2", "9.1", "10.1"] },
    { "id": 5, "tasks": ["8.1", "10.2", "10.3"] }
  ]
}
```
