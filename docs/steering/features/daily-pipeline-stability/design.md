# Daily Pipeline Stability Design

## Proposed AWS Shape

Use AWS Step Functions Standard Workflows as the daily organizer.

The state machine should coordinate existing Lambdas instead of replacing them:

1. `SyncStaticMetadata`
2. `CreateOrRefreshManifest`
3. `CollectPrices`
4. `RepairPriceGaps`
5. `CollectNews`
6. `CollectCalendarsAndEvidence`
7. `BuildReadinessReport`
8. `DecidePublicationTier`
9. `AnalyzeShortlist`
10. `ReviewActionableCalls`
11. `PublishArtifacts`
12. `PublishWorkflowSummary`

Keep per-ticker fanout inside bounded worker Lambdas and manifest tasks. Do not model 900 tickers as individual Step Functions states in the first version; that would add noise, cost, and complexity without improving reliability.

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

## Publication Tiers

The publication tier is computed per recommendation and summarized at artifact level.

- `decision_grade`: Fully reviewed output. Suitable for public top picks and future demo trading.
- `reduced_confidence`: Useful suggestion with visible evidence gaps. Suitable for user research, not automated trading.
- `fallback_preview`: Heuristic or unreviewed fallback. Useful for debugging and human triage, not default ranked picks unless explicitly enabled.
- `blocked`: Do not show as a suggestion; show only in data-health/readiness diagnostics.

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

## Migration Path

First version should keep existing EventBridge schedules mostly intact while adding the workflow in shadow/manual mode. After the workflow proves stable:

- Disable the 5-minute `Phase1PublishSchedule`.
- Disable or narrow the 5-minute `CollectionDistributorSchedule`.
- Keep low-frequency news collection only if it refreshes optional articles outside the daily workflow.
- Keep gap scanning as either a workflow step or a separate after-market maintenance job, but ensure it cannot affect publication gates for the same day unless explicitly counted.

