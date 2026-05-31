-- Migration: 002_demo_trading_accounts
-- Description: Create demo trading accounts schema for simulated trading feature
-- Date: 2025-01-01

-- Demo trading accounts
CREATE TABLE demo_accounts (
    id SERIAL PRIMARY KEY,
    account_name VARCHAR(100) UNIQUE NOT NULL,
    cash_balance DECIMAL(12,2) NOT NULL DEFAULT 10000.00,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Demo account stock holdings
CREATE TABLE demo_holdings (
    id BIGSERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES demo_accounts(id),
    ticker VARCHAR(10) NOT NULL REFERENCES stocks(ticker),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    purchase_price DECIMAL(12,4) NOT NULL,
    purchased_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(account_id, ticker)
);

-- Demo account transactions
CREATE TABLE demo_transactions (
    id BIGSERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES demo_accounts(id),
    ticker VARCHAR(10) NOT NULL,
    action VARCHAR(4) NOT NULL CHECK (action IN ('BUY', 'SELL')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    price_per_share DECIMAL(12,4) NOT NULL,
    total_value DECIMAL(12,2) NOT NULL,
    commission_fee DECIMAL(12,2) NOT NULL,
    cash_after DECIMAL(12,2) NOT NULL,
    executed_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Daily portfolio snapshots for time series
CREATE TABLE demo_daily_snapshots (
    id BIGSERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES demo_accounts(id),
    snapshot_date DATE NOT NULL,
    portfolio_value DECIMAL(12,2) NOT NULL,
    cash_balance DECIMAL(12,2) NOT NULL,
    holdings_value DECIMAL(12,2) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(account_id, snapshot_date)
);

-- Indexes
CREATE INDEX idx_demo_holdings_account ON demo_holdings(account_id);
CREATE INDEX idx_demo_transactions_account ON demo_transactions(account_id);
CREATE INDEX idx_demo_transactions_executed ON demo_transactions(executed_at DESC);
CREATE INDEX idx_demo_snapshots_account_date ON demo_daily_snapshots(account_id, snapshot_date);
