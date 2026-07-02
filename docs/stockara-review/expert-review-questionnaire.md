# Stockara Expert Review Questionnaire

Purpose: collect structured feedback from stock-market experts on Stockara's data inputs, data gates, scoring logic, AI analysis/review workflow, and web UI output.

Recommended response scale for rating questions:

- 1 = Strongly disagree / not sufficient.
- 2 = Somewhat disagree.
- 3 = Neutral or unsure.
- 4 = Somewhat agree.
- 5 = Strongly agree / sufficient.

## Reviewer Background

- Name
- Email
- Role or area of expertise
- Years of market/investment experience
- Main asset classes or sectors reviewed

## Data Collection

Rating questions:

- The current data categories are sufficient for a daily catalyst scanner.
- Daily pre-publication news collection is sufficient for the current Stockara workflow.
- Earnings, dividends, SEC filings, analyst actions, sector context, and macro proxies provide enough event context for first-stage review.

Free-text questions:

- What missing sources or evidence types should Stockara consider essential?
- Which current sources or evidence types are least useful or potentially noisy?
- What collection schedule would you recommend for price, news, earnings, dividends, filings, analyst actions, sector context, and macro context?

## Data Gates

Rating questions:

- A maximum 3-day age for latest price data is appropriate.
- At least 30 calendar days and 20 price rows is enough for a decision-grade near-term scan.
- Stale news should be shown as a warning rather than automatically suppressing every stock.

Free-text questions:

- Should different stock types have different freshness or history requirements?
- What minimum history and freshness requirements would you recommend?
- Under what conditions should stale or missing news suppress publication?

## Candidate Scoring

Rating questions:

- Separating opportunity score and negative score is the right high-level scoring structure.
- Sell alerts should use different thresholds than BUY opportunities.
- Sector-relative strength should affect candidate scoring.
- Company size should affect score interpretation.

Free-text questions:

- Which signals should carry more weight?
- Which signals should carry less weight?
- What scoring thresholds would you recommend for BUY candidates?
- What scoring thresholds would you recommend for SELL alerts?
- What examples would you use to calibrate scoring quality?

## AI Analysis

Rating questions:

- The AI analyst receives the right evidence for a useful near-term assessment.
- The AI should be required to mention upcoming earnings or dividends when relevant.
- The requested outputs are clear: recommendation, risk, confidence, catalyst, timeframe, reasoning, and invalidation criteria.

Free-text questions:

- Should the AI prompt require valuation, liquidity, balance-sheet, volatility, or other context?
- What evidence should the AI never ignore?
- What would make the AI reasoning more useful to a stock expert?
- What mistakes would you expect the AI analyst to make?

## AI Review

Rating questions:

- The second AI review step is necessary before publishing BUY or SELL recommendations.
- The review step should be stricter than the first AI analyst step.
- Rejected AI ideas should remain visible to expert reviewers.

Free-text questions:

- What rejection categories would be most useful for expert audit?
- Should confidence adjustments be constrained differently?
- Should rejected ideas be hidden from non-expert users?
- What evidence standard should a recommendation meet before publication?

## Web UI Output

Rating questions:

- The Top Picks card shows enough information to judge a recommendation quickly.
- The Sell Alert card shows enough negative evidence and urgency.
- Data warnings are prominent enough.
- The Withheld AI Recommendations section is useful for expert review.
- The static charts are sufficient for first-pass review.

Free-text questions:

- What additional chart indicators should be shown?
- What information is missing from Top Picks?
- What information is missing from Sell Alerts?
- What would make the GUI easier for expert review sessions?

## Overall Assessment

Rating questions:

- Stockara's current workflow is a reasonable foundation for expert-reviewed daily stock recommendations.
- Stockara's current warnings are understandable and actionable.
- Stockara's current publication controls reduce the risk of unsupported recommendations.

Free-text questions:

- What would make Stockara's published recommendations more trustworthy?
- What would make Stockara's warnings more actionable?
- Which recommendation examples should be reviewed manually to calibrate scoring and review strictness?
- What minimum evidence standard should a public BUY or SELL recommendation meet?
- Any other comments or concerns?

