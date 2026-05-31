# Requirements Document

## Introduction

The Demo Trading Accounts feature extends the Stock Monitoring and Analysis System with 100 simulated trading accounts that autonomously execute trades based on existing AI recommendations. Each account is named after a superhero, starts with a $10,000 bankroll, and pays 1% commission on every transaction. A public webpage (no authentication required) displays portfolio performance graphs, a leaderboard, and full transaction history for all demo accounts.

## Glossary

- **Demo_Account_Manager**: The component responsible for creating, storing, and managing the 100 demo trading accounts
- **Demo_Trade_Executor**: The component responsible for executing simulated trades on demo accounts based on AI recommendations
- **Demo_Public_Dashboard**: The publicly accessible webpage displaying demo account performance, leaderboard, and transaction history
- **Demo_Account**: A simulated trading account with a unique superhero name, a cash balance, and stock holdings
- **Commission_Fee**: A 1% fee deducted from the total transaction value on every buy or sell action
- **Transaction**: A single buy or sell action executed on a Demo_Account, recording ticker, quantity, price, commission, and timestamp
- **Portfolio_Value**: The sum of a Demo_Account's cash balance and the market value of all held stocks at current closing prices
- **Leaderboard**: A ranked list of all Demo_Accounts ordered by total Portfolio_Value

## Requirements

### Requirement 1: Demo Account Creation

**User Story:** As a system operator, I want 100 demo accounts created and seeded with initial data, so that the system can simulate trading activity for public display.

#### Acceptance Criteria

1. THE Demo_Account_Manager SHALL create exactly 100 Demo_Accounts, each with a unique superhero name (e.g., "Spider-Man", "Batman", "Wonder Woman")
2. THE Demo_Account_Manager SHALL assign each Demo_Account an initial bankroll of exactly $10,000.00 USD
3. WHEN a Demo_Account is created, THE Demo_Account_Manager SHALL randomly allocate the $10,000.00 between cash and stock holdings, where cash is at least $500.00 and at most $9,500.00
4. WHEN allocating initial stock holdings, THE Demo_Account_Manager SHALL select stocks only from the active Stock_Monitor watchlist and assign random quantities at current market closing prices
5. WHEN allocating initial stock holdings, THE Demo_Account_Manager SHALL deduct a 1% Commission_Fee from each initial stock purchase transaction
6. THE Demo_Account_Manager SHALL store each Demo_Account with its superhero name, cash balance, stock holdings (ticker, quantity, purchase price), and creation timestamp
7. IF fewer than 10 active stocks exist in the Stock_Monitor watchlist at creation time, THEN THE Demo_Account_Manager SHALL reject account creation and log an error indicating insufficient stocks available

### Requirement 2: Daily Automated Trading

**User Story:** As a system operator, I want demo accounts to automatically trade based on AI recommendations each day, so that the demo accounts reflect realistic simulated trading behavior.

#### Acceptance Criteria

1. WHEN new daily AI analysis results are available, THE Demo_Trade_Executor SHALL evaluate the latest recommendations for each stock held or available to each Demo_Account
2. WHEN the AI_Analyzer recommends BUY for a stock not held by a Demo_Account, THE Demo_Trade_Executor SHALL purchase shares of that stock using available cash, allocating up to 10% of the Demo_Account's total Portfolio_Value per single buy transaction
3. WHEN the AI_Analyzer recommends SELL for a stock held by a Demo_Account, THE Demo_Trade_Executor SHALL sell all shares of that stock from the Demo_Account
4. THE Demo_Trade_Executor SHALL deduct a Commission_Fee of exactly 1% of the total transaction value from the Demo_Account cash balance for each buy or sell Transaction
5. WHEN executing a buy Transaction, THE Demo_Trade_Executor SHALL use the stock's latest closing price as the purchase price and calculate the maximum whole shares purchasable within the allocated budget minus Commission_Fee
6. WHEN executing a sell Transaction, THE Demo_Trade_Executor SHALL use the stock's latest closing price as the sale price and credit the Demo_Account cash balance with the sale proceeds minus Commission_Fee
7. IF a Demo_Account has insufficient cash to purchase at least 1 share of a recommended BUY stock (including Commission_Fee), THEN THE Demo_Trade_Executor SHALL skip that buy Transaction for that Demo_Account and log the skip reason
8. THE Demo_Trade_Executor SHALL record each Transaction with the Demo_Account name, ticker, action (BUY or SELL), quantity, price per share, total value, Commission_Fee amount, and execution timestamp
9. WHEN the AI_Analyzer recommends HOLD for a stock, THE Demo_Trade_Executor SHALL take no action on that stock for the Demo_Account

### Requirement 3: Transaction History

**User Story:** As a public visitor, I want to see the complete transaction history of each demo account, so that I can understand the trading decisions made over time.

#### Acceptance Criteria

1. THE Demo_Account_Manager SHALL store a permanent record of every Transaction executed on every Demo_Account
2. THE Demo_Account_Manager SHALL make transaction records available with the following fields: Demo_Account name, date, ticker symbol, action (BUY or SELL), quantity, price per share, total transaction value, Commission_Fee amount, and resulting cash balance after the transaction
3. WHEN a Transaction is recorded, THE Demo_Account_Manager SHALL also store a daily portfolio snapshot containing the Demo_Account's total Portfolio_Value, cash balance, and list of held stocks with quantities and current market values
4. THE Demo_Account_Manager SHALL retain all transaction records and daily snapshots indefinitely for historical viewing

### Requirement 4: Public Dashboard - Leaderboard

**User Story:** As a public visitor, I want to see a ranked leaderboard of all demo accounts, so that I can quickly identify which simulated strategies perform best.

#### Acceptance Criteria

1. THE Demo_Public_Dashboard SHALL display a Leaderboard ranking all 100 Demo_Accounts by total Portfolio_Value in descending order
2. THE Demo_Public_Dashboard SHALL display for each Demo_Account on the Leaderboard: rank position, superhero name, current total Portfolio_Value, cash balance, total gain or loss percentage since inception, and number of transactions executed
3. THE Demo_Public_Dashboard SHALL be accessible without authentication or login
4. THE Demo_Public_Dashboard SHALL update the Leaderboard data at least once per day after daily trading completes
5. WHEN a visitor views the Leaderboard, THE Demo_Public_Dashboard SHALL display the timestamp of the last data update

### Requirement 5: Public Dashboard - Portfolio Performance Graphs

**User Story:** As a public visitor, I want to see portfolio value graphs over time for each demo account, so that I can visualize performance trends.

#### Acceptance Criteria

1. WHEN a visitor selects a Demo_Account, THE Demo_Public_Dashboard SHALL display a line graph showing the total Portfolio_Value over time from account creation to the most recent trading day
2. THE Demo_Public_Dashboard SHALL display the graph with the X-axis representing calendar dates and the Y-axis representing Portfolio_Value in USD
3. THE Demo_Public_Dashboard SHALL plot one data point per trading day on the portfolio value graph
4. THE Demo_Public_Dashboard SHALL display the initial $10,000 starting value as a horizontal reference line on the graph for comparison
5. WHEN a visitor views the Leaderboard, THE Demo_Public_Dashboard SHALL display a small sparkline graph next to each Demo_Account showing a condensed portfolio value trend

### Requirement 6: Public Dashboard - Account Detail Page

**User Story:** As a public visitor, I want to view detailed information about a specific demo account, so that I can analyze its holdings, performance, and trade history.

#### Acceptance Criteria

1. WHEN a visitor selects a Demo_Account from the Leaderboard, THE Demo_Public_Dashboard SHALL navigate to an account detail page showing the superhero name, current Portfolio_Value, cash balance, and total gain or loss
2. THE Demo_Public_Dashboard SHALL display the Demo_Account's current stock holdings with ticker, quantity, purchase price, current market price, and unrealized gain or loss per holding
3. THE Demo_Public_Dashboard SHALL display a paginated transaction history table sorted by date descending, showing date, ticker, action, quantity, price, total value, and Commission_Fee for each Transaction
4. THE Demo_Public_Dashboard SHALL display a portfolio value line chart on the account detail page showing performance over time
5. THE Demo_Public_Dashboard SHALL provide navigation to return to the Leaderboard from the account detail page
6. THE Demo_Public_Dashboard SHALL display a portfolio composition pie chart showing the percentage allocation across held stocks and cash

### Requirement 7: Public API Endpoints for Demo Accounts

**User Story:** As a frontend developer, I want API endpoints to retrieve demo account data, so that the public dashboard can display up-to-date information.

#### Acceptance Criteria

1. THE system SHALL expose a public API endpoint that returns the Leaderboard data for all 100 Demo_Accounts without requiring authentication
2. THE system SHALL expose a public API endpoint that returns the detailed portfolio and current holdings for a specified Demo_Account by name without requiring authentication
3. THE system SHALL expose a public API endpoint that returns the paginated transaction history for a specified Demo_Account by name without requiring authentication
4. THE system SHALL expose a public API endpoint that returns the daily portfolio value time series for a specified Demo_Account by name without requiring authentication
5. WHEN a requested Demo_Account name does not match any existing account, THE system SHALL return a 404 error response with a message indicating the account was not found
6. THE system SHALL respond to all public demo account API requests within 2 seconds under normal load

