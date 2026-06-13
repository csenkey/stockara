# Backlog

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
