# On-Demand Holding Review Design

## Scope

This feature reviews capital already allocated to a real-user holding. It is
not a new-opportunity scanner and does not reuse daily shortlist eligibility.

The first implementation slice is an internal, synchronous engine invoked with
`mode=holding_review` on the existing analyzer Lambda. It validates the domain
contract and AI prompts without exposing portfolio data publicly. Production
API, encrypted portfolio loading, isolated result persistence, and asynchronous
orchestration follow in later backlog slices.

## Decision layers

```text
Holding request
  -> required-evidence readiness
  -> neutral evidence snapshot (no candidate score)
  -> security AI: BUY/HOLD/SELL
  -> portfolio AI: KEEP/KEEP_INCOME/REDUCE/EXIT/REVIEW
  -> stronger review for REDUCE/EXIT
  -> private result
```

Security quality and portfolio allocation are deliberately separate. A healthy
but capital-inefficient ticker can be `HOLD` plus `REDUCE`; a flat security with
safe, useful income can be `HOLD` plus `KEEP_INCOME`.

## Initial internal invocation

```json
{
  "mode": "holding_review",
  "ticker": "AAPL",
  "quantity": 10,
  "buying_price": "150.00",
  "portfolio_total_value": "25000.00",
  "objective": "balanced"
}
```

This path is for direct, authorized operator invocation only. It does not
persist the supplied holding context. Production must replace it with an
authenticated request containing only user and request identifiers; the worker
then decrypts the portfolio in memory.

## Evidence snapshot

The snapshot contains ticker identity, recent daily OHLCV and returns, stored
market signals, recent news, earnings/dividend events, holding economics,
required and optional gaps, `evidence_as_of`, and a SHA-256 evidence hash.

Candidate opportunity and negative scores are intentionally absent. The AI sees
facts and source records, not the daily preselection rank.

## AI contracts

The analysis model returns a strict schema containing the security assessment,
portfolio action, holding role, capital efficiency, opportunity cost,
reasoning, invalidation criteria, dividend sustainability, and next review
trigger.

`REDUCE` and `EXIT` are sent to the stronger review model. Rejected or invalid
actions become `REVIEW`; the proposed action remains auditable. There is no
heuristic fallback. Model unavailability produces `FAILED`.

## Production target architecture

```text
Authenticated API
  -> create private request row
  -> start stockara-on-demand-holding-review
       -> load/decrypt portfolio in memory
       -> validate requested held ticker
       -> optionally refresh ticker evidence
       -> build immutable evidence snapshot
       -> analyze security and portfolio role
       -> review REDUCE/EXIT
       -> encrypt and persist result
  -> client polls private result endpoint
```

Step Functions carries only opaque identifiers and compact status. Decrypted
holdings and evidence bodies stay inside Lambda memory. The workflow is
separate from `stockara-daily-pipeline` and never publishes public artifacts.

## Persistence target

Use a separate namespace, not the daily analysis key:

```text
PK = ONDEMAND_REQUEST#{request_id}
SK = META

PK = USER#{user_id}
SK = HOLDING_REVIEW#{created_at}#{request_id}
```

The personalized result payload is encrypted. Indexable fields are limited to
opaque request ownership/status metadata needed for authorization and cleanup.

## Opportunity-cost evolution

The initial engine records whether comparison evidence is absent. Production
then adds cash/risk-free and ETF comparisons, current decision-grade Stockara
alternatives, portfolio-wide concentration, and backtested turnover buffers.
Until comparison evidence exists, AI may identify capital-efficiency concerns
but must not claim a quantified replacement advantage.

## Failure semantics

- `BLOCKED`: required identity or OHLCV evidence is unavailable or stale.
- `FAILED`: model invocation or response validation failed after bounded retry.
- `COMPLETED_DEGRADED`: AI completed with optional evidence gaps.
- `COMPLETED`: AI completed with expected evidence coverage.

An action-review rejection is a completed research result with approved action
`REVIEW`, not a workflow failure.
