# Daily Pipeline Stability Backlog

## Milestone 1: Explain Why There Are No Picks

- [x] Publish a daily data-readiness artifact with ticker-level details for metadata, price, history, news, AI analysis, and AI review.
- [x] Add readiness summary counters to `top-picks/latest.json` so the dashboard can explain empty results without requiring CloudWatch access.
- [x] Add a production metadata drift check that compares active DynamoDB metadata quality with `data/watchlist_seed.csv` expectations and reports rows missing required fields.
- [ ] Add a manual metadata sync verification path that reports changed, unchanged, missing, invalid, and inactive/out-of-scope production rows.
- [x] Add frontend data-health rendering for readiness rows grouped by reason and repair mode.
- [x] Add tests proving readiness summary counters match detailed rows.

## Milestone 2: Repair Missing Data

- [ ] Define a shared repair-mode input schema with `mode`, `run_date`, `tickers`, `max_tickers`, `provider_budget`, and `dry_run`.
- [ ] Implement `sync_static_metadata` as an operator-safe repair mode with no live collection field clobbering.
- [ ] Implement `repair_price_gaps` and `repair_history` modes using existing price gap scanner and date-bounded manifest tasks.
- [ ] Implement `repair_news` mode that respects NewsAPI/Finnhub/Alpha Vantage quota budgets and can target only shortlisted or missing-news tickers.
- [ ] Implement `repair_calendars` mode for earnings/dividend retry and fallback providers.
- [ ] Implement `repair_evidence` mode for SEC filing and analyst-action gaps.
- [ ] Implement `retry_ai_analysis` and `retry_ai_review` modes that reuse stored candidate scores/evidence and publish clear model/error provenance.
- [ ] Update GitHub Actions manual workflows to call repair modes instead of bespoke one-off Lambda payloads where practical.

## Milestone 3: Publish Useful Degraded Suggestions

- [ ] Add recommendation `publication_tier` values: `decision_grade`, `reduced_confidence`, `fallback_preview`, and `blocked`.
- [ ] Keep `blocked` strict for unresolved metadata, stale/missing price data, and insufficient analysis history.
- [ ] Allow `reduced_confidence` suggestions when optional news/calendar/evidence is missing but fresh price/history and metadata are present.
- [ ] Allow `fallback_preview` output for heuristic or review-unavailable BUY/SELL candidates with confidence caps, visible warnings, and exclusion from any automated trading consumers.
- [ ] Add `missing_evidence` and `confidence_adjustments` arrays to recommendation rows.
- [ ] Update ranking so decision-grade picks rank first, reduced-confidence suggestions rank separately, and fallback previews do not silently replace reviewed picks.
- [ ] Add frontend sections for reviewed picks, lower-confidence suggestions, fallback previews, blocked data issues, and withheld review rejections.
- [ ] Add tests for publication tier assignment, confidence downgrades, ranking, and artifact compatibility.

## Milestone 4: Step Functions Orchestrator

- [ ] Add a CDK Step Functions state machine for the daily Stockara workflow.
- [ ] Start the state machine from one EventBridge daily schedule before the target analysis time.
- [ ] Invoke existing Lambdas through coarse workflow states instead of per-ticker Step Functions states.
- [ ] Add retries/catches for provider throttling, transient Lambda failures, manifest incompleteness, OpenAI failures, and artifact publish failures.
- [ ] Add a readiness decision state that chooses `publish`, `publish_degraded`, `wait_or_repair`, or `blocked`.
- [ ] Publish workflow execution status artifacts under `workflow/latest.json` and `workflow/history/{date}.json`.
- [ ] Add CloudWatch metrics/alarms for workflow failed, workflow degraded, workflow blocked, and workflow missing for the day.
- [ ] Add CDK tests for state machine IAM, schedule, Lambda invocations, retry/catch policies, and outputs.
- [ ] Run the orchestrator in manual or shadow mode before disabling existing independent schedules.

## Milestone 5: Retire Superseded Scheduling

- [ ] Disable the 5-minute analyzer publisher schedule after the Step Functions workflow is responsible for daily publication.
- [ ] Disable or narrow the 5-minute collection distributor schedule after the workflow can create, dispatch, wait, and repair the daily manifest.
- [ ] Disable or narrow the frequent bounded stock collector schedule after the workflow owns final price readiness and gap repair.
- [ ] Keep the 3-times-per-day global news schedule only if it provides useful prefetching within free quota; otherwise fold news collection fully into the daily workflow.
- [ ] Decide whether stock gap scan remains a separate after-market maintenance job or becomes a workflow step.
- [ ] Update runbooks, architecture docs, and smoke tests to treat the Step Functions execution as the source of daily operational truth.
