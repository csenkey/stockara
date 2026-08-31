# On-Demand Holding Review Design

## Scope

This feature reviews capital already allocated to a real-user holding. It is
not a new-opportunity scanner and does not reuse daily shortlist eligibility.

The first implementation slice includes the internal synchronous engine plus an
authenticated preview API and page. The preview accepts manually entered
holding context and does not persist it; it validates the domain contract and
AI prompts before encrypted portfolio loading, isolated result persistence, and
asynchronous orchestration are added.

## Authentication slice

The frontend sends users to Cognito Managed Login using Authorization Code with
PKCE. Cognito owns email/password registration, email verification, sessions,
and optional Google, Facebook, and Apple federation. API Gateway's Cognito User
Pool authorizer validates the ID token before `POST /api/holding-reviews`
reaches FastAPI. FastAPI reads only the already-verified `sub` claim.

The deployed frontend loads `/auth-config.json`, which contains public runtime
values only: API URL, Cognito domain, app client ID, redirect/logout URLs, and
enabled provider names. OAuth client secrets and the Apple private key remain
in AWS Secrets Manager. Provider integrations are activated through deployment
context or environment variables containing client IDs and secret *names*.

Deployment inputs:

| Provider | Public metadata | Secrets Manager reference |
| --- | --- | --- |
| Google | `GOOGLE_OAUTH_CLIENT_ID` | `GOOGLE_OAUTH_CLIENT_SECRET_NAME` |
| Facebook | `FACEBOOK_OAUTH_CLIENT_ID` | `FACEBOOK_OAUTH_CLIENT_SECRET_NAME` |
| Apple | `APPLE_OAUTH_CLIENT_ID`, `APPLE_OAUTH_TEAM_ID`, `APPLE_OAUTH_KEY_ID` | `APPLE_OAUTH_PRIVATE_KEY_SECRET_NAME` |

Equivalent camel-case CDK context keys are supported. Each external provider is
configured with Cognito's `/oauth2/idpresponse` URL for the deployed managed
login domain; incomplete provider configuration leaves that provider disabled.

Managed Login is intentionally an adapter at the edge. Branding and a custom
auth domain can be added later; a fully custom login UI can retain the same
user pool, app client, PKCE flow, and API authorizer.

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

The Lambda `mode=holding_review` path remains for direct, authorized operator
invocation. The authenticated preview route also does not persist manually
supplied holding context. Production replaces the preview payload with an
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
