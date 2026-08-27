import { useEffect, useMemo, useState } from "react";

type LoadState<T> = {
  data: T | null;
  error: string;
  loading: boolean;
};

interface CoverageGate {
  name: string;
  passed: boolean;
  observed_value: number | string;
  required_value: number | string;
  unit: string;
  message: string;
}

interface DataHealthPayload {
  generated_at: string;
  manifest_date: string;
  manifest_key: string;
  active_ticker_count: number;
  task_counts: Record<string, number>;
  coverage_ratio: number | string;
  coverage_gates: CoverageGate[];
  tasks_by_type: Record<string, Record<string, number>>;
  failed_tasks: TaskRow[];
  recent_tasks: TaskRow[];
}

interface TaskRow {
  task_id: string;
  task_type: string;
  status: string;
  ticker_count: number;
  ticker_range_start?: string;
  ticker_range_end?: string;
  failure_reason?: string | null;
  attempt_count?: number;
  updated_at?: string;
}

interface NewsPayload {
  generated_at: string;
  lookback_days: number;
  last_run: {
    status?: string;
    articles_fetched?: number;
    articles_processed?: number;
    duplicates_skipped?: number;
    sources_available?: number;
    sources_total?: number;
    failed_sources?: string[];
  };
  recent_article_count: number;
  ticker_count_with_news: number;
  by_ticker: NewsTicker[];
}

interface NewsTicker {
  ticker: string;
  company_name: string;
  article_count: number;
  articles: NewsArticle[];
}

interface NewsArticle {
  title: string;
  source: string;
  published_at?: string;
  summary: string;
  sentiment: string;
}

interface PriceGapsPayload {
  generated_at: string;
  scan_start_date: string;
  scan_end_date: string;
  active_ticker_count: number;
  gap_ticker_count: number;
  gap_count: number;
  missing_trading_days: number;
  by_ticker: GapTicker[];
}

interface GapTicker {
  ticker: string;
  gap_count: number;
  missing_trading_days: number;
  gaps: PriceGap[];
}

interface PriceGap {
  start_date?: string;
  end_date?: string;
  trading_day_count: number;
  task_id: string;
  status: string;
}

interface ReadinessPayload {
  generated_at: string;
  run_date: string;
  publication_status: string;
  overall_status: string;
  summary: {
    active_ticker_count?: number;
    eligible_ticker_count?: number;
    excluded_ticker_count?: number;
    candidate_count?: number;
    analyzed_count?: number;
    readiness_item_count?: number;
    blocked_item_count?: number;
    degraded_item_count?: number;
    data_type_counts?: Record<string, number>;
    reason_counts?: Record<string, number>;
    repair_mode_counts?: Record<string, number>;
  };
  warnings: string[];
  items: ReadinessItem[];
}

interface ReadinessItem {
  ticker?: string | null;
  data_type: string;
  status: string;
  reason: string;
  repair_mode: string;
  required_for: string;
  provider?: string | null;
  latest_observed_at?: string | null;
  last_attempted_at?: string | null;
}

const DATA_HEALTH_URL =
  import.meta.env.VITE_DATA_HEALTH_URL || "/data-health/latest.json";
const DATA_READINESS_URL =
  import.meta.env.VITE_DATA_READINESS_URL || "/data-readiness/latest.json";
const NEWS_HEALTH_URL = import.meta.env.VITE_NEWS_HEALTH_URL || "/news/latest.json";
const PRICE_GAPS_URL =
  import.meta.env.VITE_PRICE_GAPS_URL || "/price-gaps/latest.json";

interface DataHealthProps {
  onNavigate?: (view: "top-picks" | "calendar" | "data-health") => void;
}

export default function DataHealth({ onNavigate }: DataHealthProps) {
  const [health, setHealth] = useState<LoadState<DataHealthPayload>>({
    data: null,
    error: "",
    loading: true,
  });
  const [readiness, setReadiness] = useState<LoadState<ReadinessPayload>>({
    data: null,
    error: "",
    loading: true,
  });
  const [news, setNews] = useState<LoadState<NewsPayload>>({
    data: null,
    error: "",
    loading: true,
  });
  const [gaps, setGaps] = useState<LoadState<PriceGapsPayload>>({
    data: null,
    error: "",
    loading: true,
  });

  async function loadAll() {
    await Promise.all([
      loadJson(DATA_HEALTH_URL, setHealth),
      loadJson(DATA_READINESS_URL, setReadiness),
      loadJson(NEWS_HEALTH_URL, setNews),
      loadJson(PRICE_GAPS_URL, setGaps),
    ]);
  }

  useEffect(() => {
    loadAll();
  }, []);

  const failedGates = useMemo(
    () => health.data?.coverage_gates.filter((gate) => !gate.passed) ?? [],
    [health.data],
  );

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <section className="border-b border-slate-800 bg-slate-900">
        <div className="mx-auto flex max-w-7xl flex-col gap-5 px-5 py-6 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal">
              Stockara Data Freshness
            </h1>
            <p className="mt-2 max-w-3xl text-sm text-slate-300">
              Static collection health, news coverage, and price gap artifacts.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {onNavigate && (
              <>
                <button
                  onClick={() => onNavigate("top-picks")}
                  className="inline-flex h-10 items-center justify-center rounded border border-slate-700 bg-slate-900 px-3 text-sm font-medium text-slate-200 hover:bg-slate-800"
                >
                  Top Picks
                </button>
                <button
                  onClick={() => onNavigate("calendar")}
                  className="inline-flex h-10 items-center justify-center rounded border border-slate-700 bg-slate-900 px-3 text-sm font-medium text-slate-200 hover:bg-slate-800"
                >
                  Calendars
                </button>
              </>
            )}
            <button
              onClick={loadAll}
              className="inline-flex h-10 items-center justify-center rounded border border-slate-700 bg-slate-800 px-3 text-sm font-medium text-slate-100 hover:bg-slate-700"
            >
              Refresh
            </button>
          </div>
        </div>
      </section>

      <div className="mx-auto max-w-7xl space-y-6 px-5 py-5">
        <section className="grid gap-4 md:grid-cols-4">
          <Metric label="Manifest" value={health.data?.manifest_date ?? "-"} />
          <Metric
            label="Active tickers"
            value={String(health.data?.active_ticker_count ?? "-")}
          />
          <Metric
            label="Readiness issues"
            value={String(readiness.data?.summary.readiness_item_count ?? "-")}
          />
          <Metric
            label="News articles"
            value={String(news.data?.recent_article_count ?? "-")}
          />
        </section>

        <ArtifactState label="Data readiness" state={readiness} />
        {readiness.data && <ReadinessSection payload={readiness.data} />}

        <ArtifactState label="Data health" state={health} />
        {health.data && (
          <>
            <section className="border border-slate-800 bg-slate-900 p-5">
              <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                <div>
                  <h2 className="text-lg font-semibold">Collection Coverage Targets</h2>
                  <p className="mt-1 text-sm text-slate-400">
                    Generated {formatDate(health.data.generated_at)}
                  </p>
                </div>
                <span
                  className={`border px-2 py-1 text-xs font-semibold ${
                    failedGates.length === 0
                      ? "border-emerald-700 bg-emerald-950 text-emerald-100"
                      : "border-amber-700 bg-amber-950 text-amber-100"
                  }`}
                >
                  {failedGates.length === 0
                    ? "all on target"
                    : `${failedGates.length} below target`}
                </span>
              </div>
              <div className="mt-4 grid gap-3 lg:grid-cols-3">
                {health.data.coverage_gates.map((gate) => (
                  <GateCard key={gate.name} gate={gate} />
                ))}
              </div>
            </section>

            <section className="border border-slate-800 bg-slate-900 p-5">
              <h2 className="text-lg font-semibold">Manifest Tasks</h2>
              <div className="mt-4 grid gap-3 md:grid-cols-2 lg:grid-cols-4">
                {Object.entries(health.data.tasks_by_type).map(([type, counts]) => (
                  <TaskTypeCard key={type} type={type} counts={counts} />
                ))}
              </div>
              <TaskTable rows={health.data.failed_tasks} />
            </section>
          </>
        )}

        <ArtifactState label="News" state={news} />
        {news.data && <NewsSection payload={news.data} />}

        <ArtifactState label="Price gaps" state={gaps} />
        {gaps.data && <PriceGapsSection payload={gaps.data} />}
      </div>
    </main>
  );
}

async function loadJson<T>(
  url: string,
  setState: (value: LoadState<T>) => void,
) {
  setState({ data: null, error: "", loading: true });
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    setState({ data: await response.json(), error: "", loading: false });
  } catch (loadError) {
    const detail = loadError instanceof Error ? loadError.message : "unknown error";
    setState({
      data: null,
      error: `Static artifact fetch failed (${detail}): ${url}`,
      loading: false,
    });
  }
}

function ArtifactState<T>({ label, state }: { label: string; state: LoadState<T> }) {
  if (state.loading) {
    return (
      <div className="border border-slate-800 bg-slate-900 p-4 text-sm text-slate-300">
        Loading {label.toLowerCase()}...
      </div>
    );
  }
  if (!state.error) return null;
  return (
    <div className="border border-amber-700 bg-amber-950 p-4 text-sm text-amber-100">
      {state.error}
    </div>
  );
}

function GateCard({ gate }: { gate: CoverageGate }) {
  return (
    <article className="border border-slate-800 bg-slate-950 p-4">
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-sm font-semibold">{gate.name}</h3>
        <span
          className={`border px-2 py-1 text-xs ${
            gate.passed
              ? "border-emerald-700 bg-emerald-950 text-emerald-100"
              : "border-amber-700 bg-amber-950 text-amber-100"
          }`}
        >
          {gate.passed ? "on target" : "below target"}
        </span>
      </div>
      <p className="mt-3 text-sm text-slate-300">{gate.message}</p>
      <div className="mt-3 text-sm text-slate-400">
        observed {formatNumber(gate.observed_value)} / required{" "}
        {formatNumber(gate.required_value)} {gate.unit}
      </div>
    </article>
  );
}

function TaskTypeCard({
  type,
  counts,
}: {
  type: string;
  counts: Record<string, number>;
}) {
  return (
    <article className="border border-slate-800 bg-slate-950 p-4">
      <h3 className="text-sm font-semibold uppercase text-slate-300">{type}</h3>
      <div className="mt-3 grid grid-cols-2 gap-2 text-sm text-slate-400">
        <span>total {counts.total ?? 0}</span>
        <span>succeeded {counts.succeeded ?? 0}</span>
        <span>pending {counts.pending ?? 0}</span>
        <span>failed {counts.failed ?? 0}</span>
      </div>
    </article>
  );
}

function TaskTable({ rows }: { rows: TaskRow[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="mt-5 overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <thead className="border-b border-slate-800 text-xs uppercase text-slate-500">
          <tr>
            <th className="py-2 pr-4">Task</th>
            <th className="py-2 pr-4">Type</th>
            <th className="py-2 pr-4">Tickers</th>
            <th className="py-2 pr-4">Reason</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800 text-slate-300">
          {rows.slice(0, 20).map((row) => (
            <tr key={row.task_id}>
              <td className="py-2 pr-4 font-mono text-xs">{row.task_id}</td>
              <td className="py-2 pr-4">{row.task_type}</td>
              <td className="py-2 pr-4">
                {row.ticker_range_start}-{row.ticker_range_end}
              </td>
              <td className="py-2 pr-4">{row.failure_reason ?? "failed"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReadinessSection({ payload }: { payload: ReadinessPayload }) {
  const issueRows = payload.items.slice(0, 80);
  const repairModes = Object.entries(payload.summary.repair_mode_counts ?? {});
  return (
    <section className="border border-slate-800 bg-slate-900 p-5">
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-lg font-semibold">Daily Readiness</h2>
          <p className="mt-1 text-sm text-slate-400">
            {payload.run_date}, updated {formatDate(payload.generated_at)}
          </p>
        </div>
        <span
          className={`border px-2 py-1 text-xs font-semibold ${readinessTone(
            payload.overall_status,
          )}`}
        >
          {payload.overall_status}
        </span>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <Metric
          label="Eligible"
          value={`${payload.summary.eligible_ticker_count ?? 0}/${payload.summary.active_ticker_count ?? 0}`}
        />
        <Metric
          label="Blocked"
          value={String(payload.summary.blocked_item_count ?? 0)}
        />
        <Metric
          label="Degraded"
          value={String(payload.summary.degraded_item_count ?? 0)}
        />
        <Metric
          label="AI analyzed"
          value={String(payload.summary.analyzed_count ?? 0)}
        />
      </div>

      {repairModes.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {repairModes.map(([mode, count]) => (
            <span
              key={mode}
              className="border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-300"
            >
              {mode}: {count}
            </span>
          ))}
        </div>
      )}

      {issueRows.length === 0 ? (
        <div className="mt-4 border border-slate-800 bg-slate-950 p-4 text-sm text-slate-300">
          No readiness issues were reported in the latest daily artifact.
        </div>
      ) : (
        <div className="mt-5 overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-slate-800 text-xs uppercase text-slate-500">
              <tr>
                <th className="py-2 pr-4">Ticker</th>
                <th className="py-2 pr-4">Data</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4">Reason</th>
                <th className="py-2 pr-4">Repair</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 text-slate-300">
              {issueRows.map((row, index) => (
                <tr key={`${row.ticker ?? "global"}-${row.reason}-${index}`}>
                  <td className="py-2 pr-4 font-semibold text-slate-100">
                    {row.ticker ?? "Global"}
                  </td>
                  <td className="py-2 pr-4">{row.data_type}</td>
                  <td className="py-2 pr-4">
                    <span
                      className={`border px-2 py-1 text-xs ${readinessTone(
                        row.status,
                      )}`}
                    >
                      {row.status}
                    </span>
                  </td>
                  <td className="py-2 pr-4">{row.reason}</td>
                  <td className="py-2 pr-4 font-mono text-xs">{row.repair_mode}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function NewsSection({ payload }: { payload: NewsPayload }) {
  return (
    <section className="border border-slate-800 bg-slate-900 p-5">
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-lg font-semibold">News Coverage</h2>
          <p className="mt-1 text-sm text-slate-400">
            Last {payload.lookback_days} days, updated {formatDate(payload.generated_at)}
          </p>
        </div>
        <div className="text-sm text-slate-300">
          {payload.last_run.articles_processed ?? 0} processed from{" "}
          {payload.last_run.articles_fetched ?? 0} fetched
        </div>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <Metric
          label="Sources"
          value={`${payload.last_run.sources_available ?? 0}/${payload.last_run.sources_total ?? 2}`}
        />
        <Metric
          label="Tickers with news"
          value={String(payload.ticker_count_with_news)}
        />
        <Metric
          label="Duplicates"
          value={String(payload.last_run.duplicates_skipped ?? 0)}
        />
      </div>
      <div className="mt-5 space-y-3">
        {payload.by_ticker.slice(0, 25).map((row) => (
          <article key={row.ticker} className="border border-slate-800 bg-slate-950 p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="font-semibold">{row.ticker}</h3>
                <p className="mt-1 text-sm text-slate-400">{row.company_name}</p>
              </div>
              <span className="border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200">
                {row.article_count} articles
              </span>
            </div>
            <ul className="mt-3 space-y-2 text-sm text-slate-300">
              {row.articles.slice(0, 3).map((article) => (
                <li key={`${row.ticker}-${article.source}-${article.title}`}>
                  <span className="font-medium text-slate-100">{article.title}</span>
                  <span className="text-slate-500"> · {article.source}</span>
                </li>
              ))}
            </ul>
          </article>
        ))}
      </div>
    </section>
  );
}

function PriceGapsSection({ payload }: { payload: PriceGapsPayload }) {
  return (
    <section className="border border-slate-800 bg-slate-900 p-5">
      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
        <div>
          <h2 className="text-lg font-semibold">Ticker Price Gaps</h2>
          <p className="mt-1 text-sm text-slate-400">
            {payload.scan_start_date} to {payload.scan_end_date}, updated{" "}
            {formatDate(payload.generated_at)}
          </p>
        </div>
        <div className="text-sm text-slate-300">
          {payload.missing_trading_days} missing trading days
        </div>
      </div>
      {payload.by_ticker.length === 0 ? (
        <div className="mt-4 border border-slate-800 bg-slate-950 p-4 text-sm text-slate-300">
          No price gaps were detected in the latest scan.
        </div>
      ) : (
        <div className="mt-5 overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-slate-800 text-xs uppercase text-slate-500">
              <tr>
                <th className="py-2 pr-4">Ticker</th>
                <th className="py-2 pr-4">Missing days</th>
                <th className="py-2 pr-4">Ranges</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 text-slate-300">
              {payload.by_ticker.slice(0, 50).map((row) => (
                <tr key={row.ticker}>
                  <td className="py-2 pr-4 font-semibold text-slate-100">
                    {row.ticker}
                  </td>
                  <td className="py-2 pr-4">{row.missing_trading_days}</td>
                  <td className="py-2 pr-4">
                    {row.gaps
                      .slice(0, 3)
                      .map((gap) => `${gap.start_date} to ${gap.end_date}`)
                      .join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-slate-800 bg-slate-900 p-4">
      <div className="text-xs uppercase text-slate-500">{label}</div>
      <div className="mt-2 text-base font-semibold text-slate-100">{value}</div>
    </div>
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatNumber(value: number | string) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return numeric.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function readinessTone(status: string) {
  if (status === "ready") return "border-emerald-700 bg-emerald-950 text-emerald-100";
  if (status === "degraded") return "border-amber-700 bg-amber-950 text-amber-100";
  return "border-red-800 bg-red-950 text-red-100";
}
