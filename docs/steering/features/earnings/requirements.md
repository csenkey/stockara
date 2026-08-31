# Earnings Charter Requirements

## Purpose

Create a trustworthy earnings intelligence capability in two ordered stages:

1. Publish a complete, current, source-reconciled calendar for the active
   Stockara watchlist.
2. Research and validate whether upcoming earnings events offer positive
   expected value after risk and trading costs.

An earnings beat prediction and a positive post-report price prediction are
different outputs. Stockara must never present either as certain or imply that
historical event behavior guarantees a profitable trade.

## Requirements

### EARN-1: Calendar completeness and freshness

- The public earnings calendar must read its dedicated calendar artifact, not
  the capped top-picks publication. The dividend tab may retain its existing
  source until dividend artifact aggregation is independently corrected.
- Upcoming events must not be truncated by a global display limit.
- The collection must cover the full active Stockara watchlist. Broader-market
  events may be added later but must be identified as outside the watchlist.
- Partial ticker slices and manifest tasks must not overwrite a full-watchlist
  `latest` artifact.
- Artifacts must expose collection date, generated time, requested range,
  selected ticker count, event count, provider health, zero-event tickers, and
  warnings.

### EARN-2: Provider reconciliation

- Upcoming dates must be collected from at least two independent providers
  when provider budgets permit.
- Events must retain every provider observation and a canonical event record.
- Canonical records must expose `tentative`, `single_source`, `confirmed`, or
  `conflicting` date confidence.
- Near-term conflicts must be eligible for bounded confirmation from company
  investor-relations material or another configured provider.
- Provider disagreement must never be silently resolved by last-write-wins.

### EARN-3: Historical event evidence

- Store at least eight quarters when available for each supported ticker.
- Preserve report date/time, fiscal period, EPS and revenue consensus/actuals,
  surprise values, guidance evidence, estimate revisions, source URLs, and
  collection timestamps when available.
- Backfill runs must count quota/budget skips as incomplete, not successful.
- History coverage must be measurable per ticker and across the active universe.

### EARN-4: Price-reaction event study

- Compute split-adjusted returns for pre-event and post-event windows including
  `[-5,-1]`, `[-1,+1]`, `[0,+1]`, `[+1,+5]`, and `[+1,+20]` trading days.
- Respect before-open and after-close timing when selecting the event session.
- Store raw, broad-market-adjusted, and sector-adjusted returns plus abnormal
  volume when inputs exist.
- Missing timing, benchmark, price, or liquidity data must reduce evidence
  quality rather than produce a fabricated zero.

### EARN-5: Predictive research contract

- Model result surprise separately from subsequent abnormal price direction and
  magnitude.
- Inputs may include consensus dispersion/revisions, prior surprise history,
  guidance, source-backed news, technical context, valuation, liquidity, and
  implied move when licensed options data is available.
- Evaluation must be walk-forward by time with no future report, revision,
  price, transcript, or news leakage.
- Report calibration, precision/recall, Brier score, expected return, drawdown,
  turnover, and results after spread, slippage, commission, and borrow/option
  costs where applicable.
- Promotion requires a versioned `AnalysisStrategy`, reproducible artifacts,
  sufficient sample size, and performance across multiple market regimes.

### EARN-6: Product surface and safety

- Provide an “Earnings opportunities — next 7 days” research view with event
  confidence, history, expected versus implied move when available, catalysts,
  risks, and `TRADE`, `WATCH`, or `AVOID` research classification.
- Low-confidence or conflicting dates default to `WATCH` or `AVOID`.
- Predictions run in shadow mode before they can influence published top picks,
  holding reviews, demo trading, or future automated consumers.
- Public copy must explain that an EPS beat can still produce a negative price
  reaction because guidance, valuation, and positioning matter.

### EARN-7: Operations and deployment

- Provider calls must use explicit budgets, circuit breakers, retry/backoff, and
  safe diagnostics.
- CloudWatch must expose calendar freshness, watchlist coverage, conflicts,
  history coverage, quota skips, and shadow-prediction scoring.
- Every release must pass backend, frontend, and infrastructure tests, deploy
  through `main`, and verify dedicated production artifacts and representative
  near-term tickers.
