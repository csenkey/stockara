# On-Demand Holding Review Requirements

## Goal

Help an authenticated portfolio owner decide whether capital already invested in
a held ticker should remain allocated there. The feature distinguishes the
security-level `BUY`/`HOLD`/`SELL` assessment from the portfolio-level
`KEEP`/`KEEP_INCOME`/`REDUCE`/`EXIT`/`REVIEW` action.

## Requirements

### OHR-1 Held-ticker request

- A user may request a review only for a ticker in their decrypted portfolio.
- The first internal implementation may accept explicit holding context for
  operator testing, but must not expose that path as a public API.
- The ticker, quantity, buying price, portfolio objective, and optional total
  portfolio value are validated before analysis.

### OHR-2 Score-independent AI analysis

- Every valid request with sufficient required evidence invokes the AI security
  analyzer regardless of Stockara's candidate score, universe rank, shortlist,
  or daily publication status.
- The on-demand path must not call `select_shortlist`, use an opportunity-score
  threshold, or write daily candidate-score records.
- A low or absent internal score must never block a holding review.
- AI failure produces an explicit failed result; it must not masquerade as a
  completed heuristic AI review.

### OHR-3 Evidence readiness

- Required evidence consists of valid active-ticker metadata and at least 20
  OHLCV rows spanning at least 30 calendar days, with a latest price no more
  than three calendar days old by default.
- Missing required evidence blocks the request with explicit reasons.
- Missing news, earnings, dividend, or other optional evidence is disclosed but
  does not prevent AI analysis.
- Every result records an evidence timestamp, provenance-oriented evidence
  summary, and deterministic evidence hash.

### OHR-4 Holding economics

- The review calculates current position value, cost basis, unrealized return,
  recent price return, and position weight when portfolio value is available.
- Dividend analysis distinguishes trailing dividend income, forward/current
  yield where supported, and yield on cost.
- Yield on cost is explanatory only and must not be the sole justification for
  keeping a holding.

### OHR-5 Two-layer decision

- Security analysis continues to use only `BUY`, `HOLD`, or `SELL` and
  `LOW`, `MEDIUM`, or `HIGH` risk.
- Portfolio action uses only `KEEP`, `KEEP_INCOME`, `REDUCE`, `EXIT`, or
  `REVIEW`.
- The portfolio action may be `REDUCE` or `EXIT` when the security-level
  recommendation is `HOLD`, provided the result explains the portfolio-level
  opportunity cost or concentration reason.
- `KEEP_INCOME` requires an income-oriented justification and must discuss
  dividend sustainability or missing dividend evidence.

### OHR-6 Action review gate

- `REDUCE` and `EXIT` actions require a stronger AI review before they can be
  presented as approved portfolio actions.
- A review failure or rejection changes the approved action to `REVIEW` and
  preserves the proposed action and reviewer rationale for explanation.
- The feature provides decision support only and never executes a trade.

### OHR-7 Opportunity cost

- A production portfolio action must compare keeping the holding with a
  documented comparison set such as cash, a broad-market ETF, a sector ETF,
  or current decision-grade Stockara opportunities.
- Replacement must account for configured transaction-cost, tax-availability,
  risk, uncertainty, and minimum-improvement buffers.
- Missing tax data is disclosed; Stockara must not invent tax consequences.

### OHR-8 Privacy and isolation

- Real portfolio data remains encrypted as a single stored string and is
  decrypted only in memory.
- Decrypted portfolio contents must not be placed in Step Functions state,
  public S3 artifacts, metrics, or logs.
- Personalized results are private to the owning user and stored encrypted when
  persistence is added.
- On-demand reviews must not overwrite daily `ANALYSIS#{ticker}` records, update
  daily analysis health timestamps, enter public artifacts, or trigger demo
  trading.

### OHR-9 Cost and idempotency

- Authenticated requests are subject to per-user and global concurrency and AI
  budgets.
- Idempotent retries and concurrent requests for an identical request must not
  create duplicate model charges.
- Exact-snapshot caching, if enabled, includes strategy, ticker, evidence hash,
  models, prompt versions, schema versions, and relevant parameters.

### OHR-10 Explainability

- Results expose generated-at and evidence-as-of timestamps separately.
- Results explain security thesis, holding role, capital efficiency,
  opportunity cost, invalidation criteria, missing evidence, and next review
  trigger.
- The UI must clearly distinguish an approved action from a proposed action
  withheld by the stronger review.
