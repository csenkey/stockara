# Daily Pipeline Stability Backlog

## Milestone 1: Explain Why There Are No Picks

- [x] Publish a daily data-readiness artifact with ticker-level details for metadata, price, history, news, AI analysis, and AI review.
- [x] Add readiness summary counters to `top-picks/latest.json` so the dashboard can explain empty results without requiring CloudWatch access.
- [x] Add a production metadata drift check that compares active DynamoDB metadata quality with `data/watchlist_seed.csv` expectations and reports rows missing required fields.
- [x] Add a manual metadata sync verification path that reports changed, unchanged, missing, invalid, and inactive/out-of-scope production rows.
  - [x] Add a sync-only GitHub Actions workflow that validates the deployed repair summary for changed, unchanged, missing, invalid, inactive, and out-of-scope rows.
  - [x] Extend the repair summary or a companion audit to report inactive/out-of-scope production rows.
- [x] Add frontend data-health rendering for readiness rows grouped by reason and repair mode.
- [x] Add tests proving readiness summary counters match detailed rows.

## Milestone 2: Repair Missing Data

- [x] Define a shared repair-mode input schema with `mode`, `run_date`, `tickers`, `max_tickers`, `provider_budget`, and `dry_run`.
- [x] Implement `sync_static_metadata` as an operator-safe repair mode with no live collection field clobbering.
- [x] Implement `repair_price_gaps` and `repair_history` modes using existing price gap scanner and date-bounded manifest tasks.
  - [x] Add shared `repair_price_gaps` and `repair_history` mode aliases to the stock collector with dry-run support.
  - [x] Wire gap scanning output directly into bounded `repair_price_gaps` invocations.
- [x] Implement `repair_news` mode that respects NewsAPI/Finnhub/Alpha Vantage quota budgets and can target only shortlisted or missing-news tickers.
- [x] Implement `repair_calendars` mode for earnings/dividend retry and fallback providers.
  - [x] Add shared `repair_calendars` mode aliases to earnings and dividend collectors with dry-run support.
  - [x] Update calendar manual backfill and Phase 1 workflows to use repair payloads with provider budget inputs.
- [x] Implement `repair_evidence` mode for SEC filing and analyst-action gaps.
  - [x] Add shared `repair_evidence` mode alias to the evidence collector with `sec`, `finnhub`, and `yfinance` provider budgets.
  - [x] Update `Run Evidence Collection Now` to use `repair_evidence` with provider budget and dry-run inputs.
- [x] Implement `retry_ai_analysis` and `retry_ai_review` modes that reuse stored candidate scores/evidence and publish clear model/error provenance.
  - [x] Add analyzer retry modes for fallback/missing AI analyses and missing/error AI reviews.
  - [x] Add a manual `Run AI Retry Now` workflow with dry-run, ticker subset, run date, and max target controls.
- [x] Update GitHub Actions manual workflows to call repair modes instead of bespoke one-off Lambda payloads where practical.
  - [x] Update `Sync Watchlist Metadata Now` to use the shared repair-mode payload shape.
  - [x] Update `Run Phase 1 Pipeline Now` metadata sync and historical repair payloads to use shared repair-mode names.
  - [x] Update `Run News Collection Now` to use `repair_news` with provider budget and dry-run inputs.
  - [x] Update `Run Calendar Backfill Now` and Phase 1 calendar collector calls to use `repair_calendars`.
  - [x] Update `Run Evidence Collection Now` to use `repair_evidence`.
  - [x] Add `Run AI Retry Now` for `retry_ai_analysis` and `retry_ai_review`.

## Milestone 3: Publish Useful Degraded Suggestions

- [x] Add recommendation `publication_tier` values: `decision_grade`, `reduced_confidence`, `fallback_preview`, and `blocked`.
- [x] Keep `blocked` strict for unresolved metadata, stale/missing price data, and insufficient analysis history.
- [x] Allow `reduced_confidence` suggestions when optional news/calendar/evidence is missing but fresh price/history and metadata are present.
- [x] Allow `fallback_preview` output for heuristic or review-unavailable BUY/SELL candidates with confidence caps, visible warnings, and exclusion from any automated trading consumers.
- [x] Add `missing_evidence` and `confidence_adjustments` arrays to recommendation rows.
- [x] Update ranking so decision-grade picks rank first, reduced-confidence suggestions rank separately, and fallback previews do not silently replace reviewed picks.
- [x] Add frontend sections for reviewed picks, lower-confidence suggestions, fallback previews, blocked data issues, and withheld review rejections.
- [x] Add tests for publication tier assignment, confidence downgrades, ranking, and artifact compatibility.

## Milestone 4: Step Functions Orchestrator

- [x] Add a CDK Step Functions state machine for the daily Stockara workflow.
- [x] Start the state machine from one EventBridge daily schedule before the target analysis time.
- [x] Invoke existing Lambdas through coarse workflow states instead of per-ticker Step Functions states.
- [x] Add retries/catches for provider throttling, transient Lambda failures, manifest incompleteness, OpenAI failures, and artifact publish failures.
- [x] Add a readiness decision state that chooses `publish`, `publish_degraded`, `wait_or_repair`, or `blocked`.
- [x] Publish workflow execution status artifacts under `workflow/latest.json` and `workflow/history/{date}.json`.
- [x] Add CloudWatch metrics/alarms for workflow failed, workflow degraded, workflow blocked, and workflow missing for the day.
- [x] Add CDK tests for state machine IAM, schedule, Lambda invocations, retry/catch policies, and outputs.
- [x] Run the orchestrator in manual or shadow mode before disabling existing independent schedules.

## Milestone 5: Retire Superseded Scheduling

- [x] Disable the 5-minute analyzer publisher schedule after the Step Functions workflow is responsible for daily publication.
- [x] Disable or narrow the 5-minute collection distributor schedule after the workflow can create, dispatch, wait, and repair the daily manifest.
- [x] Disable or narrow the frequent bounded stock collector schedule after the workflow owns final price readiness and gap repair.
- [x] Keep the 3-times-per-day global news schedule only if it provides useful prefetching within free quota; otherwise fold news collection fully into the daily workflow.
- [x] Decide whether stock gap scan remains a separate after-market maintenance job or becomes a workflow step.
  - Decision: keep the 23:15 UTC stock gap scan as separate after-market maintenance; the daily workflow owns same-day price readiness through manifest dispatch and `repair_price_gaps`.
- [x] Update runbooks, architecture docs, and smoke tests to treat the Step Functions execution as the source of daily operational truth.

## Milestone 6: Production Dispatch Recovery

The first scheduled production execution after schedule retirement timed out on
2026-07-31 after spending three hours in the manifest dispatch loop. Mutable
task state was stored by concurrent workers through whole-document S3 writes,
which allowed one worker to overwrite another worker's lease or completion.

- [x] Store mutable manifest task state as atomic per-task DynamoDB rows while keeping S3 manifests as public operational snapshots.
- [x] Make manifest creation and task lifecycle updates idempotent and compatible with existing S3 manifests.
- [x] Bound manifest dispatch by an explicit deadline and convert exhausted dispatch into a typed blocked/degraded workflow result.
- [x] Publish `workflow/latest.json` and dated workflow status for every terminal workflow path, including dispatch exhaustion and caught failures.
- [x] Add tests for concurrent task completion, expired leases, a 900-ticker manifest, and workflow terminal paths.
- [x] Prevent workflow retries from inheriting an expired dispatch window from an earlier same-day manifest.
- [x] Keep optional news/calendar collection in its dedicated workflow states instead of duplicating it across 198 manifest tasks.
- [x] Cap manifest worker concurrency and separate manifest creation from worker dispatch so terminal status publication cannot be starved.
- [ ] Deploy and manually verify a complete production workflow plus current public artifacts.
- [ ] Refine the dashboard so stale publication state is concise, current workflow freshness is prominent, and degraded suggestions remain usable.
