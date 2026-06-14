# Stockara Business Requirements

This document captures the current business vision for the Stockara web page,
domain model, and data storage needs. It is intended as a discussion draft before
schema or implementation changes are made.

## Business Goal

Stockara should help visitors and registered customers understand daily stock
opportunities, manage a portfolio, and receive clear AI-backed suggestions while
keeping sensitive customer data encrypted in storage.

## Domain Models

### Ticker

Each ticker should be stored separately in the database with all relevant
business data needed for analysis and display.

Required ticker data includes:

- Basic stock identity: ticker symbol, company name, sector, and company size.
- Business profile: brief company history, leading products, and relevant
  business description.
- Financial and business statistics: earnings, valuation, revenue, margin,
  growth, or other business metrics used by the recommendation algorithm.
- Reduced stock price history suitable for long-term storage and analysis.
- Dividend history, including dividend date, dividend value, and observed or
  calculated effect on stock price.
- Earnings-call summaries, including call date, summary, key topics, and observed
  or calculated effect on stock price.

### Sector

Each sector should be represented as its own analytical concept, not only as a
label on a ticker.

Required sector data includes:

- Sector identity and metadata.
- Sector trend history.
- Trend data stored in a form that allows correlation calculations between a
  sector and the tickers belonging to that sector.
- Enough historical trend granularity to compare sector movement, ticker
  movement, and recommendation outcomes over time.

### Customer

Customer data should support portfolio management, suggestion history, and
secure storage.

Required customer data includes:

- Customer portfolio.
- Suggestions given to the customer, separated by day.
- Suggestion history that can be reviewed later.
- Encrypted customer data in the database so direct database queries do not
  expose human-readable portfolio data.
- Decryption only inside application logic for calculations and screen display.

### Superhero Demo Account

Superhero demo accounts should be stored using the same customer-account model
as real customers wherever practical.

Required demo data includes:

- Portfolio state.
- Suggestions or recommendations applied by day.
- Transaction history.
- Public performance data.
- The same storage semantics as real customers unless public leaderboard needs
  require intentionally non-sensitive denormalized data.

## Web Page Requirements

### Opening Page

The first page should show a prominent investment disclaimer.

Requirements:

- The user must acknowledge the disclaimer before continuing.
- The system should remember acknowledgement status where appropriate.
- The disclaimer should make clear that Stockara provides analysis and simulated
  or informational suggestions, not financial advice.

### Daily Top Pick

The opening page should show Stockara's daily top pick.

Requirements:

- Show the selected ticker.
- Show reasoning for the pick.
- Prefer using the latest available analysis date if current-day analysis is not
  available.
- The top pick should be available before login so visitors can understand the
  value of the product.

### Customer Access

Users should be able to:

- Register.
- Log in.
- Enter or upload their portfolio.
- View their portfolio.
- View personalized suggestions.

## Current Data Model Fit

The current implementation uses a DynamoDB single-table model with these entity
families:

- `STOCK#{ticker}/META`
- `STOCKDATA#{ticker}/DATE#{trading_date}`
- `NEWS#{title_source_hash}/META`
- `ANALYSIS#{ticker}/DATE#{analysis_date}`
- `USER#{user_id}/PROFILE`
- `USER#{user_id}/PORTFOLIO`
- `USER#{user_id}/PREFERENCES`
- `DEMO_ACCOUNT#{account_id}/META`
- `DEMO_HOLDING#{account_id}/TICKER#{ticker}`
- `DEMO_TXN#{account_id}/TS#{timestamp}#{id}`
- `DEMO_SNAPSHOT#{account_id}/DATE#{snapshot_date}`

### What Already Fits

- Tickers exist as separate stock metadata records.
- Daily OHLCV stock price history exists per ticker and date.
- AI analysis is stored per ticker and analysis date.
- News summaries are stored and linked to tickers.
- Customer portfolios are stored as a single encrypted string.
- Customer preferences are stored separately.
- Demo accounts have holdings, transactions, and daily snapshots.
- Public demo APIs are separated from authenticated portfolio APIs.

### Gaps Against These Business Requirements

- Ticker business profile data is not currently modeled. There are no fields for
  company history, leading products, detailed business description, or business
  statistics.
- Dividend history is not currently modeled.
- Dividend impact on stock price is not currently modeled.
- Earnings-call summaries are not currently modeled.
- Earnings-call impact on stock price is not currently modeled.
- Sector exists only as a string field on each stock, not as its own entity.
- Sector trend history is not currently modeled.
- The current sector representation does not directly support efficient
  sector-to-ticker correlation calculations.
- Customer suggestion history is not currently stored by customer and day.
  Suggestions appear to be generated from the latest analysis and current
  portfolio at request time.
- Demo accounts are not stored the same way as real customers. They currently
  use explicit public demo account, holding, transaction, and snapshot entities,
  while real customer portfolios are encrypted as one portfolio blob.
- The opening-page disclaimer acknowledgement is not currently modeled.
- A public daily top-pick entity or endpoint is not currently modeled.

## Initial Assessment

The current database model supports the original core requirements well:
watchlist stocks, OHLCV history, news, AI recommendations, encrypted customer
portfolios, and public demo trading.

It does not yet fully support the richer business-research model described here.
The largest model additions would be:

- richer ticker fundamentals/profile entities,
- dividend event entities,
- earnings-call event entities,
- sector entities and sector trend records,
- customer suggestion snapshot/history records,
- disclaimer acknowledgement records,
- a daily top-pick record or query pattern,
- and a decision on whether demo accounts should truly share the encrypted
  customer portfolio model or remain public denormalized demo records.

## Discussion Questions

1. Should real customer suggestion history be encrypted together with portfolio
   data, or can historical suggestions be stored as readable analytical records
   keyed by user ID?
2. Should superhero demo accounts remain public, query-optimized records, or
   should they use the same encrypted portfolio blob as real customers and expose
   derived public snapshots?
3. What does "stock price effect" mean for dividends and earnings calls:
   same-day price movement, next-trading-day movement, 7-day movement, or a
   calculated abnormal return against sector/index movement?
4. Should sector trends come from market ETFs, aggregated ticker movement,
   external trend data, or all of these?
5. Should the daily top pick be a manually reviewable published record, or
   automatically selected from AI analysis by confidence/risk/recommendation?

## Confirmed Decisions

- Rich ticker profiles should be created and stored separately from core stock
  metadata.
- Dividend history should be stored, including a dividend price-impact model.
- Earnings-call summaries should be stored, including an earnings-call
  price-impact model.
- Sectors should become first-class analytical entities with their own trend and
  ticker-correlation records.
- Customer suggestion history should be persisted with inexpensive
  user-and-date lookups. It does not have to be stored inside the encrypted
  portfolio blob, but the customer-specific suggestion payload should still be
  encrypted because SELL suggestions can imply current holdings.
- The disclaimer is a UI-only startup acknowledgement. It can be saved in a
  browser cookie and does not need database persistence.
- The daily top pick should have a public endpoint. Its content can be static
  generated content that is regenerated once per day.

## Proposed DynamoDB Entity Shapes

These entities extend the existing single-table design:

- `STOCK#{ticker}/PROFILE`: rich company history, business description, leading
  products, and business stats.
- `DIVIDEND#{ticker}/DATE#{ex_dividend_date}`: dividend value, payment metadata,
  and observed price impact.
- `EARNINGS_CALL#{ticker}/DATE#{call_date}`: earnings-call summary, topics,
  sentiment, and observed price impact.
- `SECTOR#{sector}/META`: sector metadata and optional benchmark symbol.
- `SECTOR#{sector}/TREND#DATE#{trend_date}`: sector trend observations over
  time.
- `SECTOR#{sector}/CORRELATION#TICKER#{ticker}#DATE#{calculation_date}#WINDOW#{window_days}`:
  calculated sector-to-ticker correlation records.
- `USER#{user_id}/SUGGESTIONS#DATE#{suggestion_date}`: encrypted generated
  customer suggestion snapshot for that day, with
  `GSI1PK=SUGGESTIONS#{suggestion_date}` for date-based operational lookups.
- `TOP_PICK/DATE#{pick_date}`: public static top-pick content regenerated daily,
  with `GSI1PK=TOP_PICK` for latest-pick lookup.
