import { useEffect, useMemo, useState } from "react";

interface SignalSource {
  provider: string;
  observed_at: string;
}

interface AiReview {
  status: string;
  model: string;
  approved: boolean;
  rationale: string;
  concerns: string[];
  rejection_category?: string | null;
  what_would_make_approvable?: string | null;
}

interface TopPick {
  rank: number;
  ticker: string;
  company_name: string;
  sector: string;
  recommendation: "BUY" | "HOLD" | "SELL";
  risk_level: "LOW" | "MEDIUM" | "HIGH";
  confidence_score: number;
  catalyst: string;
  expected_timeframe: string;
  rationale: string;
  invalidation_criteria: string;
  supporting_evidence: string[];
  source_traceability: SignalSource[];
}

interface SellAlert {
  rank: number;
  ticker: string;
  company_name: string;
  sector: string;
  severity: string;
  risk_level: "LOW" | "MEDIUM" | "HIGH";
  confidence_score: number;
  negative_catalyst: string;
  rationale: string;
  supporting_evidence: string[];
  source_traceability: SignalSource[];
}

interface ReviewRejection {
  ticker: string;
  company_name: string;
  sector?: string;
  recommendation: "BUY" | "SELL";
  risk_level: "LOW" | "MEDIUM" | "HIGH";
  confidence_score: number;
  opportunity_score: number;
  negative_score: number;
  catalyst: string;
  analyst_reasoning: string;
  invalidation_criteria: string;
  supporting_evidence: string[];
  ai_review: AiReview;
}

interface DataQuality {
  coverage_status?: string;
  active_ticker_count?: number;
  eligible_ticker_count?: number;
  excluded_ticker_count?: number;
  exclusion_reason_counts?: Record<string, number>;
}

interface TopPicksPayload {
  publication_date: string;
  generated_at: string;
  top_picks: TopPick[];
  sell_alerts: SellAlert[];
  review_rejections?: ReviewRejection[];
  candidate_count: number;
  analyzed_count: number;
  data_quality?: DataQuality;
  data_warnings: string[];
}

const TOP_PICKS_URL =
  import.meta.env.VITE_TOP_PICKS_URL || "/top-picks/latest.json";

function badgeClass(value: string) {
  switch (value) {
    case "LOW":
      return "bg-emerald-50 text-emerald-700 border-emerald-200";
    case "MEDIUM":
      return "bg-amber-50 text-amber-700 border-amber-200";
    case "HIGH":
    case "critical":
      return "bg-red-50 text-red-700 border-red-200";
    default:
      return "bg-slate-50 text-slate-700 border-slate-200";
  }
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

interface DashboardProps {
  onNavigate?: (view: "top-picks" | "data-health") => void;
}

export default function Dashboard({ onNavigate }: DashboardProps) {
  const [payload, setPayload] = useState<TopPicksPayload | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function loadTopPicks() {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(TOP_PICKS_URL, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      setPayload(await response.json());
    } catch {
      setError("Daily top picks have not been published yet.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadTopPicks();
  }, []);

  const generatedLabel = useMemo(() => {
    if (!payload?.generated_at) return "Waiting for first publication";
    return formatDate(payload.generated_at);
  }, [payload]);
  const reviewRejections = payload?.review_rejections ?? [];

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <section className="border-b border-slate-800 bg-slate-900">
        <div className="mx-auto flex max-w-7xl flex-col gap-5 px-5 py-6 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal">
              Stockara Daily Top Picks
            </h1>
            <p className="mt-2 max-w-3xl text-sm text-slate-300">
              Catalyst-ranked opportunities and urgent risk alerts generated
              from static Phase 1 artifacts.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {onNavigate && (
              <button
                onClick={() => onNavigate("data-health")}
                className="inline-flex h-10 items-center justify-center rounded border border-slate-700 bg-slate-900 px-3 text-sm font-medium text-slate-200 hover:bg-slate-800"
              >
                Data Freshness
              </button>
            )}
            <button
              onClick={loadTopPicks}
              className="inline-flex h-10 items-center justify-center rounded border border-slate-700 bg-slate-800 px-3 text-sm font-medium text-slate-100 hover:bg-slate-700"
            >
              Refresh
            </button>
          </div>
        </div>
      </section>

      <section className="mx-auto grid max-w-7xl gap-4 px-5 py-5 md:grid-cols-4">
        <Metric marker="time" label="Generated" value={generatedLabel} />
        <Metric
          marker="picks"
          label="Top Picks"
          value={String(payload?.top_picks.length ?? 0)}
        />
        <Metric
          marker="risk"
          label="Sell Alerts"
          value={String(payload?.sell_alerts.length ?? 0)}
        />
        <Metric
          marker="scan"
          label="Analyzed"
          value={`${payload?.analyzed_count ?? 0}/${payload?.candidate_count ?? 0}`}
        />
      </section>

      <div className="mx-auto max-w-7xl px-5 pb-10">
        {loading && (
          <div className="border border-slate-800 bg-slate-900 p-6 text-sm text-slate-300">
            Loading the latest static publication...
          </div>
        )}

        {!loading && error && (
          <div className="border border-amber-700 bg-amber-950 p-5 text-sm text-amber-100">
            {error}
          </div>
        )}

        {!loading && payload && (
          <div className="space-y-8">
            {payload.data_warnings.length > 0 && (
              <div className="border border-amber-700 bg-amber-950 p-4 text-sm text-amber-100">
                <div className="mb-2 flex items-center gap-2 font-medium">
                  Data Warnings
                </div>
                <ul className="space-y-1">
                  {payload.data_warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
                <FreshnessSummary dataQuality={payload.data_quality} />
              </div>
            )}

            {reviewRejections.length > 0 && (
              <section>
                <h2 className="mb-3 text-lg font-semibold">
                  Withheld AI Recommendations
                </h2>
                <div className="space-y-3">
                  {reviewRejections.map((row) => (
                    <ReviewRejectionRow key={row.ticker} row={row} />
                  ))}
                </div>
              </section>
            )}

            <section>
              <h2 className="mb-3 text-lg font-semibold">Top Opportunities</h2>
              {payload.top_picks.length === 0 ? (
                <div className="border border-slate-800 bg-slate-900 p-5 text-sm text-slate-300">
                  No BUY recommendations passed the review gate for this publication.
                </div>
              ) : (
                <div className="grid gap-4 lg:grid-cols-2">
                  {payload.top_picks.map((pick) => (
                    <PickRow key={pick.ticker} pick={pick} />
                  ))}
                </div>
              )}
            </section>

            <section>
              <h2 className="mb-3 text-lg font-semibold">Urgent Sell Alerts</h2>
              {payload.sell_alerts.length === 0 ? (
                <div className="border border-slate-800 bg-slate-900 p-5 text-sm text-slate-300">
                  No urgent sell alerts crossed the configured threshold.
                </div>
              ) : (
                <div className="grid gap-4 lg:grid-cols-2">
                  {payload.sell_alerts.map((alert) => (
                    <SellAlertRow key={alert.ticker} alert={alert} />
                  ))}
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </main>
  );
}

function FreshnessSummary({ dataQuality }: { dataQuality?: DataQuality }) {
  const reasonCounts = dataQuality?.exclusion_reason_counts ?? {};
  const entries = Object.entries(reasonCounts).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return null;

  return (
    <div className="mt-3 border-t border-amber-800 pt-3">
      <div className="mb-2 text-xs font-semibold uppercase text-amber-200">
        Freshness exclusion reasons
      </div>
      <div className="flex flex-wrap gap-2">
        {entries.map(([reason, count]) => (
          <span
            key={reason}
            className="border border-amber-700 bg-amber-900 px-2 py-1 text-xs text-amber-50"
          >
            {reason}: {count}
          </span>
        ))}
      </div>
    </div>
  );
}

function ReviewRejectionRow({ row }: { row: ReviewRejection }) {
  return (
    <article className="border border-slate-700 bg-slate-900 p-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="text-xs uppercase text-slate-500">
            Analyst proposed {row.recommendation}
          </div>
          <h3 className="mt-1 text-lg font-semibold">{row.ticker}</h3>
          <p className="mt-1 text-sm text-slate-400">
            {row.company_name}
            {row.sector ? ` · ${row.sector}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <span className={`border px-2 py-1 ${badgeClass(row.risk_level)}`}>
            {row.risk_level}
          </span>
          <span className="border border-slate-700 bg-slate-800 px-2 py-1 text-slate-200">
            confidence {row.confidence_score}%
          </span>
          <span className="border border-slate-700 bg-slate-800 px-2 py-1 text-slate-200">
            opp {row.opportunity_score}
          </span>
          <span className="border border-slate-700 bg-slate-800 px-2 py-1 text-slate-200">
            neg {row.negative_score}
          </span>
        </div>
      </div>

      <div className="mt-4 grid gap-4 text-sm lg:grid-cols-2">
        <div>
          <div className="text-xs font-semibold uppercase text-slate-500">
            Analyst thesis
          </div>
          <p className="mt-1 font-medium text-slate-100">{row.catalyst}</p>
          <p className="mt-2 leading-6 text-slate-300">{row.analyst_reasoning}</p>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase text-slate-500">
            Reviewer rationale
          </div>
          {row.ai_review.rejection_category && (
            <p className="mt-1 text-xs uppercase text-amber-300">
              {row.ai_review.rejection_category}
            </p>
          )}
          <p className="mt-2 leading-6 text-slate-300">{row.ai_review.rationale}</p>
          {row.ai_review.what_would_make_approvable && (
            <p className="mt-2 text-slate-400">
              Needed: {row.ai_review.what_would_make_approvable}
            </p>
          )}
        </div>
      </div>

      {row.ai_review.concerns.length > 0 && (
        <ul className="mt-4 space-y-1 border-t border-slate-800 pt-3 text-sm text-slate-400">
          {row.ai_review.concerns.map((concern) => (
            <li key={concern}>{concern}</li>
          ))}
        </ul>
      )}
    </article>
  );
}

function Metric({
  marker,
  label,
  value,
}: {
  marker: string;
  label: string;
  value: string;
}) {
  return (
    <div className="border border-slate-800 bg-slate-900 p-4">
      <div className="mb-2 flex items-center gap-2 text-xs uppercase text-slate-400">
        <span className="font-semibold text-slate-500">{marker}</span>
        {label}
      </div>
      <div className="text-base font-semibold text-slate-100">{value}</div>
    </div>
  );
}

function PickRow({ pick }: { pick: TopPick }) {
  return (
    <article className="border border-slate-800 bg-slate-900 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-xs text-slate-400">#{pick.rank}</div>
          <h3 className="mt-1 text-xl font-semibold">{pick.ticker}</h3>
          <p className="mt-1 text-sm text-slate-400">
            {pick.company_name} · {pick.sector}
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <span className="border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700">
            {pick.recommendation}
          </span>
          <span className={`border px-2 py-1 text-xs ${badgeClass(pick.risk_level)}`}>
            {pick.risk_level}
          </span>
        </div>
      </div>
      <p className="mt-4 text-sm font-medium text-slate-100">{pick.catalyst}</p>
      <p className="mt-2 text-sm leading-6 text-slate-300">{pick.rationale}</p>
      <div className="mt-4 grid gap-3 text-sm md:grid-cols-2">
        <Fact label="Confidence" value={`${pick.confidence_score}%`} />
        <Fact label="Timeframe" value={pick.expected_timeframe} />
      </div>
      <Evidence items={pick.supporting_evidence} />
    </article>
  );
}

function SellAlertRow({ alert }: { alert: SellAlert }) {
  return (
    <article className="border border-red-900 bg-red-950 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-xs text-red-200">#{alert.rank}</div>
          <h3 className="mt-1 text-xl font-semibold">{alert.ticker}</h3>
          <p className="mt-1 text-sm text-red-200">
            {alert.company_name} · {alert.sector}
          </p>
        </div>
        <span className={`border px-2 py-1 text-xs ${badgeClass(alert.severity)}`}>
          {alert.severity}
        </span>
      </div>
      <p className="mt-4 text-sm font-medium text-red-50">
        {alert.negative_catalyst}
      </p>
      <p className="mt-2 text-sm leading-6 text-red-100">{alert.rationale}</p>
      <div className="mt-4 grid gap-3 text-sm md:grid-cols-2">
        <Fact label="Confidence" value={`${alert.confidence_score}%`} />
        <Fact label="Risk" value={alert.risk_level} />
      </div>
      <Evidence items={alert.supporting_evidence} />
    </article>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase text-slate-500">{label}</div>
      <div className="mt-1 text-slate-200">{value}</div>
    </div>
  );
}

function Evidence({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="mt-4 space-y-2 border-t border-slate-800 pt-4 text-sm text-slate-300">
      {items.slice(0, 3).map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}
