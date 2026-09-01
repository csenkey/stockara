import { useEffect, useMemo, useState } from "react";

import {
  calendarUrlFor,
  earningsConfidencePresentation,
} from "../services/calendarArtifacts.mjs";

interface EarningsEvent {
  ticker: string;
  company_name?: string | null;
  event_date: string;
  eps_estimate?: number | string | null;
  reported_eps?: number | string | null;
  surprise_percent?: number | string | null;
  time_of_day?: string | null;
  is_upcoming?: boolean;
  provider?: string | null;
  source_url?: string | null;
  reconciliation_status?: string | null;
  date_confidence?: string | null;
  candidate_dates?: string[];
}

interface ReconciliationSummary {
  canonical_event_count?: number;
  confirmed_event_count?: number;
  company_confirmed_event_count?: number;
  single_source_event_count?: number;
  conflicting_candidate_count?: number;
  conflict_group_count?: number;
  unreconciled_event_count?: number;
}

interface DividendEvent {
  ticker: string;
  company_name?: string | null;
  ex_dividend_date: string;
  pay_date?: string | null;
  dividend_amount?: number | string | null;
  dividend_yield?: number | string | null;
  is_upcoming?: boolean;
  provider?: string | null;
  source_url?: string | null;
}

interface CalendarPayload {
  collection_date: string;
  generated_at: string;
  upcoming_earnings?: EarningsEvent[];
  upcoming_dividends?: DividendEvent[];
  data_warnings?: string[];
  reconciliation_summary?: ReconciliationSummary;
}

interface NormalizedCalendarPayload<T> {
  collection_date: string;
  generated_at: string;
  collection_status?: string;
  warnings?: string[];
  events?: T[];
  reconciliation_summary?: ReconciliationSummary;
}

interface PublishedCalendarPayload {
  publication_date: string;
  generated_at: string;
  upcoming_dividends?: DividendEvent[];
  data_warnings?: string[];
}

interface CalendarProps {
  onNavigate?: (view: "top-picks" | "calendar" | "data-health") => void;
}

type CalendarTab = "earnings" | "dividends";

const TOP_PICKS_URL =
  import.meta.env.VITE_TOP_PICKS_URL || "/top-picks/latest.json";

async function fetchCalendar<T>(url: string): Promise<NormalizedCalendarPayload<T>> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return (await response.json()) as NormalizedCalendarPayload<T>;
}

async function fetchPublication(url: string): Promise<PublishedCalendarPayload> {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return (await response.json()) as PublishedCalendarPayload;
}

export default function Calendar({ onNavigate }: CalendarProps) {
  const [payload, setPayload] = useState<CalendarPayload | null>(null);
  const [activeTab, setActiveTab] = useState<CalendarTab>("earnings");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function loadCalendar() {
    setLoading(true);
    setError("");
    try {
      const [earningsResult, dividendsResult] = await Promise.allSettled([
        fetchCalendar<EarningsEvent>(calendarUrlFor(TOP_PICKS_URL, "earnings")),
        fetchPublication(TOP_PICKS_URL),
      ]);
      if (earningsResult.status === "rejected" && dividendsResult.status === "rejected") {
        throw new Error(
          `${String(earningsResult.reason)}; ${String(dividendsResult.reason)}`,
        );
      }
      setPayload((previous) => {
        const earnings = earningsResult.status === "fulfilled" ? earningsResult.value : null;
        const dividendsPublication =
          dividendsResult.status === "fulfilled" ? dividendsResult.value : null;
        const warnings = [
          ...(earnings?.warnings ?? []).map((warning) => `Earnings: ${warning}`),
          ...(dividendsPublication?.data_warnings ?? [])
            .filter((warning) => warning.toLowerCase().includes("dividend"))
            .map((warning) => `Dividends: ${warning}`),
        ];
        if (earningsResult.status === "rejected") {
          warnings.push("Earnings calendar refresh failed; retaining the previous in-page result.");
        }
        if (dividendsResult.status === "rejected") {
          warnings.push("Dividend publication refresh failed; retaining the previous in-page result.");
        }
        if (earnings?.collection_status === "degraded") {
          warnings.push("Earnings calendar collection is degraded.");
        }
        const collectionDates = [
          earnings?.collection_date,
          dividendsPublication?.publication_date,
          previous?.collection_date,
        ].filter((value): value is string => Boolean(value));
        const generatedTimes = [
          earnings?.generated_at,
          dividendsPublication?.generated_at,
          previous?.generated_at,
        ].filter((value): value is string => Boolean(value));
        return {
          collection_date: collectionDates.sort().slice(-1)[0] ?? "",
          generated_at: generatedTimes.sort().slice(-1)[0] ?? "",
          upcoming_earnings:
            earnings?.collection_status === "degraded" && previous?.upcoming_earnings
              ? previous.upcoming_earnings
              : earnings?.events?.filter((event) => event.is_upcoming !== false) ??
                previous?.upcoming_earnings ??
                [],
          upcoming_dividends:
            dividendsPublication?.upcoming_dividends ?? previous?.upcoming_dividends ?? [],
          data_warnings: warnings,
          reconciliation_summary:
            earnings?.reconciliation_summary ?? previous?.reconciliation_summary,
        };
      });
    } catch (loadError) {
      const detail = loadError instanceof Error ? loadError.message : "unknown error";
      setError(`Calendar fetch failed (${detail}).`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadCalendar();
  }, []);

  const earnings = useMemo(
    () => sortByDate(payload?.upcoming_earnings ?? [], (row) => row.event_date),
    [payload],
  );
  const dividends = useMemo(
    () => sortByDate(payload?.upcoming_dividends ?? [], (row) => row.ex_dividend_date),
    [payload],
  );
  const calendarWarnings = payload?.data_warnings ?? [];

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <section className="border-b border-slate-800 bg-slate-900">
        <div className="mx-auto flex max-w-7xl flex-col gap-5 px-5 py-6 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal">
              Stockara Earnings and Dividend Calendars
            </h1>
            <p className="mt-2 max-w-3xl text-sm text-slate-300">
              Upcoming earnings and ex-dividend events from the latest static Phase 1 publication.
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
                  onClick={() => onNavigate("data-health")}
                  className="inline-flex h-10 items-center justify-center rounded border border-slate-700 bg-slate-900 px-3 text-sm font-medium text-slate-200 hover:bg-slate-800"
                >
                  Data Freshness
                </button>
              </>
            )}
            <button
              onClick={loadCalendar}
              className="inline-flex h-10 items-center justify-center rounded border border-slate-700 bg-slate-800 px-3 text-sm font-medium text-slate-100 hover:bg-slate-700"
            >
              Refresh
            </button>
          </div>
        </div>
      </section>

      <div className="mx-auto max-w-7xl space-y-5 px-5 py-5">
        <section className="grid gap-4 md:grid-cols-5">
          <Metric label="Generated" value={payload?.generated_at ? formatDate(payload.generated_at) : "-"} />
          <Metric label="Earnings" value={String(earnings.length)} />
          <Metric label="Dividends" value={String(dividends.length)} />
          <Metric label="Collection" value={payload?.collection_date ?? "-"} />
          <Metric
            label="Date conflicts"
            value={String(payload?.reconciliation_summary?.conflict_group_count ?? 0)}
          />
        </section>

        {loading && (
          <div className="border border-slate-800 bg-slate-900 p-6 text-sm text-slate-300">
            Loading the latest calendar publication...
          </div>
        )}

        {!loading && error && (
          <div className="border border-amber-700 bg-amber-950 p-5 text-sm text-amber-100">
            {error}
          </div>
        )}

        {!loading && payload && (
          <>
            <section className="border border-slate-800 bg-slate-900 p-4">
              <div className="flex flex-wrap gap-2">
                <TabButton
                  active={activeTab === "earnings"}
                  onClick={() => setActiveTab("earnings")}
                  label={`Earnings (${earnings.length})`}
                />
                <TabButton
                  active={activeTab === "dividends"}
                  onClick={() => setActiveTab("dividends")}
                  label={`Dividends (${dividends.length})`}
                />
              </div>
              {calendarWarnings.length > 0 && (
                <div className="mt-4 border border-amber-700 bg-amber-950 p-3 text-sm text-amber-100">
                  {calendarWarnings.map((warning) => (
                    <div key={warning}>{warning}</div>
                  ))}
                </div>
              )}
            </section>

            {activeTab === "earnings" ? (
              <EarningsTable rows={earnings} />
            ) : (
              <DividendTable rows={dividends} />
            )}
          </>
        )}
      </div>
    </main>
  );
}

function EarningsTable({ rows }: { rows: EarningsEvent[] }) {
  if (rows.length === 0) {
    return <EmptyCalendar label="No upcoming earnings events are present in the latest publication." />;
  }
  return (
    <section className="overflow-hidden border border-slate-800 bg-slate-900">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-800 text-sm">
          <thead className="bg-slate-950 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-3">Date</th>
              <th className="px-4 py-3">Ticker</th>
              <th className="px-4 py-3">Company</th>
              <th className="px-4 py-3">Timing</th>
              <th className="px-4 py-3">EPS Estimate</th>
              <th className="px-4 py-3">Date confidence</th>
              <th className="px-4 py-3">Provider</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {rows.map((row) => (
              <tr key={`${row.ticker}-${row.event_date}`}>
                <td className="whitespace-nowrap px-4 py-3 text-slate-100">{formatShortDate(row.event_date)}</td>
                <td className="whitespace-nowrap px-4 py-3 font-semibold text-slate-100">{row.ticker}</td>
                <td className="min-w-52 px-4 py-3 text-slate-300">{row.company_name || "-"}</td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-300">{formatTiming(row.time_of_day)}</td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-300">{formatNullable(row.eps_estimate)}</td>
                <td className="whitespace-nowrap px-4 py-3">
                  <ConfidenceBadge row={row} />
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-400">
                  <ProviderLink provider={row.provider} url={row.source_url} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ConfidenceBadge({ row }: { row: EarningsEvent }) {
  const presentation = earningsConfidencePresentation(row.reconciliation_status);
  const toneClass = {
    positive: "border-emerald-700 bg-emerald-950 text-emerald-200",
    warning: "border-amber-600 bg-amber-950 text-amber-100",
    neutral: "border-sky-800 bg-sky-950 text-sky-200",
    muted: "border-slate-700 bg-slate-900 text-slate-400",
  }[presentation.tone];
  const candidates = row.candidate_dates?.join(" or ");
  return (
    <span
      className={`inline-flex rounded border px-2 py-1 text-xs font-medium ${toneClass}`}
      title={candidates ? `Candidate dates: ${candidates}` : undefined}
    >
      {presentation.label}
    </span>
  );
}

function DividendTable({ rows }: { rows: DividendEvent[] }) {
  if (rows.length === 0) {
    return <EmptyCalendar label="No upcoming ex-dividend events are present in the latest publication." />;
  }
  return (
    <section className="overflow-hidden border border-slate-800 bg-slate-900">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-800 text-sm">
          <thead className="bg-slate-950 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-4 py-3">Ex-Date</th>
              <th className="px-4 py-3">Ticker</th>
              <th className="px-4 py-3">Company</th>
              <th className="px-4 py-3">Pay Date</th>
              <th className="px-4 py-3">Amount</th>
              <th className="px-4 py-3">Yield</th>
              <th className="px-4 py-3">Provider</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {rows.map((row) => (
              <tr key={`${row.ticker}-${row.ex_dividend_date}`}>
                <td className="whitespace-nowrap px-4 py-3 text-slate-100">{formatShortDate(row.ex_dividend_date)}</td>
                <td className="whitespace-nowrap px-4 py-3 font-semibold text-slate-100">{row.ticker}</td>
                <td className="min-w-52 px-4 py-3 text-slate-300">{row.company_name || "-"}</td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-300">{row.pay_date ? formatShortDate(row.pay_date) : "-"}</td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-300">{formatNullable(row.dividend_amount)}</td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-300">{formatPercent(row.dividend_yield)}</td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-400">
                  <ProviderLink provider={row.provider} url={row.source_url} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function EmptyCalendar({ label }: { label: string }) {
  return (
    <section className="border border-amber-700 bg-amber-950 p-5 text-sm text-amber-100">
      {label} This is a collection gap worth investigating, especially if the data freshness page shows completed calendar tasks.
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-slate-800 bg-slate-900 p-4">
      <div className="mb-2 text-xs uppercase text-slate-500">{label}</div>
      <div className="text-base font-semibold text-slate-100">{value}</div>
    </div>
  );
}

function TabButton({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`h-9 rounded border px-3 text-sm font-medium ${
        active
          ? "border-sky-500 bg-sky-950 text-sky-100"
          : "border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800"
      }`}
    >
      {label}
    </button>
  );
}

function ProviderLink({ provider, url }: { provider?: string | null; url?: string | null }) {
  if (!url) return <span>{provider || "-"}</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer" className="underline underline-offset-4">
      {provider || "Source"}
    </a>
  );
}

function sortByDate<T>(rows: T[], getDate: (row: T) => string) {
  return [...rows].sort((a, b) => getDate(a).localeCompare(getDate(b)));
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatShortDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

function formatTiming(value?: string | null) {
  if (value === "before_market") return "Before market";
  if (value === "after_market") return "After market";
  return "-";
}

function formatNullable(value?: number | string | null) {
  return value === undefined || value === null || value === "" ? "-" : String(value);
}

function formatPercent(value?: number | string | null) {
  if (value === undefined || value === null || value === "") return "-";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(2)}%` : String(value);
}
