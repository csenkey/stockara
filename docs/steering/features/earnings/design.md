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

Session mapping uses the actual ordered exchange/price sessions supplied to the
event study, not weekday arithmetic. A before-open report maps to the first
session on or after its report date; an after-close report on a trading day maps
to the following session. Weekends, holidays, and closures therefore advance to
the next observed session. Unknown timing on a trading day retains both possible
boundaries and selects neither; on a non-trading day both interpretations
collapse to the next session. Missing future sessions are `insufficient`, never
a fabricated zero-return observation.

The reaction engine calculates `[-5,-1]`, `[-1,+1]`, `[0,+1]`, `[+1,+5]`, and
`[+1,+20]` session returns from positive `adjusted_close_price` values only.
SPY and the named sector benchmark must have prices on the exact same boundary
sessions; a benchmark gap removes only that adjusted return while retaining a
valid stock return. Abnormal volume compares the reaction session with exactly
20 preceding exchange sessions. Every window records its boundary dates,
missing inputs, and `complete`/`partial`/`missing` quality, while the event-level
quality also reflects timing ambiguity, truncated histories, and volume gaps.
The broad-market and sector benchmark ticker identities travel with the result.

The earnings collector publishes history coverage to
`earnings/history-coverage/as_of_date=YYYY-MM-DD/coverage.json` and, only for a
full-watchlist run, `earnings/history-coverage/latest.json`. Manifest tasks use a
`task_id=...` child path and never replace the global latest report. Each ticker
reports its distinct historical quarter count, estimate/actual field counts,
date bounds, coverage status, collection outcome, and incomplete reasons. Eight
distinct past quarters is the current completeness threshold. Provider budget,
quota, configuration, and request failures are explicit incomplete collection
outcomes even when previously stored history exists; they are also counted as
failed manifest tickers rather than successful work.

Historical backfill uses the daily collection manifest's persistent DynamoDB
task rows. Earnings work is split into deterministic, alphabetically complete
10-ticker chunks—smaller than the configured 20-call Alpha Vantage invocation
budget—and each chunk has an atomic lease, attempt count, output counts, and
retry timestamp. A quota-delayed chunk remains resumable while later earnings
chunks continue through the watchlist; it does not block the next ticker range.
Provider-specific quota reasons survive task completion so Alpha Vantage limits
receive the provider's longer retry delay instead of being flattened into a
generic partial failure. Writes remain idempotent at ticker/report date, making
replayed chunks safe.

Only a full-watchlist audit emits the universe-wide history coverage percentage;
targeted repair chunks cannot overwrite or distort that metric. CloudWatch
alarms notify when full-watchlist coverage falls below 90%, when the daily
coverage metric is absent, or when an actual provider rate/quota limit blocks a
ticker. Operator-configured request-budget exhaustion remains visible in task
and artifact outcomes but is not mislabeled as a provider quota incident.

The normalized historical contract keeps EPS and revenue consensus separately
from reported EPS and revenue, with independent surprise percentages. It also
keeps fiscal-period identity, report timing, a primary source URL plus the full
source URL set, provider observation ID, observation timestamp, and collection
timestamp. Guidance evidence is a source-backed, publication-timestamped metric
range/direction statement; estimate revisions retain the metric, fiscal period,
previous/current consensus, analyst count, source, and observation cutoff. These
lists remain empty when no configured provider supplies them—absence is measured
in the coverage artifact and is never replaced with inferred evidence. The
current free Alpha Vantage quarterly endpoint supplies fiscal identity and EPS
history but not revenue history; Finnhub fields are retained when its response
provides revenue estimates or actuals.

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
