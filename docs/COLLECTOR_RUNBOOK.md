# Rock-Solid Collector Runbook

This runbook covers the Phase 1 daily data collection flow: market prices, news,
earnings calendar, dividend calendar, manifest coverage gates, and manual
recovery before the analyzer publishes recommendations.

## Normal Daily Flow

1. EventBridge runs the collection distributor.
2. The distributor writes `collection_manifest/YYYY-MM-DD.json` to the artifact
   bucket.
3. Price, news, earnings, and dividend workers process bounded ticker chunks.
4. Workers update task status, attempts, retry state, provider health, and output
   counts in the manifest.
5. The analyzer checks manifest coverage gates before scoring or publishing.
6. Published artifacts include `data_quality.collection_manifest` when the
   manifest is available.

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
  failures.

Do not override gates for normal publication unless the published artifact will
explicitly call out partial coverage.
