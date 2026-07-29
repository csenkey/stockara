# Daily Pipeline Stability Requirements

## Purpose

Make Stockara reliably produce a once-daily, human-usable recommendation artifact by orchestrating data collection, repair, analysis, review, and publication as one observable workflow.

The product should avoid two bad outcomes:

- Looking broken when the daily analysis window has not opened yet.
- Publishing an empty dashboard without explaining exactly which data, provider, model, or review gate prevented actionable picks.

## Requirements

### 1. Daily Orchestration

Stockara must have one daily production workflow that coordinates the Phase 1 run instead of relying on several independent schedules racing each other.

Acceptance criteria:

- The workflow starts once per trading day before the analysis target time.
- The workflow invokes bounded Lambdas for static metadata sync, price collection, gap repair, news collection, calendar/evidence collection, AI analysis, review, and publication.
- The workflow waits, retries, or skips according to typed readiness states rather than fixed clock assumptions.
- The workflow emits a final success, degraded, or blocked result with links to the daily artifacts and data-readiness report.

### 2. Data Readiness Report

Every daily run must publish a machine-readable and UI-readable data readiness report before final publication.

Acceptance criteria:

- The report lists missing or degraded data by ticker, data type, provider, reason, latest observed timestamp, retry state, and suggested repair mode.
- Required classes include `metadata`, `price`, `history`, `news`, `earnings`, `dividends`, `evidence`, `ai_analysis`, and `ai_review`.
- Summary counters must match the detailed ticker/provider rows.
- The report distinguishes "collector has not run", "provider returned no rows", "provider failed", "quota/rate limited", "unsupported symbol", "symbol mapping needed", "inactive/delisted", and "data exists but is stale".

### 3. Repair Modes

Each missing-data class must have an idempotent repair path that can be invoked by the workflow, by GitHub Actions manual workflows, or by an operator runbook.

Acceptance criteria:

- Support repair modes for `sync_static_metadata`, `repair_price_gaps`, `repair_history`, `repair_news`, `repair_calendars`, `repair_evidence`, `retry_ai_analysis`, and `retry_ai_review`.
- Repair modes accept a target date and optional ticker list.
- Repair modes update manifest/readiness state without duplicating records or clobbering fresher data.
- Repair modes expose provider attempts, fallback provider used, retry-after time, and terminal failure reason.

### 4. Degraded Publication Tiers

The daily artifact must publish useful human decision-support output when safe, even when optional data is missing.

Acceptance criteria:

- Recommendations are tagged with one of `decision_grade`, `reduced_confidence`, `fallback_preview`, or `blocked`.
- `decision_grade` requires fresh price/history, resolved metadata, AI analysis, and passing review.
- `reduced_confidence` allows missing optional news/calendar/evidence when the price/history and identity data are fresh; confidence is reduced and missing evidence is visible.
- `fallback_preview` allows heuristic or AI-review-unavailable results only when clearly labeled, confidence-capped, and excluded from automated/demo trading decisions until explicitly allowed.
- `blocked` suppresses tickers with unresolved identity metadata, stale/missing current price data, or insufficient history for the analysis window.
- The frontend should render degraded/fallback recommendations separately enough that users can evaluate them without mistaking them for fully reviewed picks.

### 5. Production Drift Detection

The workflow must detect when production data disagrees with repository-backed expectations.

Acceptance criteria:

- The daily report flags when active production stock metadata contains unresolved rows even though `data/watchlist_seed.csv` and the local metadata audit are clean.
- Static metadata sync runs as part of the workflow or is explicitly skipped with a recorded reason.
- Drift warnings identify whether rows are missing, inactive/out-of-scope, missing required fields, or stale compared with the seed hash.

### 6. Cost And Quota Control

The workflow must remain cost-conscious for a low-volume Phase 1 product.

Acceptance criteria:

- NewsAPI usage stays below the free development/test quota during development.
- Provider calls have daily budgets and circuit breakers where free-tier limits apply.
- Workflow cost is tracked as Step Functions state transitions plus existing Lambda/EventBridge/S3/DynamoDB usage.
- The expected Step Functions cost remains near-free for one daily Standard Workflow unless the workflow starts high-cardinality per-ticker state transitions.

