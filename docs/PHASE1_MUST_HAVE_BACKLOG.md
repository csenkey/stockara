# Phase 1 Must-Have Backlog

Phase 1 is an accurate, reliable stock analysis product intended to support real business and investment decisions. It is not a throwaway MVP, demo, or proof of concept.

The Phase 1 recommendation promise is to publish a very promising list of opportunities that are backed by fresh, reliable data and clear evidence. It does not need to claim the absolute top 5 or top 10 stocks across the full universe when coverage is partial. It must show business opportunities when they appear, but only for tickers whose own data quality is strong enough to support decision-grade analysis.

The items below are must-have gaps to close before treating Phase 1 recommendations as decision-grade.

## P0 - Decision-Grade Blockers

### 1. Reliable Seed Stock Metadata

**Status:** Done. Verified 2026-07-05: `data/watchlist_seed.csv` has 906 active-scope rows and no required metadata gaps. The seed handler rejects missing required static metadata on first seed, existing metadata can be synced without clobbering live collection fields, the enrichment tool supports gap-only runs that preserve already complete rows, and Phase 1 excludes unresolved metadata rows from decision-grade scoring. The 97 unresolved stale, inactive, renamed, acquired, ETF, or otherwise out-of-scope gap rows were removed from the active seed universe. The 3 currently listed gap rows (`BRK.B`, `BF.B`, and `GEF`) now have source-backed company identity plus explicit sector, industry, metadata source, source URL, and verification date.

**Gap:** The seed watchlist does not include sector metadata. `seed_watchlist_handler.py` uses a small `SECTOR_MAP` and defaults all unknown tickers to `Technology`.

**Why it matters:** Incorrect sectors corrupt sector filters, sector-relative analysis, portfolio views, and published explanations.

**Required outcome:**

- `data/watchlist_seed.csv` follows `docs/WATCHLIST_STATIC_METADATA_CONTRACT.md`.
- `data/watchlist_seed.csv` includes explicit, source-backed sector, industry, company name, metadata source, metadata URL/source ID, and verification date for every seeded ticker.
- `data/watchlist_seed.csv` includes source-backed AI context fields where available, including business description, flagship products, revenue segments, customer groups, durable risks, and competitive position.
- The seed handler rejects missing or invalid sectors instead of defaulting.
- Tests prove all Phase 1 seed rows have valid sector and company size values.

**Current verification:**

- `data/watchlist_seed.csv` has 906 active-scope rows.
- 0 rows are missing required metadata fields from `docs/WATCHLIST_STATIC_METADATA_CONTRACT.md`.
- No duplicate tickers were found.
- No invalid `company_size` values were found.
- The current gap list is tracked in `docs/WATCHLIST_METADATA_GAPS.md`.
- The current provider-coverage audit is tracked in `docs/WATCHLIST_METADATA_AUDIT.md` and `docs/WATCHLIST_METADATA_AUDIT.csv`; it has 0 unresolved decision-grade metadata rows.

**References:**

- `data/watchlist_seed.csv`
- `docs/WATCHLIST_STATIC_METADATA_CONTRACT.md`
- `docs/WATCHLIST_METADATA_AUDIT.md`
- `backend/src/scripts/seed_watchlist_handler.py`
- `backend/src/scripts/audit_watchlist_metadata.py`
- `.kiro/specs/stock-monitoring-system/requirements.md` Requirement 3.8

### 2. Recommendation Freshness Gates

**Status:** Done.

**Gap:** The Phase 1 pipeline can publish top picks even when stock data is stale, incomplete, or absent for some tickers. Missing data simply produces fewer signals.

**Why it matters:** Recommendations based on stale or partial data can look current and actionable when they are not.

**Required outcome:**

- Define minimum data freshness and completeness rules before a ticker can be scored or published.
- Suppress tickers with insufficient ticker-level data.
- Do not block publication solely because the full watchlist/universe is incomplete; publish the best available opportunities among eligible tickers.
- Publish coverage status that makes partial-universe analysis explicit.
- Publish data-quality warnings that identify stale, incomplete, or missing inputs.
- Tests cover stale stock data, missing news, incomplete backfill, and mixed-quality candidate sets.

**References:**

- `backend/src/analysis/phase1_pipeline.py`
- `backend/src/db/connection.py`
- `backend/src/api/health.py`

### 3. Explicit AI Fallback Policy

**Status:** Done.

**Gap:** If OpenAI is unavailable or not configured, the pipeline silently emits heuristic fallback BUY/HOLD/SELL recommendations with normal-looking confidence scores.

**Why it matters:** Users cannot distinguish AI-analyzed recommendations from degraded fallback outputs.

**Required outcome:**

- Store and publish `analysis_method` or equivalent provenance, such as `ai`, `fallback_heuristic`, or `suppressed`.
- Cap or downgrade confidence for fallback analyses.
- Decide whether fallback BUY/SELL recommendations are allowed in production, or whether they must be withheld.
- Emit metrics and warnings when fallback analysis is used.
- Tests cover missing OpenAI key, OpenAI errors, and fallback confidence behavior.

**Implemented policy:** Heuristic fallback analyses are stored with `analysis_method=fallback_heuristic`, include a `fallback_reason`, cap confidence via `PHASE1_FALLBACK_CONFIDENCE_CAP` defaulting to `55`, and withhold fallback-generated BUY/SELL recommendations from public publication unless `PHASE1_ALLOW_FALLBACK_ACTIONABLE_RECOMMENDATIONS=true` is explicitly configured. Publication artifacts include fallback policy counts and warnings.

**References:**

- `backend/src/analysis/phase1_pipeline.py`
- `backend/src/models/schemas.py`

## P1 - Reliability and Trust Gaps

### 4. Provider Provenance for Market Data

**Status:** Done.

**Gap:** Stored OHLCV records do not capture provider, adjusted/unadjusted status, exchange, currency, fetch window, or split/dividend adjustment context.

**Why it matters:** Business decisions require knowing exactly what data was analyzed and whether prices are comparable across corporate actions.

**Required outcome:**

- Stock data records include provider and data semantics metadata.
- The analyzer explicitly handles adjusted vs unadjusted close.
- Provider fallback records are distinguishable from primary-provider records.
- Tests verify stored provenance for yfinance and fallback providers.

**Implemented policy:** Stored OHLCV records include `data_provider`, `provider_symbol`, `provider_endpoint`, `provider_priority`, `price_adjustment`, adjusted-close availability, corporate-action adjustment context, split/dividend adjustment context, exchange, currency, fetch period, and fetch window. Yfinance records are marked as primary unadjusted OHLCV with adjusted close retained when available. Alpha Vantage, Nasdaq, and Stooq records are marked as fallback unadjusted OHLCV without adjusted close. Nasdaq provides the first no-key recent-history fallback for first-run analysis coverage; Stooq remains an opportunistic CSV fallback and challenge pages are rejected. Analysis prefers `adjusted_close_price` when available and otherwise uses raw `close_price`.

**References:**

- `backend/src/collectors/stock_collector.py`
- `backend/src/db/connection.py`

### 5. Collection Completeness and Failure Handling

**Status:** Done.

**Gap:** The stock collector can return success even when some tickers fail all providers. It does not persist failed ticker state or emit enough completeness metrics.

**Why it matters:** A green Lambda run can hide incomplete data collection.

**Required outcome:**

- Persist per-run collection summaries with selected, collected, duplicate, malformed, and failed ticker counts.
- Emit metrics for failed tickers and collection completeness percentage.
- Make failure thresholds explicit.
- Add retry or follow-up handling for failed tickers.
- Tests cover partial failure and all-provider failure behavior.

**Implemented policy:** Each stock collection run writes a historical `COLLECTIONSUMMARY#STOCK_COLLECTION / RUN#{timestamp}` record and updates the latest system status summary. Summaries include selected, successful, failed, duplicate, malformed, and no-data counts, the configured minimum completeness ratio, threshold pass/fail state, truncated ticker lists, and retry timing. DynamoDB summary writes preserve numeric values as `Decimal` so run summaries do not fail when completeness ratios are floats. CloudWatch metrics include completeness percentage, failed tickers, successful tickers, duplicate records, malformed tickers, no-data tickers, partial runs, failed/degraded runs, and threshold breaches. Failed tickers are marked on their stock metadata with failure reason, failed timestamp, retry-after timestamp, and failure count; successful collection clears stale failure markers. Due-stock selection prioritizes retryable failed tickers once the configured retry delay has elapsed.

**References:**

- `backend/src/collectors/stock_collector.py`
- `infrastructure/stacks/monitoring_stack.py`

### 6. Scan-Free Critical Access Patterns

**Status:** Done, with follow-up caveat.

**Gap:** Several critical paths still scan DynamoDB entity sets, including stock listing, latest prices, last collection timestamps, news by ticker, candidate scores, and candidate analyses.

**Why it matters:** Five years of data for 1000+ tickers can make scans slow, expensive, and unreliable.

**Required outcome:**

- Replace broad scans with keyed queries or summary/read-model records for critical access patterns.
- Add latest-price summary items or equivalent read model.
- Add ticker/date keyed news access or another efficient news lookup strategy.
- Health and publication checks should not scan all stock data.
- Add GSIs only when a repeated alternate query dimension cannot be served by existing keys or low-cost summary records.
- Evaluate whether system status summaries should be written by collector run completion as well as data writes; current summaries can represent the last successful inserted record rather than the last successful no-op collector run.

**References:**

- `backend/src/db/connection.py`
- `infrastructure/stacks/database_stack.py`

### 7. Signal Quality Upgrade

**Status:** Open.

**Gap:** Several signals score because data exists, not because the underlying data is directionally meaningful. The 2026-06-18 sharded AI run confirmed that many shortlisted candidates were based mostly on single-day price moves or volume spikes; the review model correctly withheld those actionable BUY/SELL calls because the evidence did not establish catalyst durability, trend context, valuation/fundamental support, sector context, or risk/reward.

**Why it matters:** Placeholder-like signals can inflate confidence and produce recommendations that look more evidence-based than they are.

**Required outcome:**

- Replace existence-based signals with directionally meaningful calculations.
- Separate neutral context from scored evidence.
- Require each published pick to include enough concrete evidence to support the recommendation.
- Reduce one-day-move-only candidates by adding richer multi-day technical context, sector-relative context, event/news catalyst context, and provider-backed signal direction before OpenAI analysis.
- Feed the model and review gate concrete evidence such as multi-day trend, relative strength versus sector ETF, volume persistence, catalyst source, upcoming event timing, valuation/fundamental context where available, and clear invalidation criteria.
- Keep the review gate strict; do not loosen it merely to make the dashboard non-empty.
- Tests cover scoring for analyst, options, insider, institutional, earnings, dividend, price, volume, news, and sector-relative signals.

**References:**

- `backend/src/analysis/phase1_pipeline.py`

### 8. News Timeliness and Ticker Classification

**Status:** Open.

**Gap:** The news collector is coded/documented for 15-minute polling, but CDK schedules it daily. Fallback ticker matching can produce false positives for short ticker symbols.

**Why it matters:** Timely and correctly classified news is central to catalyst analysis.

**Required outcome:**

- Align the deployed news schedule with the Phase 1 news freshness requirement.
- Use safer ticker extraction with word boundaries, known ticker universe checks, and false-positive handling for short/common tickers.
- Record source availability and classification confidence.
- Tests cover short ticker false positives and source outage behavior.

**References:**

- `backend/src/collectors/news_collector.py`
- `infrastructure/stacks/api_stack.py`

### 9. Static Business Context Sync

**Status:** Open.

**Gap:** `data/watchlist_seed.csv` includes source-backed business context fields such as `business_description`, `flagship_products`, `revenue_segments`, `primary_customers`, `competitive_position`, and `key_static_risks`, and the seed handler can store them on first empty-table bootstrap. However, the deployed seed custom resource skips when stock metadata already exists, so new or corrected CSV context does not automatically update existing DynamoDB stock metadata.

**Why it matters:** The analyzer should understand what a company actually sells, where revenue comes from, who the customers are, and what durable risks exist before interpreting catalysts. Stale business context can lead to weak or incorrect recommendation reasoning even when market data is fresh.

**Required outcome:**

- Add an explicit metadata sync path that compares the static watchlist CSV to existing stock metadata and updates changed source-backed static fields without clobbering live collection progress fields.
- Preserve provenance for context fields, including `metadata_source`, `metadata_source_url`, and `metadata_as_of`.
- Make sync behavior idempotent and safe to run after deploys or as a manual operator workflow.
- Surface changed, unchanged, missing, and invalid metadata counts in logs/metrics.
- Feed stored business context into candidate analysis prompts as neutral company context, not scored market evidence.
- Tests prove static context is stored on first seed and updated when CSV values change.

**References:**

- `data/watchlist_seed.csv`
- `docs/WATCHLIST_STATIC_METADATA_CONTRACT.md`
- `backend/src/scripts/seed_watchlist_handler.py`
- `backend/src/db/connection.py`

## P2 - Operational Readiness

### 10. Health Endpoint Should Evaluate Freshness

**Status:** Done.

**Gap:** `/api/health` reports `ok` when the database is reachable, even if data collection, analysis, or publication is stale.

**Why it matters:** Operational health must reflect whether the product is producing trustworthy current outputs.

**Required outcome:**

- Health status degrades when stock collection, news collection, analysis, or publication freshness violates SLA.
- Health response includes enough freshness details to diagnose stale components.
- Tests cover stale and fresh component states.

**Implemented policy:** `/api/health` reports per-component freshness for stock, news, earnings, dividend, analysis, and publication. Overall health degrades when a required component is missing, stale, or has an unparseable status timestamp while preserving the existing timestamp and summary fields for compatibility.

**References:**

- `backend/src/api/health.py`
- `backend/src/db/connection.py`

### 11. Product-Quality Alarms

**Status:** Open.

**Gap:** Monitoring alarms mainly catch Lambda errors, while many product failures return success with bad or incomplete outputs.

**Why it matters:** The system needs to alert on degraded data quality, not only exceptions.

**Required outcome:**

- Add alarms for zero published picks, low analyzed count, stale publication, provider outage, excessive ticker failures, and incomplete backfill.
- Dashboard shows collection completeness, provider availability, fallback usage, and publication freshness.
- Tests or CDK assertions cover critical alarm definitions.

**References:**

- `infrastructure/stacks/monitoring_stack.py`
- `backend/src/collectors/stock_collector.py`
- `backend/src/collectors/news_collector.py`
- `backend/src/analysis/phase1_pipeline.py`

### 12. Motto-Aligned Test Coverage

**Status:** Open.

**Gap:** Tests do not yet encode the Phase 1 reliability motto.

**Why it matters:** The product can regress into demo-quality behavior unless correctness and reliability expectations are executable.

**Required outcome:**

- Add tests for seed metadata completeness.
- Add tests for freshness gating and publication suppression.
- Add tests for fallback labeling and confidence downgrades.
- Add tests for provider provenance and collection completeness.
- Add tests for scan-free or summary-based access paths where practical.

**References:**

- `backend/tests/`
- `infrastructure/tests/`

## P3 - Low-Priority Phase 1 Enhancements

### 13. Company Logo Enrichment and Caching

**Status:** Open.

**Gap:** Ticker panels and company context views do not have source-backed company logos or icons. This is helpful for visual scanning and polish, but it is not required for decision-grade recommendation quality.

**Why it matters:** Logos make top picks, sell alerts, withheld candidates, and company-info panels easier to scan. They should be treated as presentation metadata, not as analysis evidence.

**Required outcome:**

- Add optional logo metadata fields to the static metadata contract and stock metadata sync path: `logo_url`, `logo_icon_url`, `logo_source`, `logo_source_url`, and `logo_checked_at`.
- Prefer a ticker-aware financial data provider first. Massive/Polygon ticker details expose `branding.logo_url` and `branding.icon_url`, and also expose active/delisted status that can help P0 universe hygiene.
- Use Logo.dev as a fallback when a reliable company domain is available from `website` or provider metadata. Logo.dev supports logo lookup by domain, stock ticker, ISIN, or crypto symbol.
- Avoid Clearbit Logo API because it is discontinued/unavailable for new users. Avoid random favicon scraping as the primary solution because quality and rights are inconsistent.
- Download and cache logo files into the Stockara artifact bucket instead of hotlinking provider URLs. Suggested paths: `logos/{ticker}/logo.svg`, `logos/{ticker}/icon.png`, and `logos/{ticker}/metadata.json`.
- Publish cached logo URLs through CloudFront and use those URLs in the frontend. Fall back to ticker initials when no source-backed logo is available.
- Keep logo enrichment separate from scoring. Missing logos must never suppress analysis, publication, or watchlist eligibility.

**Suggested implementation plan:**

- Extend `docs/WATCHLIST_STATIC_METADATA_CONTRACT.md` with optional logo fields.
- Extend `backend/src/scripts/enrich_watchlist_metadata.py` or add a sibling `enrich_watchlist_logos.py` that can run in `--only-gaps` / `--only-missing-logos` mode.
- Add provider helpers in priority order: Massive/Polygon ticker details, then Logo.dev domain/ticker fallback.
- Add S3 artifact publishing for downloaded logo bytes plus normalized logo metadata.
- Add frontend rendering support for cached `logo_icon_url` on compact ticker panels and `logo_url` in expanded company-info panels.
- Add tests covering provider normalization, cache key generation, missing-logo fallback, and no scoring impact.

**References:**

- `docs/WATCHLIST_STATIC_METADATA_CONTRACT.md`
- `backend/src/scripts/enrich_watchlist_metadata.py`
- `backend/src/scripts/seed_watchlist_handler.py`
- `frontend/src/pages/TopPicks.tsx`
