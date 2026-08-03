# Daily Pipeline Stability Design

## Current AWS Shape

Use AWS Step Functions Standard Workflows as the daily organizer and source of
daily operational truth.

The production state machine is `stockara-daily-pipeline`. It coordinates
existing Lambdas instead of replacing them:

1. `SyncStaticMetadata`
2. `CreateOrRefreshManifest`
3. `DispatchManifestTasks`
4. `WaitForManifestDispatch`
5. `CollectPrices`
6. `RepairPriceGaps`
7. `CollectNews`
8. `CollectCalendarsAndEvidence`
9. `BuildReadinessReport`
10. `DecidePublicationTier`
11. `AnalyzeShortlist`
12. `ReviewActionableCalls`
13. `PublishArtifacts`
14. `PublishWorkflowStatus`

The post-1.0 review-recovery extension adds a bounded loop inside the analysis
portion of this flow:

```text
Analyze shortlist -> Review actionable calls -> Validate review contract
  -> approved: publish
  -> valid rejection: withhold and record evidence gaps
  -> repairable gap: targeted collectors -> merge evidence
       -> rerun analysis/review once -> publish or withhold
  -> feature missing: developer incident
  -> provider failure after retries: data incident
  -> invalid review after one retry: AI incident
```

The loop is intentionally bounded to one repair cycle and a small candidate
cap. It is evidence recovery, not an approval loop.

Keep per-ticker fanout inside bounded worker Lambdas and manifest tasks. Do not model 900 tickers as individual Step Functions states in the first version; that would add noise, cost, and complexity without improving reliability.

## Operational Source Of Truth

Daily production status should be read from the Step Functions execution and the
published workflow artifacts:

- State machine: `stockara-daily-pipeline`.
- Automatic schedule: one EventBridge trigger before the daily analysis window.
- Manual runbook: `.github/workflows/run-daily-workflow-now.yml`.
- Latest status artifact: `workflow/latest.json`.
- Dated status artifact: `workflow/history/{date}.json`.

The public dashboard reads `workflow/latest.json` as its primary daily-run
summary, keeps the latest completed publication visible while a newer review is
in progress, and leaves detailed warnings collapsed behind the data-health
surface. Reduced-confidence and fallback-preview rows remain available for
human research even when no decision-grade recommendation is published.

The deploy smoke test validates `workflow/latest.json` when it exists. A missing
workflow artifact is acceptable only before the first workflow publication in a
new environment; after that, stale or malformed workflow status should be
treated as the first operational clue before checking individual Lambdas.

The old high-frequency EventBridge rules are rollback paths, not the normal
production path:

- `Phase1PublishSchedule`: disabled.
- `CollectionDistributorSchedule`: disabled.
- `StockCollectionSchedule`: disabled.

The remaining independent schedules are intentional supporting jobs:

- News collection runs three times per day as quota-conscious prefetching.
- Calendar and evidence collectors run once daily to keep optional evidence warm.
- Stock gap scan runs at 23:15 UTC as after-market maintenance; same-day
  publication readiness is owned by the workflow's manifest dispatch and
  `repair_price_gaps` steps.

## Why Step Functions

EventBridge schedules are good at starting work, but poor at explaining a whole daily business process. Step Functions gives Stockara:

- Ordered execution.
- Built-in retries and catches.
- Wait states for provider backoff or manifest completion.
- A visible execution graph for daily failures.
- One final status for "completed", "completed degraded", or "blocked".

## Cost Expectation

AWS Step Functions Standard pricing is based on state transitions, with a monthly free tier. A once-daily Stockara workflow should be cheap if it uses coarse workflow states and lets worker Lambdas process chunks.

Planning estimate:

- 30 daily executions per month.
- 40 to 100 state transitions per execution.
- About 1,200 to 3,000 monthly transitions.
- This should usually fit inside the free tier or cost cents per month.

Avoid a first design where the state machine loops over every ticker or every article. If per-ticker orchestration becomes necessary later, evaluate Distributed Map, SQS, or Express Workflows separately.

The repair loop must preserve this cost boundary. Targeted collection is only
allowed for shortlisted candidates, provider budgets are explicit, and the
second AI review is limited to one attempt per candidate per daily run.

## Review And Evidence Contract

The review payload is a persisted contract, not an informal JSON suggestion.
At minimum it contains:

- `approved`
- `rationale`
- `concerns`
- `rejection_category`
- `what_would_make_approvable`
- `evidence_gaps[]`
- `model`, `attempt`, and response validation metadata

The evidence-gap registry maps each gap to a capability and an owner:

| Gap class | Current disposition | Next action |
|---|---|---|
| Company-specific catalyst | Collector exists | Targeted news/IR retry |
| Analyst action recency | Partial collector | Targeted Finnhub retry |
| Technical confirmation | Data exists, context incomplete | Enrich analyzer/reviewer context |
| SEC filing substance | Feature missing | Add bounded SEC filing-text extraction |
| Fundamental/valuation context | Partial | Add durable source-backed fundamentals |
| Provider/auth/quota failure | Operational | Retry with budget, then incident |

Keyword parsing may remain a compatibility fallback for old artifacts, but new
reviews must emit typed gaps directly.

## Publication Tiers

The publication tier is computed per recommendation and summarized at artifact level.

- `decision_grade`: Fully reviewed output. Suitable for public top picks and future demo trading.
- `reduced_confidence`: Useful suggestion with visible evidence gaps. Suitable for user research, not automated trading.
- `fallback_preview`: Heuristic or unreviewed fallback. Useful for debugging and human triage, not default ranked picks unless explicitly enabled.
- `blocked`: Do not show as a suggestion; show only in data-health/readiness diagnostics.

The manifest's aggregate `price_freshness` target is an operational coverage
signal, not a global publication safety gate. When it is missed, the workflow
continues as degraded and publishes opportunities among eligible tickers. The
analyzer's per-ticker freshness and history checks remain authoritative: every
stale, missing-price, or under-supported ticker is excluded before scoring, and
the artifact exposes that the ranking covers only the eligible subset.

## Readiness Report Shape

Suggested artifact paths:

- `data-readiness/latest.json`
- `data-readiness/history/{date}.json`
- `workflow/latest.json`
- `workflow/history/{date}.json`

Each readiness item should include:

- `ticker`
- `data_type`
- `status`
- `required_for`
- `provider`
- `provider_symbol`
- `reason`
- `latest_observed_at`
- `last_attempted_at`
- `next_retry_at`
- `repair_mode`
- `terminal`
- `details`

Metadata drift detection compares active production stock metadata with the
repository seed expectations. Because the analyzer Lambda is packaged from
`backend/` while the canonical seed lives at repository root under
`data/watchlist_seed.csv`, keep `backend/src/data/watchlist_seed.csv` as a
packaged runtime snapshot. A contract test must verify the packaged snapshot
matches the canonical seed. Drift readiness rows should classify production
metadata as `active_not_in_seed`, `missing_required_metadata`, or
`metadata_seed_mismatch`, and point to `sync_static_metadata` as the repair
mode.

## Migration Status

The workflow has moved past shadow/manual mode and owns daily publication. The
retired schedules are intentionally kept in CDK as disabled rollback paths:

- Disabled: 5-minute `Phase1PublishSchedule`.
- Disabled: 5-minute `CollectionDistributorSchedule`.
- Disabled: frequent `StockCollectionSchedule`.
- Retained: low-frequency news prefetching.
- Retained: after-market stock gap scan maintenance.

## Manifest Task State

S3 manifest documents are operational snapshots, not a concurrent mutation
store. The immutable task definition and latest aggregate summary remain
published at `collection_manifest/{date}.json`, while each mutable task state is
stored in the existing DynamoDB single table:

- `PK = COLLECTION_MANIFEST#{date}`
- `SK = TASK#{task_id}`

Workers update only their own task row. Lease acquisition and lifecycle changes
use conditional or atomic DynamoDB updates so concurrent worker completion
cannot erase another task's state. The distributor reads the task rows,
recomputes the manifest summary, and republishes the S3 snapshot.

Existing S3-only manifests remain readable. When the distributor encounters one
without DynamoDB task rows, it seeds the rows from the S3 task definitions
without replacing newer rows.

The workflow must also have a dispatch deadline shorter than the state machine
timeout. When active tasks remain after that deadline, it proceeds to a terminal
classification and publishes workflow status instead of waiting until Step
Functions terminates the execution without an artifact.
