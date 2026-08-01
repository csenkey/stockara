# Rock-Solid Collector Runbook

This runbook covers the Phase 1 daily data collection flow: market prices, news,
earnings calendar, dividend calendar, manifest coverage gates, and manual
recovery before the analyzer publishes recommendations.

## Normal Daily Flow

1. EventBridge starts the `stockara-daily-pipeline` Step Functions workflow at
   21:05 UTC.
2. The distributor writes `collection_manifest/YYYY-MM-DD.json` to the artifact
   bucket.
3. Price, news, and dividend workers process bounded ticker chunks. The daily
   earnings worker uses one forward-looking Finnhub date-range request filtered
   against the full active watchlist, then a bounded rotating per-ticker
   fallback only when the range request produces no watchlist events.
4. Workers update task status, attempts, retry state, provider health, and output
   counts in the manifest.
5. The workflow waits for readiness, then invokes the analyzer/reviewer only
   after coverage gates are evaluated.
6. Published artifacts include `data_quality.collection_manifest` when the
   manifest is available.

The Step Functions execution and `workflow/latest.json` are the operational
source of truth. The old independent analyzer/distributor schedules are
disabled rollback paths. Use the after-market gap scan and three daily news
prefetches only as supporting jobs.

The global news collector is intentionally scheduled only three times per day.
The daily manifest also runs ticker-scoped news chunks, so frequent global
polling can exhaust low-cost NewsAPI development quotas without improving the
once-daily recommendation publication.

## Required Secrets

Store provider keys in AWS Secrets Manager and expose their secret names through
Lambda environment variables:

- `NEWSAPI_KEY_SECRET_NAME`
- `FINNHUB_KEY_SECRET_NAME`
- `ALPHA_VANTAGE_API_KEY_SECRET_NAME`
- `OPENAI_API_KEY_SECRET_NAME`

Supported JSON secret fields are provider-specific names such as
`NEWSAPI_KEY`, `FINNHUB_KEY`, `ALPHA_VANTAGE_API_KEY`, or `api_key`.

## First Daily Run

1. Confirm the watchlist seed has completed.
2. Confirm the artifact bucket exists and collector Lambdas have S3 read/write
   access.
3. Run the distributor or wait for its scheduled run.
4. Inspect the manifest summary:
   - `pending_tasks`
   - `running_tasks`
   - `retry_wait_tasks`
   - `failed_tasks`
   - `coverage_gates`
5. Run or wait for worker Lambdas until required coverage gates pass.
6. Run the analyzer only after failed gates are resolved or intentionally
   accepted as partial coverage.

## Manual Watchlist Metadata Sync

Use the `Sync Watchlist Metadata Now` GitHub Actions workflow when
`data-readiness/latest.json` reports `metadata_drift:*` rows. The workflow
invokes only the deployed `stockara-watchlist-seed` Lambda with
the shared repair-mode payload shape and validates the repair summary before it
marks the run green.

Shared repair-mode payload fields:

- `mode`: repair command, such as `sync_static_metadata`.
- `run_date`: optional collection or publication date to repair.
- `tickers`: optional target ticker list.
- `max_tickers`: optional upper bound for broad scans.
- `provider_budget`: optional provider-to-call-budget map for quota-aware modes.
- `dry_run`: whether the mode should report planned work without writing changes.

Stock collector repair modes currently supported:

- `repair_history`: restores or backfills historical OHLCV rows for selected or
  due tickers.
- `repair_price_gaps`: backfills a specific missing price date for selected
  tickers. When only `run_date` is provided, it repairs that single date.

Use `Run Stock Gap Scan Now` with `repair_after_scan=true` to scan recent price
history and immediately run a bounded number of `repair_price_gaps` invocations
from `price-gaps/latest.json`. Keep `repair_max_tasks` small while provider
health or quota state is uncertain.

News repair mode currently supported:

- `repair_news`: fetches targeted or broad news with the shared
  `provider_budget` map. In `Run News Collection Now`, use
  `provider_budget_json` to cap or disable providers, for example
  `{"newsapi":1,"finnhub":5,"alpha_vantage":1}`. Set a provider budget to `0`
  to skip that provider for the run.

Earnings repair behavior:

- An uncapped `repair_calendars` earnings invocation scans the full active
  watchlist in one Finnhub request; do not add `max_tickers` to the scheduled
  workflow input.
- `fallback_max_tickers` caps the rotating yfinance/Alpha Vantage fallback used
  when Finnhub is empty or unavailable. The daily workflow uses 25, so fallback
  calls stay bounded and do not permanently favor alphabetically early tickers.
- A zero-event run returns `status=degraded`, publishes provider diagnostics and
  warnings in `calendar/normalized/earnings/latest.json`, and increments
  `earnings_provider_degraded_runs`. It must not be interpreted as a successful
  proof that no tracked companies have upcoming earnings.
- The workflow status artifact includes `events_collected`,
  `selected_ticker_count`, `provider_health`, and `warnings` for calendar steps.

Expected summary fields:

- `created`: active seed rows that were missing in DynamoDB and were inserted.
- `missing`: the same newly-created count, kept for operator readability.
- `changed`: existing metadata rows updated from `data/watchlist_seed.csv`.
- `unchanged`: existing metadata rows that already matched the packaged seed.
- `invalid`: malformed seed rows. The workflow fails when this is non-zero.
- `inactive`: production stock rows absent from the seed and already marked
  inactive.
- `out_of_scope`: active production stock rows absent from the seed. These rows
  should be reviewed because the analyzer can still treat them as eligible.

After a successful sync, run `Analyze Phase 1 Now` to republish
`data-readiness/latest.json` and confirm the metadata drift warning cleared.

## Manual Retry

Use the task `task_id`, `manifest_bucket`, and `manifest_key` from the manifest.
Invoke the matching worker Lambda with:

```json
{
  "mode": "manifest_task",
  "manifest_bucket": "<artifact-bucket>",
  "manifest_key": "collection_manifest/YYYY-MM-DD.json",
  "task_id": "<task-id>"
}
```

Tasks in `retry_wait` should not be retried until `next_retry_at`, unless the
operator has confirmed the provider quota or outage has recovered.

## Manual Earnings History Backfill

For one-time earnings-calendar history fills, prefer the local operator script
over adding a short-lived Lambda. It uses the same yfinance normalization and
DynamoDB row shape as the earnings collector, paces requests between tickers,
and upserts by ticker/date.

Dry-run a small batch first:

```bash
STOCKARA_TABLE_NAME=<table-name> \
AWS_REGION=<region> \
python -m scripts.backfill_earnings_calendar_history \
  --max-tickers 10 \
  --sleep 1.5 \
  --dry-run
```

Run the backfill in paced chunks. Chunked runs should write DynamoDB only; the
daily publisher will rebuild the public `top-picks/latest.json` calendar view
from the full database state.

```bash
STOCKARA_TABLE_NAME=<table-name> \
STOCKARA_ARTIFACT_BUCKET=<artifact-bucket> \
AWS_REGION=<region> \
python -m scripts.backfill_earnings_calendar_history \
  --max-tickers 100 \
  --offset 0 \
  --limit 64 \
  --sleep 1.5
```

Repeat with increasing `--offset` values until the active watchlist is covered.
After the final chunk, run the analyzer/publisher so
`top-picks/latest.json` includes the refreshed upcoming earnings summary.
If `--publish-artifact` is used with `--max-tickers`, `--offset`, or `--tickers`,
the script writes a scoped manual artifact under the collection date and does
not overwrite `calendar/normalized/earnings/latest.json`. Only an uncapped
all-active run refreshes that audit `latest.json`.

## Provider Outage Triage

Check task `failure_reason`, `provider_attempts`, and ticker health values:

- `rate_limited`: wait for `next_retry_at`; for Alpha Vantage and NewsAPI this
  may be the next daily quota window.
- `transient_failure`: retry after backoff; inspect provider status and Lambda
  network errors.
- `provider_unsupported`: do not keep retrying blindly; verify provider coverage.
- `symbol_mapping_needed`: add or correct `provider_symbols` on the stock
  metadata.
- `inactive_or_delisted`: quarantine or deactivate the ticker if confirmed.

## Quarantining Bad Tickers

If a ticker repeatedly blocks collection:

1. Verify whether the company is active and the canonical ticker is correct.
2. Add provider-specific symbols where needed, for example
   `provider_symbols = {"stooq": "brk-b.us", "alpha_vantage": "BRK-B"}`.
3. If the ticker is inactive or delisted, set `is_active=false` in metadata.
4. Re-run only affected manifest chunks.

## Analyzer Gate Failures

The analyzer suppresses normal publication when required manifest gates fail.
Common fixes:

- `price_freshness`: retry price chunks or inspect symbol mappings.
- `news_freshness`: verify NewsAPI and Finnhub secrets, quotas, and worker logs.
- `calendar_coverage`: retry earnings/dividend chunks and inspect yfinance
  failures. For earnings, first inspect the Finnhub status under
  `provider_health.providers`, then the bounded yfinance/Alpha Vantage fallback
  statuses. `provider_returned_zero_events` and `providers_unavailable` are
  degraded collection incidents, not empty-calendar confirmations.

Do not override gates for normal publication unless the published artifact will
explicitly call out partial coverage.
