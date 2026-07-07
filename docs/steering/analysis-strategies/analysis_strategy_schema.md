# Analysis Strategy Schema

This is the planning schema for versioned Stockara analyzer business logic. The implementation may use Pydantic models, but the fields below define the durable manifest shape expected by backtests and S3 artifacts.

```yaml
analysis_strategy:
  id: analysis_strategy_YYYY_MM_DD_short_name_vN
  status: candidate
  parent: analysis_strategy_previous_id
  git_commit: unknown
  created_at: YYYY-MM-DD
  description: Short explanation of what changed.
  owner: istvan

preselection:
  flow_version: preselection_pipeline_v1
  predicates:
    - active_watchlist_only
    - price_data_fresh
    - min_30d_ohlcv
  filters:
    - exclude_stale_or_under_supported_tickers
  scoring:
    version: candidate_score_v1
    weights:
      momentum: 0.0
      volume_spike: 0.0
      news_sentiment: 0.0
      earnings_proximity: 0.0
      dividend_signal: 0.0
  limits:
    max_candidates_per_day: 100
    max_ai_analyzed_per_day: 30

evidence:
  required:
    - ohlcv_30d
  optional:
    - news_7d
    - earnings_context
    - dividend_context
  used_data_sources:
    ohlcv:
      - stooq
      - yahoo
    news:
      - newsapi
      - finnhub
    earnings:
      - alpha_vantage
      - yahoo
    dividends:
      - yahoo
      - alpha_vantage
  excluded_data_sources: []
  missing_evidence_behavior: suppress_if_required_missing

recommendation_ai:
  enabled: true
  model: model-name
  prompt_template: recommendation_template_v1
  prompt_inputs:
    - ohlcv_30d
    - technical_indicators
    - missing_evidence_summary
  output_schema: recommendation_output_v1
  parameters:
    temperature: 0

review_ai:
  enabled: true
  model: model-name
  prompt_template: review_template_v1
  review_gate:
    require_for_public_buy_sell: true
    reject_if_missing_catalyst: true
    reject_if_stale_price_data: true
  parameters:
    temperature: 0

publication:
  suppress_stale_tickers: true
  suppress_under_supported_tickers: true
  suppress_review_rejected_buy_sell: true
  expose_partial_coverage: true

fallbacks:
  missing_news: continue_with_missing_evidence_warning
  ai_recommendation_failure: suppress_ticker
  ai_review_failure: suppress_public_buy_sell

cost_limits:
  max_total_run_cost_usd: 50
  max_ai_recommendation_calls_per_day: 30
  max_ai_review_calls_per_day: 8
```

## Cache Identity

AI recommendation and review caches must include:

- analysis strategy ID
- ticker
- analysis date
- model ID
- prompt template version
- prompt input/evidence hash
- structured output schema version
- relevant model parameters

Changing any of those fields must produce a different cache key.

