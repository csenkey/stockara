-- Migration: 001_initial_schema
-- Description: Create initial database schema for Stock Monitoring System
-- Date: 2025-01-01

-- Stock watchlist
CREATE TABLE stocks (
    ticker VARCHAR(10) PRIMARY KEY,
    company_name VARCHAR(255) NOT NULL,
    sector VARCHAR(50) NOT NULL,
    company_size VARCHAR(10) NOT NULL CHECK (company_size IN ('blue_chip', 'mid_cap', 'startup')),
    added_at TIMESTAMP NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Daily OHLCV data
CREATE TABLE stock_data (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL REFERENCES stocks(ticker),
    trading_date DATE NOT NULL,
    open_price DECIMAL(12,4) NOT NULL,
    high_price DECIMAL(12,4) NOT NULL,
    low_price DECIMAL(12,4) NOT NULL,
    close_price DECIMAL(12,4) NOT NULL,
    volume BIGINT NOT NULL,
    collected_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(ticker, trading_date)
);

-- News summaries
CREATE TABLE news_summaries (
    id BIGSERIAL PRIMARY KEY,
    title VARCHAR(500) NOT NULL,
    source VARCHAR(100) NOT NULL,
    published_at TIMESTAMP NOT NULL,
    tickers VARCHAR(10)[] DEFAULT '{}',
    summary TEXT NOT NULL,
    is_classified BOOLEAN NOT NULL DEFAULT TRUE,
    collected_at TIMESTAMP NOT NULL DEFAULT NOW(),
    title_source_hash VARCHAR(64) UNIQUE NOT NULL
);

-- AI analysis results
CREATE TABLE analysis_results (
    id BIGSERIAL PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL REFERENCES stocks(ticker),
    analysis_date DATE NOT NULL,
    short_term_recommendation VARCHAR(4) NOT NULL CHECK (short_term_recommendation IN ('BUY', 'HOLD', 'SELL')),
    long_term_recommendation VARCHAR(4) NOT NULL CHECK (long_term_recommendation IN ('BUY', 'HOLD', 'SELL')),
    risk_level VARCHAR(6) NOT NULL CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH')),
    confidence_score INTEGER NOT NULL CHECK (confidence_score BETWEEN 0 AND 100),
    reasoning TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(ticker, analysis_date)
);

-- Users (managed by Cognito, mirrored for FK references)
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Encrypted portfolios
CREATE TABLE portfolios (
    user_id UUID PRIMARY KEY REFERENCES users(id),
    encrypted_data TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- User preferences
CREATE TABLE user_preferences (
    user_id UUID PRIMARY KEY REFERENCES users(id),
    preferred_sectors VARCHAR(50)[] DEFAULT '{}',
    preferred_sizes VARCHAR(10)[] DEFAULT '{}',
    max_risk_level VARCHAR(6) DEFAULT 'HIGH',
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Indexes for common query patterns
CREATE INDEX idx_stock_data_ticker ON stock_data(ticker);
CREATE INDEX idx_stock_data_trading_date ON stock_data(trading_date);
CREATE INDEX idx_news_summaries_published_at ON news_summaries(published_at);
CREATE INDEX idx_news_summaries_tickers ON news_summaries USING GIN(tickers);
CREATE INDEX idx_analysis_results_ticker ON analysis_results(ticker);
CREATE INDEX idx_analysis_results_date ON analysis_results(analysis_date);
CREATE INDEX idx_stocks_sector ON stocks(sector);
CREATE INDEX idx_stocks_company_size ON stocks(company_size);
CREATE INDEX idx_stocks_is_active ON stocks(is_active);
