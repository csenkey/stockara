# Requirements Document: Backtest Support With Shadowed Portfolios

## Introduction

Backtest Support With Shadowed Portfolios validates whether Stockara's daily recommendation system improves portfolio outcomes versus simpler alternatives. The feature replays historical trading days from a fixed start date, simulates portfolios that act on Stockara decisions, creates shadow portfolios when Stockara would change holdings, and compares results against hold-only and ETF-only baselines.

This feature is explicitly not the real-user portfolio tracking system. It uses S3-backed artifacts for simulated backtest data, run outputs, metrics, and investigation results. Data safety, authentication, GDPR, and user portfolio encryption are out of scope for this feature unless the implementation later reuses production portfolio services.

The product goal is decision-system reliability: determine whether versioned Stockara analysis logic, BUY/HOLD/SELL decisions, AI review gates, risk classifications, and ticker filters add measurable value after commission costs and realistic trade constraints.

## Glossary

- **Backtest_Run**: A deterministic simulation over historical trading dates using a fixed configuration, random seed, data snapshot, and strategy set.
- **Backtest_Portfolio**: A simulated portfolio with cash, holdings, transactions, snapshots, risk profile, and strategy rules.
- **Main_Portfolio**: A simulated portfolio that follows Stockara's chosen strategy actions.
- **Global_Hold_Shadow**: A comparison portfolio that starts from the same initial holdings as a main portfolio and never follows Stockara trades.
- **Decision_Shadow**: A bounded comparison portfolio forked from the pre-trade state whenever Stockara changes the main portfolio, then evaluated over fixed future windows.
- **Policy_Shadow**: A comparison portfolio that uses the same recommendation stream but a different decision policy, such as conservative-only or review-ignored.
- **ETF_Baseline**: A comparison portfolio that invests in one or more ETFs, such as SPY, VOO, QQQ, VTI, or VT, and follows a documented baseline rule.
- **Point_In_Time_Data**: Historical data that would have been known at the simulated decision timestamp, excluding future prices, future news, revised events unknown at the time, or hindsight metadata.
- **Analysis_Strategy**: A versioned snapshot of Stockara's recommendation-generation business logic, including non-AI preselection, predicates, filters, scoring weights, prompt templates, prompt evidence blocks, AI models, review gates, suppression rules, fallback behavior, and publication criteria.
- **Portfolio_Policy**: A risk-tolerance policy that converts recommendations into simulated trades, such as conservative, balanced, or aggressive.
- **Commission_Drag**: The loss in return caused by transaction fees.

## Requirements

### Requirement 1: Historical Market Data Foundation

**User Story:** As a system evaluator, I want historical market data for stocks and ETFs, so that backtests can replay realistic portfolio values across multiple years.

#### Acceptance Criteria

1. THE system SHALL support loading at least 5 years of daily OHLCV data for active watchlist stocks when provider coverage allows.
2. THE system SHALL store or load adjusted price data suitable for return calculations that account for splits and dividends where available.
3. THE system SHALL distinguish stock instruments from ETF instruments in metadata and simulation inputs.
4. THE system SHALL support ETF price history for at least one broad-market baseline instrument, initially SPY or VOO.
5. WHEN historical data for a ticker is missing for a simulated trading date, THE system SHALL mark the valuation as incomplete and avoid silently carrying forward stale prices beyond a configured tolerance.
6. WHEN a ticker lacks enough historical data for a decision date, THE system SHALL exclude that ticker from decision-grade analysis for that date and record the exclusion reason.
7. THE system SHALL preserve provider provenance, collection timestamp, and adjusted/unadjusted price semantics for historical price records.

### Requirement 2: Historical Evidence Context

**User Story:** As a system evaluator, I want the analyzer to use only evidence that was available at the historical decision date, so that backtest results do not include hindsight leakage.

#### Acceptance Criteria

1. THE backtest analyzer SHALL only use OHLCV rows with trading dates at or before the simulated decision date.
2. THE backtest analyzer SHALL only use news items whose publication timestamp is at or before the simulated decision timestamp.
3. THE backtest analyzer SHALL only use earnings and dividend events that were known or reasonably discoverable at the simulated decision timestamp.
4. IF historical news is unavailable for a date range, THE system SHALL mark the run as reduced-evidence instead of pretending full evidence was available.
5. THE system SHALL record which evidence categories were available for every simulated decision date.
6. THE system SHALL cache or persist historical recommendation outputs by ticker, analysis date, model version, strategy inputs, and evidence coverage to avoid repeated AI spend.
7. THE system SHALL make any known point-in-time limitations visible in the backtest run summary.

### Requirement 3: Seeded Portfolio Generation

**User Story:** As a system evaluator, I want a diverse set of starting portfolios, so that Stockara can be tested across different concentration and risk situations.

#### Acceptance Criteria

1. THE system SHALL generate exactly 100 simulated starting portfolios by default.
2. EACH starting portfolio SHALL begin with exactly USD 10,000.00 total capital before commission costs.
3. THE generated portfolios SHALL include concentration variants with 1, 3, 5, and 10 starting tickers unless the configured universe cannot support them.
4. THE generated portfolios SHALL support conservative, balanced, and aggressive portfolio policies.
5. THE generated portfolios SHALL support deterministic generation from a stored random seed.
6. THE generated portfolios SHALL use only active watchlist stocks with sufficient price history on the start date.
7. THE system SHALL record the initial allocation method, selected tickers, rejected tickers, and seed used for each portfolio.

### Requirement 4: Versioned Analysis Strategies

**User Story:** As a system evaluator, I want every meaningful analyzer change captured as a versioned analysis strategy, so that Stockara can compare recommendation logic snapshots and promote or reject changes based on measured backtest results.

#### Acceptance Criteria

1. THE system SHALL define an `Analysis_Strategy` manifest for each finished analyzer-logic snapshot.
2. THE manifest SHALL include a stable strategy ID, parent strategy ID when applicable, status, description, creation date, git commit, and owner or author metadata.
3. THE manifest SHALL record the non-AI preselection flow, predicates, filters, scoring weights, candidate limits, and suppression rules.
4. THE manifest SHALL record recommendation AI model, review AI model, prompt template versions, prompt evidence blocks, structured output schema version, and model parameter settings.
5. THE manifest SHALL record required evidence categories, optional evidence categories, actually used data sources, excluded data sources, and missing-evidence behavior.
6. THE system SHALL store analysis strategy manifests as durable S3 artifacts and copy the exact manifest into every backtest run that uses it.
7. THE system SHALL cache AI recommendation and review outputs by analysis strategy ID, ticker, date, prompt version, model, evidence hash, and output schema version.
8. THE system SHALL support comparing at least two analysis strategies against the same data snapshot, portfolio set, execution rule, commission rate, and portfolio policies.
9. THE system SHALL preserve candidate scores, rejected candidates, prompts, model responses, review decisions, publication decisions, and suppression reasons per analysis strategy.
10. THE system SHALL support promotion metadata for analysis strategies, including baseline, candidate, promoted, rejected, and archived states.

### Requirement 5: Portfolio Policies and Trade Decisions

**User Story:** As a system evaluator, I want portfolio policies to transform recommendations into trades, so that conservative and aggressive investor behavior can be compared separately from Stockara analyzer changes.

#### Acceptance Criteria

1. THE conservative portfolio policy SHALL buy only blue-chip stocks with BUY recommendations, LOW risk, and successful stronger AI review.
2. THE conservative portfolio policy SHALL avoid promising-but-review-failed recommendations.
3. THE balanced portfolio policy SHALL allow blue-chip and mid-cap stocks with BUY recommendations and LOW or MEDIUM risk, preferring successful stronger AI review.
4. THE aggressive portfolio policy SHALL be allowed to consider startup or high-growth tickers and promising recommendations that failed stronger AI review, while recording that the review failed.
5. EACH portfolio policy SHALL define maximum single-position exposure, maximum daily turnover, cash reserve behavior, and replacement thresholds.
6. WHEN replacing an existing holding, THE portfolio policy SHALL require the candidate buy to exceed the existing holding by a configurable evidence or score margin.
7. HOLD recommendations SHALL produce no transaction unless another portfolio-level rule requires rebalancing.
8. SELL recommendations SHALL liquidate all shares for that ticker when the active portfolio policy accepts the sell signal.
9. THE system SHALL record the reason for every accepted or rejected trade decision.

### Requirement 6: Commission-Aware Portfolio Simulation

**User Story:** As a system evaluator, I want simulated trades to include fees and whole-share constraints, so that results reflect realistic portfolio friction.

#### Acceptance Criteria

1. THE simulator SHALL charge a configurable commission rate on every buy and sell transaction, defaulting to 1%.
2. BUY transactions SHALL purchase only whole shares.
3. BUY transactions SHALL not cause cash balance to become negative after price and commission.
4. SELL transactions SHALL liquidate the configured quantity and credit proceeds minus commission.
5. THE simulator SHALL record account, date, ticker, action, quantity, execution price, gross value, commission, cash after, and rationale for each transaction.
6. THE simulator SHALL take a daily snapshot for every active main portfolio, including cash, holdings value, total value, unrealized gain/loss, realized gain/loss, and commission paid to date.
7. THE simulator SHALL value portfolios using documented execution timing, initially next available close or next open after the recommendation.
8. THE simulator SHALL fail or mark the date incomplete when required execution prices are unavailable.

### Requirement 7: Shadow Portfolio Evaluation

**User Story:** As a system evaluator, I want shadow portfolios for Stockara decisions, so that underperforming decisions can be isolated and investigated.

#### Acceptance Criteria

1. THE system SHALL maintain one global hold shadow for each generated starting portfolio.
2. WHEN Stockara changes a main portfolio through a material buy, sell, replacement, or rebalance, THE system SHALL create a decision shadow from the pre-trade state.
3. A decision shadow SHALL ignore the specific triggering action while retaining the same pre-decision holdings and cash state.
4. Decision shadows SHALL be evaluated over fixed windows, initially 7, 30, 90, 180, and 365 calendar days when data is available.
5. THE system SHALL store main-vs-shadow value deltas for each evaluation window.
6. THE system SHALL support policy shadows that replay the same recommendation stream with different portfolio policies.
7. THE system SHALL limit decision shadow growth through configurable materiality thresholds, maximum lifetime, and result compaction.
8. THE system SHALL mark decision shadows as incomplete when future valuation data is insufficient for a requested window.
9. THE system SHALL classify underperforming Stockara decisions by reason where possible, including early sell, bad replacement, commission drag, missing evidence, review-gate behavior, market shock, and data quality limitation.

### Requirement 8: Baselines and Benchmarks

**User Story:** As a system evaluator, I want Stockara results compared against simple baselines, so that performance is measured against realistic alternatives.

#### Acceptance Criteria

1. THE system SHALL compare every main portfolio against its global hold shadow.
2. THE system SHALL compare every main portfolio against at least one ETF baseline, initially SPY or VOO.
3. THE system SHALL support optional QQQ, VTI, VT, and sector ETF baselines when ETF metadata and price history are available.
4. THE ETF baseline SHALL use the same starting capital and commission assumptions as the main portfolio unless explicitly configured otherwise.
5. THE system SHALL report total return, annualized return, max drawdown, volatility, trade count, turnover, commission drag, and final value for each main, shadow, and baseline portfolio.
6. THE system SHALL aggregate results by analysis strategy, portfolio policy, starting concentration, sector concentration, company-size mix, and evidence coverage.
7. THE system SHALL identify cases where Stockara underperformed both the hold shadow and ETF baseline.

### Requirement 9: Backtest Reporting and Investigation Queue

**User Story:** As a system evaluator, I want clear reports and an investigation queue, so that weak recommendation patterns can be improved.

#### Acceptance Criteria

1. THE system SHALL emit machine-readable run outputs, initially JSON and CSV artifacts.
2. THE system SHALL include run configuration, random seed, data snapshot identifiers, date range, analysis strategy IDs, portfolio policy IDs, and evidence coverage in every run summary.
3. THE system SHALL rank the worst underperforming decisions by main-vs-shadow delta.
4. THE system SHALL rank decisions where review-passed recommendations underperformed review-failed alternatives.
5. THE system SHALL rank decisions where promising-but-review-failed recommendations outperformed conservative alternatives.
6. THE system SHALL include per-decision evidence summaries for investigation.
7. THE system SHALL support repeatable local CLI execution before any frontend dashboard is built.
8. THE system SHALL make known limitations explicit, including survivorship bias, missing delisted tickers, missing historical news, missing event history, and provider coverage gaps.
9. THE system SHALL emit pairwise analysis-strategy comparison reports showing changes in candidate selection, AI cost, recommendation mix, review pass rate, publication rate, portfolio outcome, drawdown, commission drag, and shadow underperformance.
10. THE system SHALL identify analysis strategies that regress against the current baseline and preserve enough artifacts to explain whether the regression came from preselection, prompt evidence, model choice, review gate behavior, or portfolio-policy interaction.

### Requirement 10: Testing and Correctness

**User Story:** As a developer, I want property and unit tests for simulation invariants, so that backtest conclusions are not corrupted by accounting bugs.

#### Acceptance Criteria

1. THE simulator SHALL have property tests proving cash never becomes negative after valid trade execution.
2. THE simulator SHALL have property tests proving commission is exactly the configured percentage of gross trade value.
3. THE simulator SHALL have property tests proving sell transactions cannot leave fractional or negative holdings.
4. THE simulator SHALL have tests proving global hold shadows do not trade after initialization.
5. THE simulator SHALL have tests proving decision shadows fork from the pre-trade state.
6. THE simulator SHALL have tests proving point-in-time data windows exclude future rows and articles.
7. THE simulator SHALL have tests proving deterministic portfolio generation from a seed.
8. THE simulator SHALL have tests proving metrics are calculated from snapshots consistently.
9. THE system SHALL have tests proving analysis strategy manifests are immutable within a run and included in all relevant output artifacts.
10. THE system SHALL have tests proving AI cache keys include analysis strategy identity, model, prompt version, evidence hash, and schema version.

### Requirement 11: Scope Boundaries

**User Story:** As a product owner, I want this feature kept separate from real-user portfolio management, so that validation work can move quickly without expanding compliance or security scope.

#### Acceptance Criteria

1. THE implementation SHALL not require authenticated user portfolio APIs.
2. THE implementation SHALL not require encryption of simulated portfolio data.
3. THE implementation SHALL not expose simulated backtest portfolios as real user holdings.
4. THE implementation SHALL not claim investment advice or production trading readiness.
5. THE implementation SHALL document when a run is experimental, reduced-evidence, or affected by incomplete historical data.
6. THE implementation SHALL preserve a future migration path from validated analysis strategies and portfolio policies into real-user portfolio suggestion features.
