# On-Demand Holding Review Backlog

## 1. Feature contract and safe internal foundation

- [x] OHR-1-OHR-10: Define requirements, architecture, security boundary, and phased production path.
- [x] OHR-2/OHR-3: Add a score-independent evidence builder and AI holding-review engine for a single ticker.
- [x] OHR-4/OHR-5: Calculate initial holding, return, position-weight, and dividend metrics and return separate security and portfolio decisions.
- [x] OHR-6: Add a stronger review gate for proposed `REDUCE` and `EXIT` actions.
- [x] OHR-2/OHR-6: Add an internal `mode=holding_review` Lambda invocation with explicit blocked/failed/degraded/completed results and no heuristic fallback.
- [x] OHR-2-OHR-6: Add unit tests proving score-independent invocation, required-evidence blocking, dividend metrics, and the action review gate.

## 2. Restore secure real-user portfolio foundation

- [x] OHR-1/OHR-8/OHR-11: Add Cognito Managed Login, Authorization Code with PKCE, API Gateway JWT validation, email/password self-registration, and optional Google/Facebook/Apple federation configuration.
- [x] OHR-1/OHR-10/OHR-11: Add top-right login/register controls and an authenticated-only holding-analysis page backed by a synchronous preview API.
- [ ] OHR-11: Supply production Google, Facebook, and Apple application credentials in Secrets Manager/deployment configuration and smoke-test each provider callback.
- [ ] OHR-11: Customize Managed Login branding and optionally add a custom auth domain.
- [ ] OHR-8: Restore KMS data-key plus AES-256-GCM portfolio encryption and tests.
- [ ] OHR-1/OHR-8: Restore portfolio repository/API operations with ownership, ticker, quantity, and buying-price validation.
- [ ] OHR-4: Extend holdings with optional lot dates and dividend-received data without weakening single-string encrypted storage.
- [ ] OHR-5: Add encrypted user investment objective and horizon preferences for `income`, `balanced`, and `growth` review modes.

## 3. Private asynchronous request architecture

- [ ] OHR-8/OHR-9: Add isolated on-demand request metadata and encrypted result persistence with TTL and conditional idempotency.
- [ ] OHR-1/OHR-8: Add authenticated `POST /api/portfolio/holdings/{ticker}/review` and owner-only `GET /api/holding-reviews/{request_id}` endpoints.
- [x] OHR-1/OHR-2/OHR-11: Add the temporary authenticated synchronous `POST /api/holding-reviews` preview for manually entered holding context; remove or narrow it after portfolio-backed requests ship.
- [ ] OHR-8: Add `stockara-on-demand-holding-review` Step Functions workflow that carries only opaque IDs and compact statuses.
- [ ] OHR-8: Load and decrypt the portfolio only inside the review worker; add log-redaction and workflow-payload contract tests.
- [ ] OHR-9: Add per-user/global quotas, concurrency bounds, idempotency keys, exact-evidence cache identity, and AI cost metrics.
- [ ] OHR-8: Prove on-demand runs cannot update daily analysis health, daily candidate records, public artifacts, or demo trading inputs.

## 4. Fresh evidence and opportunity-cost comparison

- [ ] OHR-3: Add `stored` and bounded `refresh` evidence modes for one ticker.
- [ ] OHR-3: Reuse targeted price/news/evidence collection without publishing global daily-workflow status or passing large payloads through Step Functions.
- [ ] OHR-7: Add cash/risk-free and broad-market ETF comparison snapshots.
- [ ] OHR-7: Add sector ETF and current decision-grade opportunity comparisons.
- [ ] OHR-7: Model transaction-cost, tax-availability, risk, uncertainty, and configurable minimum replacement-improvement buffers.
- [ ] OHR-5/OHR-7: Add portfolio concentration, sector exposure, cash level, and alternative-position context to the action decision.
- [ ] OHR-7: Backtest `KEEP`/`REDUCE`/`EXIT` thresholds and turnover safeguards before treating replacement actions as decision-grade.

## 5. Product experience and operations

- [ ] OHR-10: Replace the authenticated preview form/raw response with a portfolio-backed holding detail UI, structured result presentation, generated-at versus evidence-as-of timestamps, and progress polling.
- [ ] OHR-5/OHR-10: Show security recommendation separately from approved portfolio action, including `KEEP_INCOME` dividend context.
- [ ] OHR-6/OHR-10: Show proposed versus approved action and reviewer rationale when `REDUCE` or `EXIT` is withheld.
- [ ] OHR-10: Show missing evidence, tax limitations, comparison basis, invalidation criteria, and next review trigger.
- [ ] OHR-9: Add dashboards/alarms for request latency, blocked/failed reviews, invalid AI responses, action-review rejection, cache hits, and model cost.
- [ ] OHR-1-OHR-10: Run backend, infrastructure, frontend, deployed API, privacy, and smoke-test verification before enabling production users.
