# Next Session Handoff: Phase 1 Signal Quality and News Classification

> Steering note: this handoff is historical and dated 2026-07-05.
> The canonical current priority queue is `docs/steering/work-queue.md`.
> Use this file only for reconstructing past deployment/news-source hardening context.

Date: 2026-07-05

## Current State

- Branch: `main`
- Work policy in current phase: commit directly to `main`; pushes deploy prod through GitHub Actions.
- P0 is done. `docs/PHASE1_MUST_HAVE_BACKLOG.md` shows all P0 blockers completed.
- Active workstream: P1 signal quality and news quality hardening.
- P1/7 Signal Quality Upgrade is complete for the current P1 scope.
- P1/8 News Timeliness and Ticker Classification is complete for the current P1 scope.

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
- `4fb8310 Improve sector-relative signal scoring`
  - Sector-relative scoring now uses 5-session and 20-session relative returns versus sector ETFs with history/noise gates.
- `dafd899 Require event evidence before event scoring`
  - Earnings/dividend event scoring now requires sufficient historical reaction evidence or explicit catalyst news before affecting ranking.
  - Deploy run `28751856811` finished green with API and static smoke tests.
- `270f64d Require directional provider evidence before scoring`
  - Options, analyst, insider, and institutional live provider enrichment no longer scores mere data availability.
  - Deploy run `28752068928` finished green with API and static smoke tests.
- `b1a89b4 Add directional fundamental signal enrichment`
  - Fundamental/valuation live provider enrichment scores only coherent provider-backed quality, weakness, or valuation extremes.
  - Deploy run `28752247513` finished green with API and static smoke tests.
- `ac74296 Add signal-derived invalidation checks`
  - Analysis rows now carry signal-derived invalidation checks, and analysis/review prompts require concrete signal, price, event, or time-boxed invalidation conditions for actionable recommendations.
  - Deploy run `28753106170` finished green with API and static smoke tests.
- `6af14f7 Improve news ticker classification`
  - News ticker classification now filters to the active watchlist universe when supplied, uses word-boundary fallback matching, suppresses ambiguous common-word short tickers unless provider-tagged or strongly disambiguated, and persists classification confidence/provenance.
  - Deploy run `28753527158` finished green with API and static smoke tests.
- Pending commit in this session: improve news source availability reporting.
  - News provider status now distinguishes request health from article count.
  - Summaries record failed, skipped, and zero-article sources plus per-source status rows.
  - Deploy smoke warnings name failed configured sources instead of only reporting generic partial completeness.
- Pending follow-up commit in this session: harden news source failure details.
  - Provider error reasons are redacted before logging/storing, so query-string API keys do not leak in source-status output.
  - Nested ticker-classification confidence values are converted to DynamoDB Decimal values before persistence.
  - Manual run `28754126378` showed the new summary shape working: Finnhub and Alpha Vantage succeeded, NewsAPI returned HTTP 429, and two article writes failed before the Decimal fix.

Current local state:

- Local implementation changes are expected until the current source failure hardening commit is made.
- After deploy, trigger `run-news-collection-now.yml` once more. The expected remaining non-blocking issue is NewsAPI rate limiting; stored/logged reasons should no longer expose provider keys, and new articles with ticker classification confidence should store successfully.

## Verification Run For Latest Implementation Commit

```bash
/private/tmp/stockara-debug-venv/bin/pytest backend/tests/test_news_collector.py backend/tests/test_connection.py -q
```

Result:

```text
72 passed
```

```bash
/private/tmp/stockara-debug-venv/bin/ruff check backend infrastructure scripts
```

Result:

```text
All checks passed!
```

```bash
/private/tmp/stockara-debug-venv/bin/pytest backend/tests -q
```

Result:

```text
331 passed
```

## Immediate Next Steps

1. Commit, push, and watch the source failure hardening deployment.
2. Trigger `gh workflow run run-news-collection-now.yml` and inspect the run. It should only fail if the workflow still treats partial source coverage as fatal; the collector response should have redacted reasons and no DynamoDB float write failures.
3. Continue with the next highest-value P1 backlog item after the deploy is green.

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
