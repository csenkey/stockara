# Stockara Engineering Rules

## Commands

Backend:

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Frontend:

```bash
cd frontend
npm run lint
npm run build
npm run dev
```

Infrastructure:

```bash
cd infrastructure
python -m pytest tests/ -v
cdk deploy --all -c deploymentStage=prod
```

Prefer targeted tests while iterating, then run the relevant full suite before handing off. External provider calls should be mocked in tests; do not require real OpenAI, yfinance, Cognito, KMS, NewsAPI, Finnhub, or Alpha Vantage credentials for unit tests.

## How We Work

- During early Phase 1 development, commit implementation work directly on `main`; Istvan is the only developer and branch-scoped environments are intentionally disabled for now.
- Every commit on `main` is expected to trigger an AWS deployment through CI/CD.
- `main` deploys the active `prod` stage. Do not create or deploy feature/codex branch-scoped AWS stages unless this policy is explicitly re-enabled.
- Before committing, run all relevant local tests and builds. A commit is not ready for deployment until local tests pass.
- If an AWS deployment fails, fetch the failure details automatically from GitHub Actions logs or related AWS logs, diagnose the issue, correct it, amend the feature-branch commit, and retry deployment.
- Continue the fix, amend, and retry loop until deployment is green.
- After each successful AWS deployment, run a smoke test against the deployed environment to verify the core application is working.
- Treat a green deploy plus passing smoke test as the minimum bar before opening or updating a pull request for review.
- When branch-based development is re-enabled later, squash merge completed feature branches into `main`, then delete the feature branch and its branch-scoped AWS resources.

## Security And Data Handling

- Never store plaintext real user portfolio data in the database.
- Use AES-256-GCM/KMS-backed encryption semantics for portfolio data.
- Keep secrets out of source code; expect environment variables, AWS Secrets Manager, or CDK-provided configuration.
- Preserve HTTPS/JWT/Cognito assumptions for authenticated APIs.
- Use least-privilege IAM in CDK changes.
- Public demo endpoints may expose simulated account data only; do not leak user portfolio or auth-protected data there.
- Backtest simulated portfolios are not real user portfolios; their canonical artifacts belong in S3 under the steering-defined backtest paths.

## Implementation Guidance

- Follow existing module boundaries and naming before adding new abstractions.
- Keep API schemas in Pydantic models and frontend types aligned with response shapes.
- For financial calculations, preserve Decimal-style precision on the backend where existing code uses it.
- Keep property tests for demo-account and backtest invariants when changing trading/account logic.
- Use structured logs for batch and API operations, especially partial failures.
- Keep public demo routes outside authenticated frontend layout and backend auth middleware.
- Prefer CDK table/index changes for database schema access patterns; do not silently mutate data shapes in application code.
- Treat `docs/steering/` as the canonical planning source. `.kiro/specs/` is legacy reference material only.

## Stable Release Rules

- Create stable versions as annotated, immutable tags named `stockara-X.Y`.
- Before creating a stable tag, run the relevant local tests/builds and confirm the current `main` deployment is green.
- Push stable tags to the repository so GitHub Actions can validate and deploy them.
- Use the `Deploy Stable Stockara Version` workflow to deploy or roll back to a stable tag; it always targets `prod` and runs the normal deployment verification.
- Never force-move, delete, or reuse a stable tag. A correction requires a new version tag.
- Record stable release and rollback decisions in the commit/tag message or the relevant steering note; do not rely on chat history alone.
