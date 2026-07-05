# Next Session Handoff: Phase 1 Signal Quality and News Classification

Date: 2026-07-05

## Current State

- Branch: `main`
- Work policy in current phase: commit directly to `main`; pushes deploy prod through GitHub Actions.
- P0 is done. `docs/PHASE1_MUST_HAVE_BACKLOG.md` shows all P0 blockers completed.
- Active workstream: P1/7 Signal Quality Upgrade, then P1/8 News Timeliness and Ticker Classification.
- P1/8 timeliness is partially done: direct news EventBridge collection now runs every 15 minutes.
- P1/8 ticker classification is intentionally after P1/7, per user request.

## Completed Today

Committed and deployed:

- `13ec782 Update GitHub Actions runtimes`
  - Updated GitHub Actions and CI Node runtime to current majors/Node 26.
- `920dfc9 Increase news collection frequency`
  - Changed direct news collection schedule from daily to every 15 minutes.
- `fee8e81 Separate context signals from scoring`
  - Neutral/context-only signals remain available as context but no longer inflate opportunity/risk scores.
- `dabc892 Require confirmation for one-day market moves`
  - One-day price/volume moves require same-direction confirmation before they affect ranking.

Current uncommitted work in progress:

- P1/7 event-signal quality after sector-relative scoring was committed.
- Files modified:
  - `backend/src/analysis/phase1_pipeline.py`
  - `backend/tests/test_phase1_pipeline.py`
  - `docs/PHASE1_MUST_HAVE_BACKLOG.md`
- Behavior implemented locally:
  - Earnings predictions now score historical reaction/surprise only when at least 3 prior reaction/surprise rows exist.
  - Earnings can still score from explicit positive/negative recent news catalyst keywords.
  - Earnings with thin history and no catalyst news becomes neutral context with `context_only=true` inside prediction metadata.
  - Dividend predictions now require at least 3 historical ex-dividend reaction rows before dividend yield/history affects ranking.
  - Dividends with thin reaction history become neutral context with `context_only=true`.
  - Added focused tests for thin earnings history, thin dividend history, and sufficient dividend reaction history.

## Verification Already Run For Current Uncommitted Work

```bash
/private/tmp/stockara-debug-venv/bin/python -m pytest backend/tests/test_phase1_pipeline.py -q
```

Result:

```text
51 passed
```

```bash
/private/tmp/stockara-debug-venv/bin/python -m ruff check backend infrastructure scripts
```

Result:

```text
All checks passed!
```

```bash
/private/tmp/stockara-debug-venv/bin/python -m pytest backend/tests -q
```

Result:

```text
311 passed
```

## Immediate Next Steps

1. Review the current diff:

   ```bash
   git diff --stat
   git diff -- backend/src/analysis/phase1_pipeline.py backend/tests/test_phase1_pipeline.py docs/PHASE1_MUST_HAVE_BACKLOG.md
   ```

2. If satisfied, commit:

   ```bash
   git add backend/src/analysis/phase1_pipeline.py backend/tests/test_phase1_pipeline.py docs/PHASE1_MUST_HAVE_BACKLOG.md docs/NEXT_SESSION_HANDOFF.md
   git commit -m "Require event evidence before event scoring"
   git push
   gh run list --branch main --limit 5
   gh run watch <run-id> --exit-status
   ```

3. After deploy is green, continue P1/7 in this preferred order:

   - Analyst/options/insider/institutional signal quality: avoid scoring mere data availability; score only directionally meaningful provider-backed activity.
   - Valuation/fundamental context where provider data exists.
   - Invalidation criteria enrichment in prompts/reviewer artifacts.

4. Then return to P1/8 ticker classification:

   - Word-boundary ticker extraction.
   - Active-watchlist ticker universe.
   - Suppress common-word short tickers unless provider-tagged or strongly disambiguated.
   - Add classification confidence/provenance fields to stored news summaries.

## Useful Commands

Backend checks:

```bash
/private/tmp/stockara-debug-venv/bin/python -m ruff check backend infrastructure scripts
/private/tmp/stockara-debug-venv/bin/python -m pytest backend/tests -q
```

Focused Phase 1 tests:

```bash
/private/tmp/stockara-debug-venv/bin/python -m pytest backend/tests/test_phase1_pipeline.py -q
```

Deploy watch:

```bash
gh run list --branch main --limit 5
gh run watch <run-id> --exit-status
```

---

# Historical Handoff: Stockara Data Bootstrap and Collection

Date: 2026-06-16

Update 2026-06-17: This handoff is historical. The worktree was later verified clean on branch `codex/phase1-static-metadata`; do not assume the "Current Local Changes" section below still describes uncommitted files. The verified open Phase 1 blocker is still seed metadata completeness: `data/watchlist_seed.csv` has 100 rows with required metadata gaps. Provider provenance for market data and collection completeness tracking have since been implemented and documented in `docs/PHASE1_MUST_HAVE_BACKLOG.md`.

Operational update 2026-06-17:

- The manual GitHub Actions workflow **Run Phase 1 Pipeline Now** exists and should be preferred over AWS Console Lambda test events for normal production backfill/diagnostics.
- The workflow invokes the deployed stock, news, earnings, dividend, and optionally publisher Lambdas in order and prints Lambda tail logs.
- A production diagnostic run proved invocation worked, but stock collection produced no data because yfinance/Yahoo returned empty JSON/429-style provider failures and Alpha Vantage was not configured.
- The same run exposed a DynamoDB write bug: collection summaries containing Python floats failed with `Float types are not supported. Use Decimal types instead.`
- The collector now preserves summary numerics as DynamoDB-safe `Decimal` values and includes a keyless Nasdaq historical-data fallback that stores a bounded recent history window per ticker. Stooq remains a last fallback only; it now rejects JavaScript verification pages.
- If top picks are still empty after deployment, run **Run Phase 1 Pipeline Now** with `stock_max_tickers=25` and inspect the stock collector tail logs first. AWS Console test events are still useful for isolated Lambda debugging, but they should not be the default runbook path.

## Context

The production `stockara` DynamoDB table was empty except for config data, so the scheduled collectors had no active tickers to process. A manual CloudShell seed command populated 1003 Phase 1 stock metadata records, but a full manual Lambda invoke of `stockara-stock-collector` timed out and produced many yfinance JSON parse failures such as:

```text
Failed to get ticker 'DTE' reason: Expecting value: line 1 column 1 (char 0)
```

The AWS CLI invoke timed out before writing `/tmp/stock.json`, and a DynamoDB scan showed no `stock_data` items after the attempted full run.

## User Requests

1. Modify CloudFormation/CDK so first deploy initializes DynamoDB with tickers if no ticker records exist.
2. Make stock collection avoid timeouts and provider rate/download limits.
3. On first collection, fetch 5 years of historical data; later fetch only missing/recent data.
4. Explain ticker/price storage and whether the current single-table design is good practice.

## Current Local Changes

Historical snapshot from 2026-06-16; superseded by the 2026-06-17 update above.

These files are modified locally and not committed:

- `backend/src/collectors/news_collector.py`
- `backend/src/collectors/stock_collector.py`
- `backend/src/db/connection.py`
- `backend/tests/test_news_collector.py`
- `backend/tests/test_stock_collector.py`
- `infrastructure/stacks/api_stack.py`

This file is new and untracked:

- `backend/src/scripts/seed_watchlist_handler.py`

## Implemented Changes

### First-run watchlist bootstrap

Added a CloudFormation custom resource backed by `backend/src/scripts/seed_watchlist_handler.py`.

Behavior:

- On create/update, checks DynamoDB for existing stock metadata via `GSI1PK = STOCK`.
- If at least one stock exists, it skips seeding.
- If none exist, it reads `data/watchlist_seed.csv` and batch-writes `STOCK#{ticker} / META` items.
- It also writes `CONFIG#sell_alert_watchlist` when configured with sell-alert tickers.
- On delete, it returns success without deleting data.

CDK wiring in `infrastructure/stacks/api_stack.py`:

- Adds `WatchlistSeedFunction`.
- Grants the seed function read/write permissions to the DynamoDB table.
- Adds a `cr.Provider` and `CustomResource`.
- Makes the stock collection EventBridge rule depend on the seed custom resource.

### Bounded stock collection

Updated `backend/src/collectors/stock_collector.py` so the collector no longer tries all 1000+ tickers in one Lambda run.

New environment-controlled behavior:

- `STOCK_COLLECTOR_BATCH_SIZE=5`
- `STOCK_COLLECTOR_MAX_TICKERS=25`
- `STOCK_INITIAL_HISTORY_PERIOD=5y`
- `STOCK_INCREMENTAL_PERIOD=10d`
- `YFINANCE_BATCH_PAUSE_SECONDS=1`

Collector logic now:

- Loads active stock metadata instead of only ticker strings.
- Selects due stocks, prioritizing never-collected tickers first, then oldest `latest_stock_data_date`.
- Honors explicit event overrides such as `tickers` and `max_tickers`.
- Fetches never-collected tickers with `period=5y`.
- Fetches previously collected tickers with `period=10d`.
- Uses small yfinance batches with `threads=False`, retry/backoff, and a pause between batches.
- Uses Alpha Vantage as a configured fallback, Nasdaq as the first no-key recent-history fallback, and Stooq as a last opportunistic CSV fallback for failed tickers.

The EventBridge stock collector schedule was changed to every 15 minutes with `{"max_tickers": 25}`. At that pace, the first 1000-ticker historical fill should complete in roughly 40 runs, about 10 hours, without one giant Lambda invocation.

### Stock metadata progress tracking

Updated `backend/src/db/connection.py`:

- `put_stock_data` now marks stock metadata after a successful insert.
- It writes:
  - `latest_stock_data_date`
  - `latest_stock_data_collected_at`
- The metadata update is conditional so older data cannot move the latest date backward.
- Duplicate stock price records are still skipped without overwriting existing data.

### News collector fix

Fixed a structlog bug in `backend/src/collectors/news_collector.py`.

Old behavior crashed with:

```text
logger.info(..., event=event) got multiple values for argument 'event'
```

The log field is now named `lambda_event`.

## Tests Already Run

Backend:

```bash
cd backend
/private/tmp/stockara-py312-venv/bin/python -m pytest tests -q
```

Result:

```text
86 passed, 6 warnings in 9.10s
```

Infrastructure:

```bash
cd infrastructure
env HOME=/private/tmp JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 /private/tmp/stockara-py312-venv/bin/python -m pytest tests -q
```

Result:

```text
7 passed
```

Diff hygiene:

```bash
git diff --check
```

Result: clean.

## Data Model Notes

Ticker metadata is stored as:

```text
PK = STOCK#AAPL
SK = META
entity = stock
ticker = AAPL
company_name = ...
sector = ...
company_size = blue_chip | mid_cap | startup
is_active = true
latest_stock_data_date = YYYY-MM-DD
latest_stock_data_collected_at = ISO timestamp
```

Daily OHLCV prices are stored as:

```text
PK = STOCKDATA#AAPL
SK = DATE#YYYY-MM-DD
entity = stock_data
ticker = AAPL
trading_date = YYYY-MM-DD
open_price = ...
high_price = ...
low_price = ...
close_price = ...
volume = ...
```

The current code stores raw OHLCV values, not adjusted close.

The one-table DynamoDB design is reasonable for this serverless app if access patterns stay explicit. It is good for fetching one ticker's history by partition key. The main risk is scanning across all stock-data rows: 1000 tickers times about 1250 trading days is around 1.25 million items for five years. Future "latest price for all stocks" views should use summary items or a GSI rather than scans.

## Provider Notes

yfinance/Yahoo is unofficial and fragile. It should be treated as opportunistic, not a guaranteed production data feed.

Useful alternatives discussed:

- Stooq: free daily historical CSV, good candidate for EOD fallback.
- Alpha Vantage: free tier is very limited, around 25 requests/day; paid tiers start around $49.99/month.
- Twelve Data: free Basic tier around 8 credits/minute and 800/day; paid Grow tier around $79/month monthly.
- Financial Modeling Prep: free Basic around 250 calls/day; Starter around $22/month annually and includes 5 years of historical data.
- Finnhub: has free and paid market-data APIs.

Recommended direction: keep yfinance as opportunistic primary for now, use Nasdaq as the first no-key recent-history fallback, keep Stooq opportunistic only because it can return JavaScript verification pages, and use Alpha Vantage only as a narrow fallback unless paid.

## Important Caveats

- The CDK tests passed, but a full `cdk synth` or `cdk deploy` was not run locally.
- The repo is currently on `main`, but `AGENTS.md` says implementation work should be on feature branches.
- Before committing/pushing, create a feature branch such as `codex/stock-collector-backfill-bootstrap`.
- The current production table already has 1003 stock metadata records from manual seeding, so the new custom resource should skip seeding in prod after deploy.
- The stock collector schedule change from daily 21:00 UTC to every 15 minutes intentionally diverges from the original `AGENTS.md` schedule expectation. This is to complete gradual historical backfill without timing out.

## Suggested Next Steps

1. Create a feature branch.
2. Re-run backend and infrastructure tests if needed.
3. Optionally run CDK synth to catch packaging/provider issues.
4. Commit the local changes.
5. Push the feature branch and let CI/CD deploy.
6. If deployment fails, inspect GitHub Actions/AWS logs, amend the commit, and retry.
7. After successful deploy, smoke test:
   - Confirm custom resource skipped or seeded as expected.
   - Confirm stock collector runs in bounded batches.
   - Confirm `stock_data` items appear.
   - Confirm stock metadata gets `latest_stock_data_date`.
   - Confirm health/top-picks behavior improves after enough data exists.
