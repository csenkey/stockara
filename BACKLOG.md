# Backlog

## Immediate Priority: Rock-Solid Data Collection

Status: Proposed

Requirement: Make Stockara's data collection reliable enough that the daily analysis pipeline can trust the input coverage, freshness, and provider provenance before publishing any recommendations.

Rationale:

- The current Phase 1 product depends on decision-grade inputs, not just a Lambda invocation that returns `200`.
- News, daily OHLCV, earnings calendar, and dividend calendar must be collected for the full tracked universe before the once-daily analysis run.
- Provider failures, missing credentials, throttling, unsupported tickers, stale data, and partial coverage must be explicit and measurable.
- The collector system should tolerate several hours of small Lambda runs instead of depending on one long batch completing perfectly.
- Daily analysis should run once after collection has reached defined coverage thresholds: filters -> AI analysis -> stronger AI review -> publish.

Target operating model:

- Data collection runs continuously or frequently throughout the day.
- A distributor Lambda owns a daily collection manifest stored in S3, split by task type and ticker chunks.
- Worker Lambdas claim bounded chunks for price, news, earnings, dividend, and future signal sources.
- Every task records status, attempt count, provider used, failure reason, next retry time, and output coverage.
- The analyzer starts only when the manifest meets minimum coverage gates or publishes with explicit partial-coverage warnings.

Execution tasks:

- [x] Wire NewsAPI, Finnhub, and Alpha Vantage provider credentials through AWS Secrets Manager-backed Lambda configuration.
- [x] Prevent failed stock tickers from starving healthy/stale tickers in repeated short stock-collector runs.
- [x] Update deployed smoke tests and GitHub Actions manual runs to fail when collector responses contain `status=failed`, `status=error`, zero useful records, or completeness below configured thresholds.
- [x] Add a `collection_manifest/{date}.json` S3 schema covering task type, ticker range, status, attempts, provider, timestamps, failure reason, and output counts.
- [x] Implement a collection distributor Lambda that creates the daily manifest and schedules or invokes bounded worker tasks across the full active watchlist.
- [x] Refactor stock price collection into idempotent chunk workers that update manifest task state after each run.
- [x] Refactor news collection into chunkable ticker-aware collection, including general market news plus ticker/company-specific searches.
- [x] Add earnings-calendar task chunks for the full active watchlist with provider throttling and retry state.
- [x] Add dividend-calendar task chunks for the full active watchlist with provider throttling and retry state.
- [x] Introduce provider/ticker health state: `healthy`, `transient_failure`, `rate_limited`, `provider_unsupported`, `symbol_mapping_needed`, `inactive_or_delisted`.
- [x] Add symbol-mapping/provenance fields so provider symbols like Yahoo, Nasdaq, Stooq, Alpha Vantage, NewsAPI, and Finnhub can differ from canonical ticker when needed.
- [x] Add rate-limit aware backoff policies per provider, including daily quota budgeting for free-tier APIs.
- [x] Add collection coverage gates before daily analysis: price freshness, news freshness, earnings coverage, dividend coverage, and minimum active-universe percentage.
- [ ] Update the analyzer/publisher so daily analysis runs once after collection gates: cheap filters -> AI analysis -> stronger AI review -> publish.
- [x] Publish collection coverage metadata with every daily artifact so partial universe coverage is visible to users and later diagnostics.
- [x] Add CloudWatch metrics and alarms for manifest age, incomplete manifest tasks, provider failures, retry exhaustion, low coverage, and stale inputs.
- [x] Add a stock price gap scanner that detects missing recent trading-day OHLCV rows per active ticker.
- [x] Queue missing stock-price ranges as date-bounded manifest tasks instead of introducing a separate task table.
- [x] Teach the stock collector to execute date-bounded gap backfill tasks through existing yfinance, Nasdaq, and Stooq provider logic.
- [x] Schedule the stock gap scanner after market close and normal collection, with CloudWatch metrics for detected gaps and inserted backfill records.
- [x] Add tests for gap detection, manifest task creation, collector gap-backfill execution, and CDK wiring.
- [x] Add a collector runbook covering secret setup, first daily run, manual retry, provider outage triage, and how to quarantine bad tickers.
- [x] Add integration tests with mocked providers for manifest creation, task claiming, retry behavior, partial provider failure, and analysis gating.

Nice-to-have follow-up:

- [ ] Rework one-time 5-year historical OHLCV restoration to avoid Lambda recursive-loop detection.
  - Context: AWS reported recursive invocation termination on 2026-06-19, likely from the chained Stooq/historical backfill path. That may explain why roughly one year of stock history loaded instead of the intended five years.
  - Prefer Step Functions, SQS, or EventBridge Scheduler over Lambda self-invocation for long backfill runs.
  - Keep the recent 90-day gap scanner separate; it protects current analysis freshness but intentionally does not restore older multi-year history.
  - Current analyzer mainly uses recent OHLCV windows: 30-day minimum history, about 45-day freshness validation, 60-day price/volume context, and short sector-relative windows. A 5-year restore is useful for completeness, future backtesting, and archive quality, not an urgent blocker for daily recommendations.

Acceptance criteria:

- A full active watchlist collection can complete through multiple short Lambda runs without duplicate writes or lost tasks.
- News, price, earnings, and dividend collection expose per-source success/failure and coverage metrics.
- A few bad tickers or provider-specific unsupported symbols cannot block collection for the rest of the universe.
- Daily analysis does not publish normal recommendations when required input freshness or coverage gates fail.
- Partial coverage, when allowed, is explicitly visible in published artifacts and health output.
- GitHub Actions/manual smoke workflows fail on collection-quality failures, not only on Lambda runtime errors.

## Product Roadmap

### Define Phased Shipping Plan

Status: Proposed

Requirement: Ship Stockara in focused phases, starting with a narrow daily recommendation MVP and expanding only after the core data, analysis, and trust loops are working.

Rationale:

- Keeps the first release small enough to validate quickly.
- Makes each phase easy to explain through one user-facing promise.
- Avoids building portfolio automation or intraday complexity before the daily analysis loop is reliable.
- Creates a clear path from recommendation generation to personalization, public validation, and premium features.

Phase 1: Daily top picks and risk alerts

- Promise: every day, surface the 5-10 most promising near-term opportunities and any urgent negative sell signals worth immediate attention.
- Collect the full Phase 1 signal set: OHLCV price/volume data, financial news, earnings calendar, dividend calendar, stock metadata, configurable watchlists, options activity, analyst rating changes, insider buying/selling, institutional-flow signals, social/news momentum, and sector ETF movement.
- Run cheap signal scanning across the tracked universe before running expensive AI analysis.
- Prioritize stocks with upcoming earnings, upcoming dividends, unusual price or volume movement, strong news catalysts, analyst changes, insider/institutional signals, options activity, social/news momentum, and sector movement.
- Run deeper AI analysis only for the shortlisted candidates instead of analyzing every ticker every day.
- Publish the daily top 5-10 picks with ticker, recommendation, confidence or risk level, catalyst, expected timeframe, short rationale, supporting evidence, and analysis date.
- Publish urgent sell alerts from a configurable watchlist when negative signals cross defined severity thresholds.
- Serve the user-facing daily picks and alerts as static S3/CloudFront JSON/HTML artifacts wherever possible, avoiding live REST/database reads for public read paths.
- Defer portfolio optimization and trading automation until Phase 2; Phase 1 focuses on global daily opportunities and risk alerts.

### Phase 1: Daily Top Picks and Risk Alerts

Status: Proposed

Requirement: Build a low-cost daily catalyst scanner that finds and publishes the top 5-10 stock opportunities plus urgent sell alerts from configurable watchlists, while using AI only on shortlisted candidates.

Rationale:

- The product should identify promising near-term movement, not merely analyze a large ticker list mechanically.
- Static generated read models keep public page views cheap and fast.
- Cheap signals should narrow the candidate set before OpenAI analysis to control variable cost.
- Urgent sell alerts are a separate product surface from opportunity picks and should use stronger negative-signal thresholds.
- Phase 1 should collect all planned signal categories, even if some sources start as simple, low-frequency, or provider-limited integrations.

Deliverables:

- Daily static top-picks page and JSON artifact.
- Daily static urgent sell-alert page and JSON artifact.
- Configurable tracked universe and urgent sell-alert watchlist.
- Candidate scanner that scores cheap signals across the tracked universe.
- AI analyzer that runs only on the shortlisted candidates.
- Publisher job that writes versioned and latest static artifacts to S3.
- Frontend page or dashboard panel that reads static artifacts, not a live daily-pick API.
- Deployment smoke tests that verify the static artifacts are generated and publicly readable.

Data sources and signal coverage:

- OHLCV price and volume data for all tracked tickers.
- Financial news articles and AI summaries related to tracked tickers.
- Earnings calendar with upcoming earnings date, confirmed/estimated status, and days until event.
- Dividend calendar with ex-dividend date, pay date, dividend amount, and yield where available.
- Stock metadata with ticker, company name, sector, company size, active flag, and source provenance.
- Configurable tracked universe for opportunity discovery.
- Configurable urgent sell-alert watchlist.
- Options activity signals, including unusual volume, put/call skew, and notable implied-volatility changes where provider data allows.
- Analyst rating-change signals, including upgrades, downgrades, price-target changes, and source/date.
- Insider buying/selling signals, including transaction direction, size, date, and insider role.
- Institutional-flow signals, including available ownership/flow changes or provider-derived accumulation/distribution signals.
- Social and news momentum signals, including mention volume, sentiment direction, and acceleration.
- Sector ETF movement signals, mapping tickers to representative sector ETFs and comparing stock movement against sector movement.

Execution tasks:

- [ ] Define Phase 1 output schemas for `TopPick`, `SellAlert`, `CandidateSignal`, `SignalSource`, and `PublishedTopPicks`.
- [x] Define static artifact paths, including `/top-picks/latest.json`, `/top-picks/history/{date}.json`, `/sell-alerts/latest.json`, and `/sell-alerts/history/{date}.json`.
- [x] Add DynamoDB or repository models for signal snapshots, candidate scores, AI candidate analysis, top-pick publication records, and sell-alert publication records.
- [x] Add configuration storage for the tracked universe and urgent sell-alert watchlist.
- [x] Add a seed/bootstrap script for the initial tracked universe with ticker, company name, sector, company size, and active flag.
- [x] Wire provider secrets into deployed Lambdas through environment variables or Secrets Manager-backed configuration.
- [x] Implement earnings-calendar collection.
- [x] Implement dividend-calendar collection.
- [x] Extend OHLCV collection to compute daily price/volume movement signals.
- [ ] Extend news collection to compute ticker-level news volume, sentiment direction, and interesting-news indicators.
- [ ] Implement options activity signal collection with provider fallback behavior.
- [ ] Implement analyst rating-change signal collection with provider fallback behavior.
- [ ] Implement insider transaction signal collection with provider fallback behavior.
- [ ] Implement institutional-flow signal collection with provider fallback behavior.
- [ ] Implement social/news momentum signal collection with provider fallback behavior.
- [ ] Implement sector ETF movement collection and ticker-to-sector-ETF mapping.
- [ ] Make yfinance enrichment signals safe under Yahoo rate limits during Phase 1 scoring.
  - 2026-06-30 production run note: `Analyze Phase 1 Now` score batch 0 hit Yahoo `429 Too Many Requests` and `crumb=Edge: Too Many Requests` while fetching optional yfinance quoteSummary/options/holder/recommendation data and sector ETF context such as `XLK`.
  - Treat options, analyst, insider, institutional, and sector-relative yfinance enrichments as optional signals with a circuit breaker after the first clear Yahoo throttling response in a Lambda invocation.
  - Prefer stored OHLCV, stored market signals, news, earnings, and dividend data for baseline scoring so manual scoring can continue when Yahoo enrichment is unavailable.
  - Add configuration to disable live yfinance enrichment during manual GitHub Actions scoring runs, or make it opt-in separately from normal stored-data scoring.
  - Reduce log noise by emitting one structured provider-throttled warning/metric per invocation instead of repeated stack/log lines for each ticker.
- [ ] Implement candidate scoring with configurable weights for earnings, dividends, price move, volume move, news, options, analyst, insider, institutional, social/news momentum, and sector-relative movement.
  - Current implementation has an initial scoring pipeline; remaining work is to add/configure the full provider signal set and weights.
- [ ] Implement negative-signal scoring for urgent sell alerts with severity thresholds.
  - Current implementation has initial negative-score handling; remaining work is stronger threshold configuration and provider-backed negative signals.
- [x] Select the daily AI candidate shortlist from the highest-scoring opportunity candidates plus any high-severity negative candidates.
- [ ] Update the AI prompt to include catalyst signals, upcoming events, news evidence, technical indicators, sector-relative movement, and invalidation criteria.
  - Current prompt includes catalyst/risk framing; remaining work is adding upcoming-event, richer technical, and sector-relative context.
  - 2026-06-18 production run note: the review model withheld many actionable calls because evidence was mostly single-day price/volume movement. Improve candidate evidence before AI analysis rather than loosening the review gate or publishing weak calls.
- [x] Store AI analysis only for shortlisted candidates and include candidate score/source details for traceability.
- [x] Rank AI-analyzed candidates into top 5-10 picks using recommendation, confidence, catalyst strength, risk, and timeframe.
- [x] Rank urgent sell alerts separately using severity, confidence, negative catalyst type, and recency.
- [x] Implement a static publisher Lambda or scheduled job that writes latest and historical JSON artifacts to S3.
- [ ] Optionally generate static HTML pages for top picks and sell alerts after the JSON publisher is stable.
- [ ] Update the frontend to render top picks and sell alerts from static JSON artifacts.
- [ ] Improve the published recommendation detail cards for trust and usability.
  - Show the ticker's real company name prominently, and add a company/ticker logo when a reliable source is available.
  - Improve static price chart notation: show price values on the y-axis, expose date/price/OHLC/volume values on hover where the chart is interactive, and ensure SMA20 is computed from enough pre-window history so the line is available at the start of the displayed range when possible.
  - Remove duplicated evidence lines such as repeated analyst recommendation mix text.
  - Make related news easy to inspect, including article summaries, source/publication date, and links to the original articles.
  - Add an event calendar section for upcoming earnings, dividends, and other collected ticker events when available.
  - Keep empty states explicit when logos, news links, or upcoming events are unavailable.
- [ ] Add company intelligence details to ticker cards.
  - Prefer a compact inline expansion or side drawer opened from the ticker card/info icon, rather than a blocking modal, so users can compare Top Picks, Urgent Sell Alerts, and Withheld recommendations without losing page context.
  - Include a brief company description, top products or business segments, brief history, headquarters/exchange/industry when available, website, and metadata provenance.
  - Extend the stock metadata contract and enrichment collector if current metadata is missing these fields; cache provider results and keep empty states explicit when a profile source is unavailable.
  - Make the interaction keyboard accessible and mobile-friendly, with one expanded/detail surface at a time on small screens.
  - Add frontend tests or build fixtures for cards with full company info, partial info, and no info.
- [ ] Give Withheld AI Recommendations the same decision-support context as published picks.
  - Render supporting evidence, recent related news, and upcoming event calendar details for withheld recommendations, not only Top Picks and Urgent Sell Alerts.
  - Ensure `review_rejections` in `top-picks/latest.json` includes `related_news`, `upcoming_events`, `price_chart`, `supporting_evidence`, source traceability, and company metadata with the same shape used by public picks/alerts where possible.
  - Group withheld rows by rejection category or missing-evidence theme so the section is useful for human research instead of reading like discarded output.
  - Keep reviewer concerns and "Needed" text visible, but add links from each needed item to the evidence/news/events/company sections that could resolve it.
  - Add empty states that distinguish "no relevant evidence found" from "collector did not run or provider failed".
- [ ] Convert latest withheld recommendation "Needed" notes into a data collection plan.
  - Fetch the latest published `top-picks/latest.json` artifact, inspect `review_rejections`, and summarize withheld tickers by recommendation, rejection category, concern, and `what_would_make_approvable`.
  - Latest inspected artifact: `https://dbrz5lfasrion.cloudfront.net/top-picks/latest.json`, publication date `2026-07-02`, generated at `2026-07-02T22:03:47.805829`, with 904 candidates, 50 analyzed, 0 top picks, 0 sell alerts, and 13 withheld actionable AI recommendations.
  - Latest withheld distribution: 10 BUY and 3 SELL recommendations; 12 rejected as `insufficient_support` and 1 as `insufficient_support_and_data_quality`.
  - Latest withheld tickers: BUY `AXON`, `ABBV`, `AAL`, `ADPT`, `ACHC`, `AGIO`, `AMPL`, `AGYS`, `ALGM`, `ANET`; SELL `VZ`, `SMCI`, `T`.
  - Classify each needed item into actionable collection gaps such as fresh ticker-specific news, earnings transcript or guidance, multi-day OHLCV confirmation, sector-relative movement, analyst rating change, insider/institutional activity, options activity, social/news momentum, or company profile context.
  - Highest-priority latest gaps:
    - Extract and summarize SEC 8-K substance instead of citing the filing generically; most withheld BUY rows mention an unexplained 8-K.
    - Add valuation and fundamental context: revenue, margins, guidance, peer/history valuation, downside risk, and business impact.
    - Correct metadata/sector quality for misclassified tickers such as `AAL`, `AGIO`, `VZ`, and `T`.
    - Add company-specific catalyst verification so broad market/news items do not masquerade as ticker evidence.
    - Add technical confirmation context for momentum-driven calls: support/resistance, breakout/invalidation levels, multi-session volume confirmation, relative performance, and trade horizon.
    - For SELL calls, require recent company-specific negative catalysts such as earnings/guidance deterioration, analyst estimate cuts, adverse news, margin/order concerns, regulatory/accounting risk, or sustained relative underperformance.
  - Add a structured `needed_evidence` array to withheld rows so the frontend can show "what is missing", "how to collect it", "provider/status", and "next retry or fallback" instead of only free-text reviewer prose.
  - Wire collector manifests and provider health into the needed evidence plan so missing data can point to concrete tasks, unsupported tickers, rate limits, stale inputs, or symbol-mapping work.
  - Prioritize new collectors/fallbacks based on the latest withheld distribution, with the first implementation pass focused on the evidence gaps that suppress the most otherwise-actionable recommendations.
  - Current artifact shape note: withheld rows already include `related_news` for 9 of 13 rows, `price_chart` for all 13, `supporting_evidence` for all 13, and `upcoming_events` as empty arrays; they do not currently include `source_traceability`.
- [ ] Keep existing authenticated portfolio views separate from Phase 1 global picks.
- [ ] Add unit tests for candidate scoring, negative-signal thresholds, ranking, and static artifact generation.
  - Candidate scoring, ranking, publication gating, artifact generation, and fallback behavior have unit coverage; remaining work is expanded tests for future provider-backed signal weights and negative thresholds.
- [x] Add integration-style tests with mocked providers for the daily scan-to-publish flow.
- [ ] Add frontend tests or build checks for rendering empty, loading, successful, and stale-artifact states.
- [ ] Update GitHub Actions deployment smoke test to verify `/api/health` plus public readability of `top-picks/latest.json` and `sell-alerts/latest.json` when artifacts exist.
- [ ] Add a manual bootstrap/runbook for first deployment: seed universe, run collectors, run scanner/analyzer, publish static artifacts, verify CloudFront URLs.
- [ ] Add CloudWatch metrics for signals collected, candidates scored, AI candidates analyzed, top picks published, sell alerts published, provider failures, and artifact publish failures.
  - Implemented collection completeness, candidate/publisher funnel, publication, provider/source failure, and artifact failure metrics; remaining work is signal-specific metrics as new provider collectors land.
- [x] Add alarms for failed daily publication, missing fresh top-picks artifact, missing fresh sell-alert artifact, and repeated provider failures.

Acceptance criteria:

- A deployed environment can publish top 5-10 daily picks as static JSON without requiring a live API/database read for page views.
- A deployed environment can publish urgent sell alerts from a configurable watchlist as static JSON.
- AI analysis is limited to the candidate shortlist, not the full tracked universe.
- Every published pick includes catalyst, confidence, risk, timeframe, rationale, supporting evidence, analysis date, and source traceability.
- Every urgent sell alert includes severity, negative catalyst, confidence, rationale, supporting evidence, analysis date, and source traceability.
- The tracked universe and urgent sell-alert watchlist can be changed without code changes.
- Provider failures are logged and surfaced in metrics without breaking the whole daily publication when fallback or partial data is available.
- Public static artifacts are cacheable through S3/CloudFront and can be consumed by the frontend.

Phase 2: Portfolio-aware suggestions

- Promise: given the user's holdings, explain what they should consider doing next.
- Encrypt user portfolios at rest and decrypt only in memory.
- Personalize suggestions around buy more, hold, sell, diversify, reduce exposure, and sector balance.
- Add stock detail pages with historical analysis.
- Add user preferences for risk tolerance, sectors of interest, excluded tickers, and investment style.
- Keep execution manual; Stockara recommends and explains, while the user decides.

Phase 3: Demo trading league

- Promise: publicly show how the recommendations perform over time.
- Launch 100 superhero-named demo accounts that each start with exactly `$10,000.00`.
- Execute daily demo trades from AI recommendations with 1% commission on every transaction.
- Publish unauthenticated leaderboard, account detail, performance, holdings, and transaction-history pages.
- Use the demo league as both a validation loop and a public product surface.

Phase 4: Intraday intelligence

- Promise: react to meaningful market and news changes during the trading day.
- Increase data and news collection frequency.
- Add intraday recommendation refreshes.
- Detect price breakouts, sudden news volume, recommendation flips, and risk changes.
- Add alerts for important changes, such as a holding changing from `HOLD` to `SELL` or a strong `BUY` appearing in a preferred sector.
- Avoid turning the product into a high-frequency day-trading tool; focus on meaningful changes and explainability.

Phase 5: Strategy and backtesting

- Promise: help users trust, compare, and tune the recommendation system.
- Backtest recommendation strategies against historical data.
- Compare AI picks against relevant benchmarks.
- Show win rate, drawdown, volatility, and sector performance over time.
- Add strategy profiles such as conservative, balanced, aggressive, income, and growth.
- Use demo trading results as a visible credibility signal.

Phase 6: Automation and premium layer

- Promise: make Stockara a reliable investment assistant worth returning to.
- Add advanced alerts, personalized reports, and multi-portfolio support.
- Explore tax-aware or account-type-aware suggestions when the core recommendation quality is proven.
- Consider broker integration only after the product has earned enough user trust.
- Reserve premium tiers for higher frequency, deeper personalization, stronger alerting, and advanced analytics.

## Product Experimentation

### Add Alpha/Beta Channels for AI Stock Analysis

Status: Proposed

Requirement: Support experimentation with new AI-assisted stock analysis tools by keeping the latest `main` analysis as Alpha and allowing a newer Beta analyzer to run side by side.

Rationale:

- GUI features are expected to stabilize sooner than AI analysis.
- AI analysis prompts, models, indicators, and scoring logic will likely need frequent refinement.
- Beta analysis should be testable without destabilizing the default user experience.
- Historical Alpha/Beta results should make recommendation quality measurable over time.

Proposed behavior:

- Store analysis results with an explicit analyzer channel or version, such as `alpha`, `beta`, `beta-v1`, or a branch/SHA-derived version.
- Keep Alpha as the trusted default analysis channel used by normal users.
- Run Beta in shadow mode for all monitored stocks without changing default user-facing recommendations.
- Allow selected users or admins to opt into Beta analysis in the dashboard.
- Provide side-by-side Alpha/Beta comparison for admins or evaluators.
- Support API channel selection, for example:
  - `GET /api/suggestions?analysis_channel=alpha`
  - `GET /api/suggestions?analysis_channel=beta`
  - `GET /api/stocks/{ticker}/analysis?channel=beta`
- Track historical Beta outcomes so recommendations can be backtested over 7, 30, and 90 day windows.

Implementation notes:

- Add an `analyzer_version` or `analysis_channel` dimension to stored analysis results.
- Ensure existing dashboards and suggestion flows default to Alpha if no channel is specified.
- Add user preference support for Alpha/Beta opt-in.
- Consider an admin-only comparison view showing recommendation differences, confidence changes, risk changes, and later performance.
- Promotion path: Beta becomes Alpha only after passing deployment, smoke test, and recommendation-quality criteria.
- Feature branch deployments may publish Beta analyzers, but must not overwrite Alpha production analysis unless explicitly promoted.

## Deployment

### Create GitHub Actions CI/CD Pipeline for AWS Deployment

Status: Proposed

Requirement: Create a GitHub Actions workflow that deploys the application to AWS on every push to the `main` branch.

Rationale:

- Keeps AWS infrastructure and application deployments repeatable.
- Reduces manual deployment drift.
- Ensures tests and builds pass before production deployment.
- Aligns with the CDK-based infrastructure design.

Proposed behavior:

- Trigger on pushes to `main`.
- Run backend tests with `pytest`.
- Run frontend lint and production build.
- Install CDK dependencies.
- Authenticate to AWS using GitHub Actions OIDC, not long-lived AWS access keys.
- Run `cdk synth` before deployment.
- Run `cdk deploy --all --require-approval never` after validation succeeds.

Implementation notes:

- Add workflow under `.github/workflows/deploy.yml`.
- Configure an AWS IAM role trusted by the GitHub repository via OIDC.
- Store non-secret deployment configuration as repository variables where possible.
- Store required sensitive values in AWS Secrets Manager or GitHub Actions secrets only when OIDC is not enough.
- Consider separate future workflows for pull-request validation and production deployment.

### Automate Documentation and Backlog Updates on Main Deploy

Status: Proposed

Requirement: On every successful `main` deployment, automatically update project documentation with the latest architecture and feature descriptions, and move completed backlog items from `BACKLOG.md` into a shipped-features record.

Rationale:

- Keeps README and architecture docs aligned with the deployed system.
- Prevents finished backlog items from lingering as active work.
- Creates a lightweight release history without requiring manual bookkeeping after each deploy.
- Makes future coding-agent sessions start from current project context.

Proposed behavior:

- After deployment succeeds, run a documentation update step.
- Refresh README sections that describe architecture, deployed features, API surface, commands, and operational behavior.
- Refresh or generate architecture documentation from current CDK stacks and implemented source modules.
- Detect backlog items marked as complete/done.
- Move completed items out of `BACKLOG.md`.
- Append completed items to a shipped-features document, for example `SHIPPED.md`, with deployment date, summary, and relevant links.
- Commit documentation/backlog updates back to `main` or open an automated pull request if direct post-deploy commits are not desired.

Implementation notes:

- Define a clear done marker for backlog items, such as `Status: Done`.
- Prefer a deterministic script for moving completed backlog entries to shipped features.
- Keep generated docs reviewable and avoid overwriting hand-authored context blindly.
- Consider separating this into a follow-up workflow that runs only after the deploy workflow succeeds.
- Ensure any automated commit avoids retrigger loops, for example with `[skip ci]` or workflow path filters.

## Cost Optimization

### Refactor Database from PostgreSQL to DynamoDB

Status: Proposed

Requirement: Replace PostgreSQL-based persistence models with DynamoDB-oriented models to reduce baseline hosting cost and better fit the serverless AWS architecture.

Rationale:

- Aurora PostgreSQL Serverless v2 has a meaningful idle baseline cost.
- DynamoDB can reduce the always-on database cost for low user volume.
- The application can use denormalized, access-pattern-specific records instead of relational joins.
- Static demo leaderboard generation reduces the need for live sorted relational queries.

Proposed behavior:

- Replace PostgreSQL tables and SQL query paths with DynamoDB tables or single-table access patterns.
- Model records around actual access patterns: portfolios by owner, analysis by ticker/date/channel, stock metadata by ticker, demo/public views by generated static output.
- Move backend database access behind repository/service interfaces so API, collector, analyzer, and demo logic do not depend on SQL.
- Keep migrations or migration scripts to move existing PostgreSQL data into DynamoDB where needed.
- Remove Aurora resources from CDK after DynamoDB parity is validated.

Portfolio model requirements:

- Unify portfolio management for real users and test/demo users, including superhero demo accounts.
- Use one portfolio-management domain model for both real users and superhero/test accounts.
- Distinguish owner type explicitly, for example `owner_type = user | demo`, while keeping shared holding logic.
- Real user portfolios must be encoded/encrypted so holdings are not human-readable in direct DynamoDB queries.
- Real user portfolio encoding must preserve the current privacy goal: no plaintext portfolio holdings at rest.
- Demo/superhero portfolios may remain readable if needed for public static demo generation, but should not share private-user encryption keys or access paths.

Stock history storage requirements:

- Analyze whether detailed per-ticker historical OHLCV data should be stored long term at all.
- Design storage so ticker history is easy for the analysis app to extract in daily batches.
- Prefer compact analysis-ready formats over verbose item-per-day records when that reduces storage and read cost.
- Consider storing rolling analysis windows, summaries, or compressed blobs per ticker/date range instead of full detailed history for every ticker.
- Keep in mind that historical market data can be re-fetched at low cost when needed, potentially cheaper than storing detailed history for every monitored ticker indefinitely.
- Define retention rules for raw history, compressed history, derived indicators, and re-fetch-on-demand behavior.

Implementation notes:

- Candidate DynamoDB entities include stocks, stock metadata indexes, analysis results, portfolio records, user preferences, demo account state, demo transactions, and daily snapshots.
- Use DynamoDB conditional writes for uniqueness and conflict protection currently handled by SQL constraints.
- Replace SQL joins with denormalized records, materialized views, or batch-generated static assets.
- Keep financial quantities precise; avoid float drift in serialized DynamoDB values.
- Add tests around privacy encoding, portfolio parity for real/demo owners, analysis result lookup, and stock-history extraction.
- Treat the static demo leaderboard export as the public read model rather than querying DynamoDB live for leaderboard sorting.

### Generate Demo Leaderboard as Static Site

Status: Proposed

Requirement: The public demo leaderboard does not need to be real-time. Generate the leaderboard and related public demo pages once per day after demo trading completes, then serve them as static S3/CloudFront assets.

Rationale:

- Reduces public API read traffic and complexity.
- Makes demo pages cheap to host and easy to cache.
- Supports a future migration from Aurora PostgreSQL to DynamoDB by removing the need for live leaderboard sorting/querying.
- Shrinks unauthenticated API surface area.

Proposed behavior:

- After `DemoTradeExecutor` completes daily trading, run a static export step.
- Generate the ranked leaderboard with last-updated timestamp.
- Generate account detail payloads/pages for all demo accounts.
- Generate performance time-series data for charts.
- Generate paginated transaction-history JSON or static pages.
- Upload generated assets under the frontend S3 bucket, for example:
  - `/demo/index.html`
  - `/demo/assets/leaderboard.json`
  - `/demo/accounts/{account-name}/index.html`
  - `/demo/accounts/{account-name}/detail.json`
  - `/demo/accounts/{account-name}/performance.json`
  - `/demo/accounts/{account-name}/transactions-page-{n}.json`

Implementation notes:

- Keep the operational demo account state in the primary database initially.
- Treat the static files as a public read model derived from operational data.
- Update frontend demo routes to prefer static JSON/assets instead of live `/api/demo/*` calls.
- Consider retiring or restricting public `/api/demo/*` endpoints after the static flow is stable.
- If the database is later migrated to DynamoDB, keep the static export step as the canonical public demo read path.
