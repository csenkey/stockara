# Earnings Charter Design

## Architecture

```text
Finnhub range calendar ---------+
Alpha Vantage range calendar ---+--> provider observations
yfinance targeted confirmation -+          |
company IR / filing evidence ---+          v
                                  canonical event reconciler
                                           |
                      +--------------------+-------------------+
                      |                    |                   |
                DynamoDB event       S3 calendar         conflict and
                and history rows     artifacts           coverage metrics
                      |                    |
             event-study builder     public Calendar page
                      |
              feature snapshots
                      |
            walk-forward predictor
                      |
             shadow score artifacts
                      |
        future seven-day opportunity view
```

## Calendar publication

The public earnings tab reads:

- `calendar/normalized/earnings/latest.json`

The earnings collector owns this artifact. Daily top-picks may retain a small
calendar summary for backwards compatibility, but that summary is not the
earnings calendar product and must not impose a UI limit. The dividend tab keeps
its existing publication source until its bounded collector output can be
aggregated safely; this charter must not regress dividend coverage incidentally.

Only a full-watchlist run may publish the global `latest` artifact. Targeted,
manifest, or partial runs publish dated/scoped audit artifacts and may update
per-ticker views without replacing global coverage.

## Canonical event identity and reconciliation

The durable event identity is ticker plus fiscal period where available, with
provider observations stored independently. Date-only identity remains a
compatibility key until the data model migration is complete.

Reconciliation rules:

1. Same ticker/date from two providers: `confirmed`.
2. One provider only: `single_source`; use `tentative` when the date is beyond a
   configurable confirmation horizon or lacks company confirmation.
3. Providers disagree inside the same plausible fiscal-event window:
   `conflicting`; retain all dates and select no silent winner.
4. Company announcement or filed release can confirm the canonical date and
   records the evidence URL and timestamp.
5. An updated date supersedes an earlier tentative observation without deleting
   the audit trail.

The compatibility reconciler uses a configurable 14-day plausible-event window.
Provider dates farther apart are treated as independent quarters unless fiscal
identity or later company evidence links them. A conflicting canonical event is
represented in the legacy date-indexed artifact by one row per candidate date;
the rows share a canonical ID, candidate-date set, and complete observation IDs.

Conflicts with at least one candidate date from today through seven days ahead
receive a targeted yfinance query. Calls are deduplicated by ticker and capped by
an explicit per-run ticker budget. Matching yfinance observations are recorded as
candidate support, but do not silently select a winner while another provider date
remains. Conflicts outside the horizon receive no per-ticker confirmation call.

Finnhub range collection reserves a separate request for the first seven days,
then queries the remaining configured horizon independently. This prevents the
provider's bounded response from filling with later events and excluding the
near-term rows required for two-source verification. The two requests publish
separate provider-health diagnostics, while their observations retain the common
`finnhub` provenance and are deduplicated before reconciliation.

## Historical evidence and reactions

Historical normalized rows contain estimates and actuals independently so
pre-event snapshots can freeze what was knowable at prediction time. Event
reactions use the first eligible session boundary based on `before_market`,
`after_market`, or explicit time. Returns are adjusted against SPY and a mapped
sector benchmark when data exists.

Feature snapshots are immutable and keyed by strategy, ticker, event, prediction
timestamp, provider snapshot hash, and schema version. Report/transcript content
published after the prediction cutoff is excluded by construction.

## Predictive outputs

The research engine produces three calibrated distributions:

- probability and size of EPS/revenue surprise;
- probability of positive abnormal return;
- expected abnormal return and adverse-move distribution over configured
  holding windows.

The opportunity classification consumes expected value after costs and evidence
quality. It does not map “predicted beat” directly to `TRADE`.

## Cost-conscious provider options

- Default: Finnhub plus the Alpha Vantage global calendar, with targeted
  yfinance/company confirmation only for near-term watchlist events.
- Upgrade: a licensed fundamentals/estimates provider when free-source conflict,
  latency, or quota metrics exceed agreed thresholds.
- TipRanks may be used only through a licensed integration; scraping its public
  UI is not a production data source.

## Rollout

1. Calendar correctness and dedicated artifacts.
2. Provider reconciliation and conflict display.
3. Historical coverage and event studies.
4. Offline walk-forward research.
5. Production shadow scoring.
6. Human research view.
7. Separate promotion decision for any downstream recommendation influence.
