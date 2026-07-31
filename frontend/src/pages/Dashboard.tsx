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

interface PriceCandle {
  date: string;
  open: number | string;
  high: number | string;
  low: number | string;
  close: number | string;
  volume: number;
}

interface ChartPoint {
  date: string;
  value: number | string;
}

interface TrendLine {
  start_date: string;
  start_value: number | string;
  end_date: string;
  end_value: number | string;
  slope_per_session: number | string;
}

interface PriceChart {
  period_start: string;
  period_end: string;
  currency?: string | null;
  candles: PriceCandle[];
  sma_20: ChartPoint[];
  trend_line?: TrendLine | null;
  support?: number | string | null;
  resistance?: number | string | null;
}

interface RelatedNewsArticle {
  title: string;
  source: string;
  published_at?: string | null;
  summary: string;
  sentiment?: string;
  url?: string | null;
}

interface UpcomingTickerEvent {
  event_type: string;
  event_date: string;
  title: string;
  provider?: string | null;
  source_url?: string | null;
  details?: Record<string, number | string | null>;
}

interface CompanyInfo {
  description?: string;
  top_products?: string[];
  revenue_segments?: string[];
  industry?: string;
  exchange?: string;
  currency?: string;
  country?: string;
  website?: string;
  founded_year?: number | string;
  headquarters?: string;
  ipo_year?: number | string;
  market_cap?: number | string;
  competitive_position?: string;
  key_static_risks?: string[];
  metadata_source?: string;
  metadata_source_url?: string;
  metadata_as_of?: string;
  logo_url?: string;
  logo_icon_url?: string;
  logo_source?: string;
  logo_source_url?: string;
  logo_checked_at?: string;
  brief_history?: string;
}

interface NeededEvidence {
  gap_type: string;
  title: string;
  status: string;
  collection_plan: string;
  source_candidates: string[];
}

type PublicationTier =
  | "decision_grade"
  | "reduced_confidence"
  | "fallback_preview"
  | "blocked";

interface ConfidenceAdjustment {
  reason: string;
  adjustment: number;
  detail?: string;
}

interface RecommendationQuality {
  analysis_method?: string;
  publication_tier?: PublicationTier | string;
  missing_evidence?: string[];
  confidence_adjustments?: ConfidenceAdjustment[];
  ai_review?: AiReview | null;
}

interface TopPick extends RecommendationQuality {
  rank: number;
  ticker: string;
  company_name: string;
  sector: string;
  logo_url?: string | null;
  company_info?: CompanyInfo;
  recommendation: "BUY" | "HOLD" | "SELL";
  risk_level: "LOW" | "MEDIUM" | "HIGH";
  confidence_score: number;
  catalyst: string;
  expected_timeframe: string;
  rationale: string;
  invalidation_criteria: string;
  supporting_evidence: string[];
  source_traceability: SignalSource[];
  price_chart?: PriceChart | null;
  related_news?: RelatedNewsArticle[];
  upcoming_events?: UpcomingTickerEvent[];
}

interface SellAlert extends RecommendationQuality {
  rank: number;
  ticker: string;
  company_name: string;
  sector: string;
  logo_url?: string | null;
  company_info?: CompanyInfo;
  severity: string;
  risk_level: "LOW" | "MEDIUM" | "HIGH";
  confidence_score: number;
  negative_catalyst: string;
  rationale: string;
  supporting_evidence: string[];
  source_traceability: SignalSource[];
  price_chart?: PriceChart | null;
  related_news?: RelatedNewsArticle[];
  upcoming_events?: UpcomingTickerEvent[];
}

interface FallbackPreview extends RecommendationQuality {
  rank: number;
  ticker: string;
  company_name: string;
  sector: string;
  logo_url?: string | null;
  company_info?: CompanyInfo;
  recommendation: "BUY" | "SELL";
  risk_level: "LOW" | "MEDIUM" | "HIGH";
  confidence_score: number;
  confidence_cap?: number;
  catalyst: string;
  expected_timeframe?: string;
  rationale: string;
  invalidation_criteria?: string | null;
  supporting_evidence: string[];
  source_traceability: SignalSource[];
  price_chart?: PriceChart | null;
  related_news?: RelatedNewsArticle[];
  upcoming_events?: UpcomingTickerEvent[];
  preview_warning?: string;
  automated_trading_excluded?: boolean;
}

interface ReviewRejection {
  ticker: string;
  company_name: string;
  sector?: string;
  logo_url?: string | null;
  company_info?: CompanyInfo;
  recommendation: "BUY" | "SELL";
  risk_level: "LOW" | "MEDIUM" | "HIGH";
  confidence_score: number;
  opportunity_score: number;
  negative_score: number;
  catalyst: string;
  analyst_reasoning: string;
  invalidation_criteria: string;
  supporting_evidence: string[];
  source_traceability?: SignalSource[];
  needed_evidence?: NeededEvidence[];
  ai_review: AiReview;
  price_chart?: PriceChart | null;
  related_news?: RelatedNewsArticle[];
  upcoming_events?: UpcomingTickerEvent[];
}

interface DataQuality {
  coverage_status?: string;
  active_ticker_count?: number;
  eligible_ticker_count?: number;
  excluded_ticker_count?: number;
  exclusion_reason_counts?: Record<string, number>;
}

interface TopPicksPayload {
  artifact_type?: string;
  publication_date: string;
  generated_at: string;
  publication_status?: string;
  suppression_reason?: string;
  top_picks: TopPick[];
  sell_alerts: SellAlert[];
  fallback_previews?: FallbackPreview[];
  review_rejections?: ReviewRejection[];
  candidate_count: number;
  analyzed_count: number;
  data_quality?: DataQuality;
  data_warnings: string[];
}

interface PublicationStatusPayload {
  artifact_type?: string;
  publication_date: string;
  generated_at: string;
  publication_status?: string;
  suppression_reason?: string;
  candidate_count?: number;
  analyzed_count?: number;
  data_quality?: DataQuality;
  data_warnings: string[];
}

interface WorkflowStatusPayload {
  artifact_type?: string;
  run_date: string;
  generated_at: string;
  status: "success" | "degraded" | "waiting" | "blocked" | string;
  decision?: string;
  execution?: {
    name?: string;
    started_at?: string;
  };
  analyzer?: {
    publication_date?: string;
    publication_status?: string;
    suppression_reason?: string;
    top_picks_count?: number;
    sell_alerts_count?: number;
  };
}

const TOP_PICKS_URL =
  import.meta.env.VITE_TOP_PICKS_URL || "/top-picks/latest.json";
const TOP_PICKS_STATUS_URL =
  import.meta.env.VITE_TOP_PICKS_STATUS_URL || statusUrlFor(TOP_PICKS_URL);
const WORKFLOW_STATUS_URL =
  import.meta.env.VITE_WORKFLOW_STATUS_URL || workflowUrlFor(TOP_PICKS_URL);
const DEV_DEMO_PAYLOAD = createDemoPayload();
const TRANSIENT_GATE_REASONS = new Set([
  "collection_manifest_missing",
  "analysis_not_before",
  "coverage_gates_failed",
]);

function statusUrlFor(url: string) {
  return url.replace("/top-picks/latest.json", "/top-picks/status/latest.json");
}

function workflowUrlFor(url: string) {
  return url.replace("/top-picks/latest.json", "/workflow/latest.json");
}

function historyUrlFor(publicationDate: string) {
  return TOP_PICKS_URL.replace(
    "/top-picks/latest.json",
    `/top-picks/history/${publicationDate}.json`,
  );
}

function previousPublicationDate(publicationDate: string) {
  const date = new Date(`${publicationDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

function isTransientGatePayload(payload: TopPicksPayload | PublicationStatusPayload) {
  if (payload.artifact_type === "collection_gate_status") return true;
  if (payload.publication_status === "waiting") return true;
  return (
    payload.publication_status === "suppressed" &&
    TRANSIENT_GATE_REASONS.has(payload.suppression_reason ?? "") &&
    (payload.candidate_count ?? 0) === 0 &&
    (payload.analyzed_count ?? 0) === 0
  );
}

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

function createDemoPayload(): TopPicksPayload {
  const candles = demoCandles();
  const price_chart: PriceChart = {
    period_start: candles[0].date,
    period_end: candles[candles.length - 1].date,
    currency: "USD",
    candles,
    sma_20: candles.map((candle, index) => ({
      date: candle.date,
      value: 193 + index * 1.22,
    })),
    trend_line: {
      start_date: candles[0].date,
      start_value: 192.1,
      end_date: candles[candles.length - 1].date,
      end_value: 225.7,
      slope_per_session: 1.4,
    },
    support: 214.0,
    resistance: 230.0,
  };

  return {
    publication_date: "2026-07-02",
    generated_at: "2026-07-02T17:40:00Z",
    top_picks: [
      {
        rank: 1,
        ticker: "AAPL",
        company_name: "Apple Inc.",
        sector: "Technology",
        company_info: {
          description:
            "Apple designs consumer electronics, software platforms, and services around an integrated hardware and ecosystem strategy.",
          top_products: ["iPhone", "Mac", "iPad", "Services"],
          revenue_segments: ["Products", "Services"],
          industry: "Consumer Electronics",
          exchange: "NASDAQ",
          currency: "USD",
          country: "United States",
          website: "https://www.apple.com",
          founded_year: 1976,
          headquarters: "Cupertino, California",
          ipo_year: 1980,
          metadata_source: "demo_company_profile",
          metadata_as_of: "2026-07-02",
          brief_history:
            "Founded in 1976; headquartered in Cupertino, California; IPO in 1980.",
        },
        recommendation: "BUY",
        publication_tier: "decision_grade",
        risk_level: "MEDIUM",
        confidence_score: 78,
        catalyst: "Improving demand checks and analyst support",
        expected_timeframe: "1-30 days",
        rationale:
          "Apple has a constructive blend of price momentum, resilient services revenue, and positive analyst revisions. The setup still needs confirmation from upcoming earnings, so position sizing should stay disciplined.",
        invalidation_criteria:
          "Breakdown below support or weaker demand commentary would invalidate the near-term setup.",
        supporting_evidence: [
          "AAPL traded above its 20-session average while volume expanded across the latest sessions.",
          "Analyst recommendation mix remains constructive with more buy-side support than sell-side pressure.",
          "Recent ticker-related news flow points to improving demand checks.",
        ],
        source_traceability: [
          { provider: "demo_price_data", observed_at: "2026-07-02T17:00:00Z" },
          { provider: "demo_news", observed_at: "2026-07-02T17:00:00Z" },
        ],
        price_chart,
        related_news: [
          {
            title: "Apple suppliers rise as demand checks improve",
            source: "Demo Wire",
            published_at: "2026-07-01T14:30:00Z",
            summary:
              "Recent channel checks point to stronger near-term device demand and better availability in premium models.",
            sentiment: "positive",
            url: "https://example.com/apple-demand-demo",
          },
          {
            title: "Analysts lift Apple estimates before product cycle update",
            source: "Market Demo",
            published_at: "2026-06-30T11:15:00Z",
            summary:
              "Several analysts highlighted resilient services revenue and a cleaner setup into the next hardware cycle.",
            sentiment: "positive",
            url: "https://example.com/apple-analyst-demo",
          },
        ],
        upcoming_events: [
          {
            event_type: "earnings",
            event_date: "2026-07-30",
            title: "Upcoming earnings",
            provider: "demo",
            source_url: "https://example.com/apple-earnings-demo",
            details: { time_of_day: "after_market" },
          },
          {
            event_type: "dividend",
            event_date: "2026-08-10",
            title: "Upcoming ex-dividend date",
            provider: "demo",
            source_url: "https://example.com/apple-dividend-demo",
            details: { dividend_amount: 0.26 },
          },
        ],
      },
      {
        rank: 2,
        ticker: "MSFT",
        company_name: "Microsoft Corporation",
        sector: "Technology",
        recommendation: "BUY",
        publication_tier: "reduced_confidence",
        missing_evidence: ["earnings_calendar", "source_evidence"],
        confidence_adjustments: [
          {
            reason: "optional_evidence_gap",
            adjustment: -10,
            detail: "Calendar and source-evidence signals were incomplete.",
          },
        ],
        risk_level: "MEDIUM",
        confidence_score: 68,
        catalyst: "Cloud and AI platform momentum remains constructive",
        expected_timeframe: "1-30 days",
        rationale:
          "The core setup is constructive, but optional evidence is incomplete, so the confidence is reduced until the repair workflow fills the gaps.",
        invalidation_criteria:
          "A failed breakout or weaker cloud commentary would invalidate the setup.",
        supporting_evidence: [
          "Price and history gates are fresh enough for analysis.",
          "Optional calendar and source-evidence signals were unavailable.",
        ],
        source_traceability: [
          { provider: "demo_price_data", observed_at: "2026-07-02T17:00:00Z" },
        ],
        price_chart,
        related_news: [],
        upcoming_events: [],
      },
    ],
    sell_alerts: [
      {
        rank: 1,
        ticker: "TSLA",
        company_name: "Tesla, Inc.",
        sector: "Consumer Discretionary",
        company_info: {
          description:
            "Tesla designs electric vehicles, energy storage systems, solar products, and related software-enabled services.",
          top_products: ["Model Y", "Model 3", "Energy storage"],
          revenue_segments: ["Automotive", "Energy generation and storage", "Services"],
          industry: "Auto Manufacturing",
          exchange: "NASDAQ",
          currency: "USD",
          country: "United States",
          website: "https://www.tesla.com",
          founded_year: 2003,
          headquarters: "Austin, Texas",
          ipo_year: 2010,
          metadata_source: "demo_company_profile",
          metadata_as_of: "2026-07-02",
          brief_history:
            "Founded in 2003; headquartered in Austin, Texas; IPO in 2010.",
        },
        severity: "high",
        publication_tier: "decision_grade",
        risk_level: "HIGH",
        confidence_score: 66,
        negative_catalyst: "Weak delivery narrative and elevated volatility",
        rationale:
          "Tesla's recent price action is choppy and negative news momentum is elevated. The signal is not a certainty, but it is strong enough to deserve risk attention.",
        supporting_evidence: [
          "TSLA closed below short-term support after several high-volume down sessions.",
          "Recent coverage emphasized delivery uncertainty and margin pressure.",
        ],
        source_traceability: [
          { provider: "demo_price_data", observed_at: "2026-07-02T17:00:00Z" },
        ],
        price_chart: demoSellChart(candles),
        related_news: [
          {
            title: "Tesla delivery debate weighs on investor sentiment",
            source: "Demo Wire",
            published_at: "2026-07-01T13:00:00Z",
            summary:
              "Investors remain focused on delivery mix, pricing pressure, and whether margins can stabilize.",
            sentiment: "negative",
            url: "https://example.com/tesla-delivery-demo",
          },
        ],
        upcoming_events: [
          {
            event_type: "earnings",
            event_date: "2026-07-23",
            title: "Upcoming earnings",
            provider: "demo",
            source_url: "https://example.com/tesla-earnings-demo",
            details: { time_of_day: "after_market" },
          },
        ],
      },
    ],
    fallback_previews: [
      {
        rank: 1,
        ticker: "NVDA",
        company_name: "NVIDIA Corporation",
        sector: "Technology",
        recommendation: "BUY",
        publication_tier: "fallback_preview",
        analysis_method: "fallback_heuristic",
        risk_level: "HIGH",
        confidence_score: 55,
        confidence_cap: 55,
        catalyst: "Heuristic momentum setup needs AI confirmation",
        expected_timeframe: "1-30 days",
        rationale:
          "The fallback scorer found an actionable setup, but AI analysis or review was unavailable, so this remains a preview-only candidate.",
        invalidation_criteria:
          "Loss of momentum or failed AI review should keep this out of publication.",
        supporting_evidence: [
          "Heuristic price and catalyst signals crossed preview thresholds.",
        ],
        source_traceability: [
          { provider: "demo_price_data", observed_at: "2026-07-02T17:00:00Z" },
        ],
        price_chart,
        related_news: [],
        upcoming_events: [],
        preview_warning:
          "Heuristic fallback preview only; AI analysis did not complete. Human review required.",
        automated_trading_excluded: true,
      },
    ],
    review_rejections: [
      {
        ticker: "AXON",
        company_name: "Axon Enterprise, Inc.",
        sector: "Industrials",
        recommendation: "BUY",
        risk_level: "MEDIUM",
        confidence_score: 71,
        opportunity_score: 436,
        negative_score: 0,
        catalyst: "Momentum and public-safety demand signals need stronger confirmation",
        analyst_reasoning:
          "The analyst setup is interesting, but the reviewer withheld publication until the evidence explains fundamental impact and downside risk.",
        invalidation_criteria:
          "Breakout failure, weaker public-safety demand, or lack of follow-through would invalidate the setup.",
        supporting_evidence: [
          "AXON traded above recent moving averages with expanded volume.",
          "Reviewer requested clearer earnings, guidance, valuation, and company-specific catalyst evidence.",
        ],
        source_traceability: [
          { provider: "demo_price_data", observed_at: "2026-07-02T17:00:00Z" },
        ],
        needed_evidence: [
          {
            gap_type: "fundamental_valuation_context",
            title: "Fundamental and valuation context",
            status: "needed",
            collection_plan:
              "Collect recent earnings metrics, guidance changes, valuation context, and downside risk before publication.",
            source_candidates: ["earnings release", "SEC filing", "fundamentals provider"],
          },
          {
            gap_type: "technical_confirmation",
            title: "Technical confirmation and trade horizon",
            status: "needed",
            collection_plan:
              "Add breakout level, invalidation price, multi-session volume confirmation, and trade horizon.",
            source_candidates: ["stored OHLCV", "sector ETF context"],
          },
        ],
        ai_review: {
          status: "rejected",
          model: "gpt-5.4",
          approved: false,
          rationale:
            "The thesis relies too heavily on price momentum without enough company-specific support.",
          concerns: [
            "Overreliance on recent price momentum",
            "No valuation or downside context",
          ],
          rejection_category: "insufficient_support",
          what_would_make_approvable:
            "Add concrete earnings metrics, guidance changes, valuation context, and explicit invalidation levels.",
        },
        price_chart,
        related_news: [],
        upcoming_events: [],
      },
    ],
    candidate_count: 42,
    analyzed_count: 8,
    data_quality: {
      coverage_status: "demo",
      active_ticker_count: 1003,
      eligible_ticker_count: 812,
      excluded_ticker_count: 191,
      exclusion_reason_counts: { stale_price_data: 121, missing_news: 70 },
    },
    data_warnings: [
      "Local demo data is shown because /top-picks/latest.json is not available from the Vite dev server.",
    ],
  };
}

function demoCandles(): PriceCandle[] {
  return [
    [0, 191.2, 193.5, 190.6, 192.4, 42100000],
    [1, 192.5, 195.1, 191.8, 194.9, 43800000],
    [2, 195.0, 197.2, 194.1, 196.8, 46200000],
    [3, 197.1, 198.4, 195.6, 196.2, 40500000],
    [4, 196.3, 199.8, 195.9, 199.1, 51400000],
    [5, 199.5, 202.2, 198.7, 201.6, 55200000],
    [6, 201.2, 203.8, 200.9, 203.1, 48900000],
    [7, 202.8, 204.4, 201.5, 202.3, 44500000],
    [8, 202.5, 205.7, 201.8, 205.2, 57100000],
    [9, 205.6, 207.3, 204.4, 206.8, 59900000],
    [10, 206.1, 208.8, 205.5, 208.2, 61200000],
    [11, 208.5, 211.0, 207.4, 210.4, 65500000],
    [12, 210.1, 212.6, 208.8, 209.7, 58200000],
    [13, 209.2, 211.8, 208.3, 211.3, 53000000],
    [14, 211.5, 214.1, 210.9, 213.5, 68800000],
    [15, 213.8, 216.4, 212.7, 215.9, 72100000],
    [16, 215.1, 217.2, 213.9, 214.4, 60300000],
    [17, 214.7, 216.8, 213.6, 216.1, 56600000],
    [18, 216.6, 219.4, 215.8, 218.9, 74500000],
    [19, 219.2, 221.0, 217.5, 220.3, 76800000],
    [20, 220.0, 222.7, 219.1, 222.1, 81200000],
    [21, 222.4, 224.5, 220.8, 221.2, 69000000],
    [22, 221.5, 225.1, 220.9, 224.4, 85500000],
    [23, 224.7, 227.3, 223.8, 226.8, 90200000],
    [24, 226.2, 229.6, 225.4, 228.9, 94700000],
  ].map(([offset, open, high, low, close, volume]) => ({
    date: demoDate(offset),
    open,
    high,
    low,
    close,
    volume,
  }));
}

function demoSellChart(candles: PriceCandle[]): PriceChart {
  const sellCandles = candles.map((candle, index) => ({
    ...candle,
    open: 186 - index * 1.8,
    high: 189 - index * 1.7,
    low: 182 - index * 1.9,
    close: 184 - index * 1.85,
    volume: candle.volume + index * 900000,
  }));
  return {
    period_start: sellCandles[0].date,
    period_end: sellCandles[sellCandles.length - 1].date,
    currency: "USD",
    candles: sellCandles,
    sma_20: sellCandles.map((candle, index) => ({
      date: candle.date,
      value: 185 - index * 1.1,
    })),
    trend_line: {
      start_date: sellCandles[0].date,
      start_value: 185.5,
      end_date: sellCandles[sellCandles.length - 1].date,
      end_value: 142.0,
      slope_per_session: -1.81,
    },
    support: 139.5,
    resistance: 176.2,
  };
}

function demoDate(offset: number) {
  const base = new Date("2026-06-01T00:00:00Z");
  base.setUTCDate(base.getUTCDate() + offset);
  return base.toISOString().slice(0, 10);
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

interface DashboardProps {
  onNavigate?: (view: "top-picks" | "calendar" | "data-health") => void;
}

export default function Dashboard({ onNavigate }: DashboardProps) {
  const [payload, setPayload] = useState<TopPicksPayload | null>(null);
  const [statusPayload, setStatusPayload] = useState<PublicationStatusPayload | null>(null);
  const [workflowStatus, setWorkflowStatus] = useState<WorkflowStatusPayload | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function loadOptionalStatus() {
    try {
      const response = await fetch(TOP_PICKS_STATUS_URL, { cache: "no-store" });
      if (!response.ok) return null;
      const status = (await response.json()) as PublicationStatusPayload;
      return isTransientGatePayload(status) ? status : null;
    } catch {
      return null;
    }
  }

  async function loadPreviousCompletedPublication(transientPayload: TopPicksPayload) {
    const previousDate = previousPublicationDate(transientPayload.publication_date);
    const response = await fetch(historyUrlFor(previousDate), { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as TopPicksPayload;
  }

  async function loadOptionalWorkflowStatus() {
    try {
      const response = await fetch(WORKFLOW_STATUS_URL, { cache: "no-store" });
      if (!response.ok) return null;
      const status = (await response.json()) as WorkflowStatusPayload;
      return status.artifact_type === "daily_workflow_status" ? status : null;
    } catch {
      return null;
    }
  }

  async function loadTopPicks() {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(TOP_PICKS_URL, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const latestPayload = (await response.json()) as TopPicksPayload;
      const [latestStatus, latestWorkflowStatus] = await Promise.all([
        loadOptionalStatus(),
        loadOptionalWorkflowStatus(),
      ]);
      setWorkflowStatus(latestWorkflowStatus);
      if (isTransientGatePayload(latestPayload)) {
        const fallbackPayload = await loadPreviousCompletedPublication(latestPayload);
        setPayload(fallbackPayload ?? latestPayload);
        setStatusPayload(latestStatus ?? latestPayload);
        return;
      }
      setPayload(latestPayload);
      setStatusPayload(latestStatus);
    } catch {
      if (import.meta.env.DEV) {
        setPayload(DEV_DEMO_PAYLOAD);
        setStatusPayload(null);
        setWorkflowStatus(null);
        return;
      }
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
  const fallbackPreviews = payload?.fallback_previews ?? [];
  const reviewedTopPicks = (payload?.top_picks ?? []).filter(
    (pick) => publicationTier(pick) === "decision_grade",
  );
  const lowerConfidencePicks = (payload?.top_picks ?? []).filter(
    (pick) => publicationTier(pick) === "reduced_confidence",
  );
  const reviewedSellAlerts = (payload?.sell_alerts ?? []).filter(
    (alert) => publicationTier(alert) === "decision_grade",
  );
  const lowerConfidenceSellAlerts = (payload?.sell_alerts ?? []).filter(
    (alert) => publicationTier(alert) === "reduced_confidence",
  );
  const statusIsForDisplayedPublication =
    statusPayload?.publication_date === payload?.publication_date;
  const currentStatusWarnings =
    statusPayload && !statusIsForDisplayedPublication
      ? statusPayload.data_warnings
      : [];

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <section className="border-b border-slate-800 bg-slate-900">
        <div className="mx-auto flex max-w-7xl flex-col gap-5 px-5 py-6 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal">
              Stockara
            </h1>
            <p className="mt-2 max-w-3xl text-sm text-slate-300">
              Daily reviewed market opportunities, lower-confidence research
              ideas, and urgent risk alerts.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {onNavigate && (
              <>
                <button
                  onClick={() => onNavigate("calendar")}
                  className="inline-flex h-10 items-center justify-center rounded border border-slate-700 bg-slate-900 px-3 text-sm font-medium text-slate-200 hover:bg-slate-800"
                >
                  Calendars
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
              onClick={loadTopPicks}
              className="inline-flex h-10 items-center justify-center rounded border border-slate-700 bg-slate-800 px-3 text-sm font-medium text-slate-100 hover:bg-slate-700"
            >
              Refresh
            </button>
          </div>
        </div>
      </section>

      {!loading && payload && (
        <div className="mx-auto max-w-7xl px-5 pt-5">
          <DailyRunSummary
            workflowStatus={workflowStatus}
            publicationStatus={statusPayload}
            publication={payload}
            onOpenDataHealth={
              onNavigate ? () => onNavigate("data-health") : undefined
            }
          />
        </div>
      )}

      <section className="mx-auto grid max-w-7xl grid-cols-2 gap-4 px-5 py-5 lg:grid-cols-4">
        <Metric marker="time" label="Generated" value={generatedLabel} />
        <Metric
          marker="picks"
          label="Reviewed Picks"
          value={String(reviewedTopPicks.length)}
        />
        <Metric
          marker="risk"
          label="Sell Alerts"
          value={String(reviewedSellAlerts.length)}
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
            {statusPayload && !statusIsForDisplayedPublication && (
              <div className="flex flex-col gap-2 border-l-2 border-amber-500 bg-amber-950/60 px-4 py-3 text-sm text-amber-100 md:flex-row md:items-center md:justify-between">
                <p>
                  <span className="font-medium">
                    {statusPayload.publication_date} review is still in progress.
                  </span>{" "}
                  Showing the latest completed publication from {payload.publication_date}.
                </p>
                {currentStatusWarnings[0] && (
                  <span className="text-xs text-amber-200">
                    {currentStatusWarnings[0]}
                  </span>
                )}
              </div>
            )}

            {reviewedTopPicks.length === 0 && reviewedSellAlerts.length === 0 ? (
              <section>
                <h2 className="mb-3 text-lg font-semibold">Reviewed Calls</h2>
                <div className="border border-slate-800 bg-slate-900 p-5 text-sm text-slate-300">
                  No BUY or urgent SELL recommendation passed the review gate
                  for this publication.
                </div>
              </section>
            ) : (
              <section>
                <h2 className="mb-3 text-lg font-semibold">Reviewed Top Picks</h2>
                <div className="grid gap-4 lg:grid-cols-2">
                  {reviewedTopPicks.map((pick) => (
                    <PickRow key={pick.ticker} pick={pick} />
                  ))}
                </div>
              </section>
            )}

            {(lowerConfidencePicks.length > 0 || lowerConfidenceSellAlerts.length > 0) && (
              <section>
                <h2 className="mb-3 text-lg font-semibold">
                  Lower-Confidence Suggestions
                </h2>
                <div className="grid gap-4 lg:grid-cols-2">
                  {lowerConfidencePicks.map((pick) => (
                    <PickRow key={pick.ticker} pick={pick} />
                  ))}
                  {lowerConfidenceSellAlerts.map((alert) => (
                    <SellAlertRow key={alert.ticker} alert={alert} />
                  ))}
                </div>
              </section>
            )}

            {reviewedSellAlerts.length > 0 && (
              <section>
                <h2 className="mb-3 text-lg font-semibold">Urgent Sell Alerts</h2>
                <div className="grid gap-4 lg:grid-cols-2">
                  {reviewedSellAlerts.map((alert) => (
                    <SellAlertRow key={alert.ticker} alert={alert} />
                  ))}
                </div>
              </section>
            )}

            {fallbackPreviews.length > 0 && (
              <section>
                <h2 className="mb-3 text-lg font-semibold">Fallback Previews</h2>
                <div className="grid gap-4 lg:grid-cols-2">
                  {fallbackPreviews.map((preview) => (
                    <FallbackPreviewRow key={preview.ticker} preview={preview} />
                  ))}
                </div>
              </section>
            )}

            {payload.data_warnings.length > 0 && (
              <details className="border border-slate-800 bg-slate-900 text-sm text-slate-200">
                <summary className="cursor-pointer px-4 py-3 font-medium hover:bg-slate-800">
                  Data quality details ({payload.data_warnings.length} warnings)
                </summary>
                <div className="border-t border-slate-800 px-4 py-4">
                  <ul className="space-y-1">
                    {payload.data_warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                  <FreshnessSummary dataQuality={payload.data_quality} />
                  <BlockedDataIssues dataQuality={payload.data_quality} />
                </div>
              </details>
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
          </div>
        )}
      </div>
    </main>
  );
}

function DailyRunSummary({
  workflowStatus,
  publicationStatus,
  publication,
  onOpenDataHealth,
}: {
  workflowStatus: WorkflowStatusPayload | null;
  publicationStatus: PublicationStatusPayload | null;
  publication: TopPicksPayload;
  onOpenDataHealth?: () => void;
}) {
  const status =
    workflowStatus?.status ?? publicationStatus?.publication_status ?? "unknown";
  const runDate =
    workflowStatus?.run_date ??
    publicationStatus?.publication_date ??
    publication.publication_date;
  const updatedAt = workflowStatus?.generated_at ?? publicationStatus?.generated_at;
  const statusLabel = {
    success: "Completed",
    degraded: "Completed with gaps",
    waiting: "In progress",
    blocked: "Needs attention",
    suppressed: "Needs attention",
  }[status] ?? "Status unavailable";
  const statusTone =
    status === "success"
      ? "bg-emerald-400"
      : status === "degraded" || status === "waiting"
        ? "bg-amber-400"
        : status === "blocked" || status === "suppressed"
          ? "bg-red-400"
          : "bg-slate-500";

  return (
    <section className="border-y border-slate-800 py-4">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-[1.2fr_1fr_1fr_1.4fr_auto] lg:items-center">
        <div>
          <div className="text-xs font-semibold uppercase text-slate-500">
            Daily run
          </div>
          <div className="mt-1 flex items-center gap-2 text-sm font-medium">
            <span className={`h-2 w-2 rounded-full ${statusTone}`} />
            {statusLabel}
          </div>
        </div>
        <RunFact label="Run date" value={runDate} />
        <RunFact label="Latest publication" value={publication.publication_date} />
        <RunFact
          label="Status updated"
          value={updatedAt ? formatDate(updatedAt) : "Awaiting first workflow report"}
        />
        {onOpenDataHealth && (
          <button
            onClick={onOpenDataHealth}
            className="h-9 border border-slate-700 px-3 text-sm font-medium text-slate-200 hover:bg-slate-900"
          >
            View data health
          </button>
        )}
      </div>
    </section>
  );
}

function RunFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase text-slate-500">{label}</div>
      <div className="mt-1 text-sm text-slate-200">{value}</div>
    </div>
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

function BlockedDataIssues({ dataQuality }: { dataQuality?: DataQuality }) {
  const entries = Object.entries(dataQuality?.exclusion_reason_counts ?? {}).sort(
    (a, b) => b[1] - a[1],
  );
  if (entries.length === 0) return null;
  return (
    <div className="mt-4 border-t border-slate-800 pt-4">
      <div className="grid gap-3 text-sm md:grid-cols-3">
        <Fact
          label="Excluded"
          value={String(dataQuality?.excluded_ticker_count ?? totalReasonCount(entries))}
        />
        <Fact label="Eligible" value={String(dataQuality?.eligible_ticker_count ?? "n/a")} />
        <Fact label="Coverage" value={dataQuality?.coverage_status ?? "unknown"} />
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        {entries.map(([reason, count]) => (
          <span
            key={reason}
            className="border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200"
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
        <div className="flex min-w-0 items-start gap-3">
          <TickerLogo ticker={row.ticker} companyName={row.company_name} logoUrl={row.logo_url} />
          <div className="min-w-0">
            <div className="text-xs uppercase text-slate-500">
              Analyst proposed {row.recommendation}
            </div>
            <h3 className="mt-1 text-lg font-semibold">{row.ticker}</h3>
            <p className="mt-1 text-sm text-slate-400">
              {row.company_name}
              {row.sector ? ` · ${row.sector}` : ""}
            </p>
          </div>
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

      <CompanyInfoPanel companyName={row.company_name} info={row.company_info} tone="slate" />
      <PriceChartPanel chart={row.price_chart} tone="slate" />

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

      <NeededEvidence items={row.needed_evidence ?? []} />

      {row.ai_review.concerns.length > 0 && (
        <ul className="mt-4 space-y-1 border-t border-slate-800 pt-3 text-sm text-slate-400">
          {row.ai_review.concerns.map((concern) => (
            <li key={concern}>{concern}</li>
          ))}
        </ul>
      )}

      <Evidence items={row.supporting_evidence} />
      <RelatedNews items={row.related_news ?? []} tone="slate" />
      <UpcomingEvents items={row.upcoming_events ?? []} tone="slate" />
      <SourceTraceability items={row.source_traceability ?? []} tone="slate" />
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
  const isReducedConfidence = publicationTier(pick) === "reduced_confidence";
  return (
    <article
      className={`border p-5 ${
        isReducedConfidence
          ? "border-amber-800 bg-slate-900"
          : "border-slate-800 bg-slate-900"
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <TickerLogo ticker={pick.ticker} companyName={pick.company_name} logoUrl={pick.logo_url} />
          <div className="min-w-0">
            <div className="text-xs text-slate-400">#{pick.rank}</div>
            <h3 className="mt-1 text-xl font-semibold leading-tight">{pick.company_name}</h3>
            <p className="mt-1 text-sm text-slate-400">
              {pick.ticker} · {pick.sector}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <PublicationTierBadge tier={publicationTier(pick)} />
          <span className="border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700">
            {pick.recommendation}
          </span>
          <span className={`border px-2 py-1 text-xs ${badgeClass(pick.risk_level)}`}>
            {pick.risk_level}
          </span>
        </div>
      </div>
      <CompanyInfoPanel companyName={pick.company_name} info={pick.company_info} tone="slate" />
      <p className="mt-4 text-sm font-medium text-slate-100">{pick.catalyst}</p>
      <p className="mt-2 text-sm leading-6 text-slate-300">{pick.rationale}</p>
      <RecommendationQualityPanel row={pick} tone="slate" />
      <PriceChartPanel chart={pick.price_chart} tone="slate" />
      <div className="mt-4 grid gap-3 text-sm md:grid-cols-2">
        <Fact label="Confidence" value={`${pick.confidence_score}%`} />
        <Fact label="Timeframe" value={pick.expected_timeframe} />
      </div>
      <Evidence items={pick.supporting_evidence} />
      <RelatedNews items={pick.related_news ?? []} tone="slate" />
      <UpcomingEvents items={pick.upcoming_events ?? []} tone="slate" />
    </article>
  );
}

function SellAlertRow({ alert }: { alert: SellAlert }) {
  const isReducedConfidence = publicationTier(alert) === "reduced_confidence";
  return (
    <article
      className={`border p-5 ${
        isReducedConfidence
          ? "border-amber-800 bg-red-950"
          : "border-red-900 bg-red-950"
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <TickerLogo
            ticker={alert.ticker}
            companyName={alert.company_name}
            logoUrl={alert.logo_url}
            tone="red"
          />
          <div className="min-w-0">
            <div className="text-xs text-red-200">#{alert.rank}</div>
            <h3 className="mt-1 text-xl font-semibold leading-tight">{alert.company_name}</h3>
            <p className="mt-1 text-sm text-red-200">
              {alert.ticker} · {alert.sector}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <PublicationTierBadge tier={publicationTier(alert)} />
          <span className={`border px-2 py-1 text-xs ${badgeClass(alert.severity)}`}>
            {alert.severity}
          </span>
        </div>
      </div>
      <CompanyInfoPanel companyName={alert.company_name} info={alert.company_info} tone="red" />
      <p className="mt-4 text-sm font-medium text-red-50">
        {alert.negative_catalyst}
      </p>
      <p className="mt-2 text-sm leading-6 text-red-100">{alert.rationale}</p>
      <RecommendationQualityPanel row={alert} tone="red" />
      <PriceChartPanel chart={alert.price_chart} tone="red" />
      <div className="mt-4 grid gap-3 text-sm md:grid-cols-2">
        <Fact label="Confidence" value={`${alert.confidence_score}%`} />
        <Fact label="Risk" value={alert.risk_level} />
      </div>
      <Evidence items={alert.supporting_evidence} />
      <RelatedNews items={alert.related_news ?? []} tone="red" />
      <UpcomingEvents items={alert.upcoming_events ?? []} tone="red" />
    </article>
  );
}

function FallbackPreviewRow({ preview }: { preview: FallbackPreview }) {
  const tone = preview.recommendation === "SELL" ? "red" : "slate";
  const articleClass =
    preview.recommendation === "SELL"
      ? "border border-amber-700 bg-red-950 p-5"
      : "border border-amber-700 bg-slate-900 p-5";
  const titleClass = preview.recommendation === "SELL" ? "text-red-50" : "text-slate-100";
  const textClass = preview.recommendation === "SELL" ? "text-red-100" : "text-slate-300";
  const mutedClass = preview.recommendation === "SELL" ? "text-red-200" : "text-slate-400";

  return (
    <article className={articleClass}>
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <TickerLogo
            ticker={preview.ticker}
            companyName={preview.company_name}
            logoUrl={preview.logo_url}
            tone={tone}
          />
          <div className="min-w-0">
            <div className={`text-xs ${mutedClass}`}>#{preview.rank}</div>
            <h3 className={`mt-1 text-xl font-semibold leading-tight ${titleClass}`}>
              {preview.company_name}
            </h3>
            <p className={`mt-1 text-sm ${mutedClass}`}>
              {preview.ticker} · {preview.sector}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <PublicationTierBadge tier="fallback_preview" />
          <span
            className={`border px-2 py-1 text-xs font-semibold ${
              preview.recommendation === "SELL"
                ? "border-red-300 bg-red-50 text-red-700"
                : "border-emerald-300 bg-emerald-50 text-emerald-700"
            }`}
          >
            {preview.recommendation}
          </span>
          <span className={`border px-2 py-1 text-xs ${badgeClass(preview.risk_level)}`}>
            {preview.risk_level}
          </span>
        </div>
      </div>
      {preview.preview_warning && (
        <p className="mt-4 border border-amber-700 bg-amber-950 p-3 text-sm font-medium text-amber-100">
          {preview.preview_warning}
        </p>
      )}
      <CompanyInfoPanel companyName={preview.company_name} info={preview.company_info} tone={tone} />
      <p className={`mt-4 text-sm font-medium ${titleClass}`}>{preview.catalyst}</p>
      <p className={`mt-2 text-sm leading-6 ${textClass}`}>{preview.rationale}</p>
      <RecommendationQualityPanel row={preview} tone={tone} />
      <PriceChartPanel chart={preview.price_chart} tone={tone} />
      <div className="mt-4 grid gap-3 text-sm md:grid-cols-3">
        <Fact label="Confidence" value={`${preview.confidence_score}%`} />
        <Fact
          label="Confidence cap"
          value={preview.confidence_cap == null ? "n/a" : `${preview.confidence_cap}%`}
        />
        <Fact
          label="Automation"
          value={preview.automated_trading_excluded ? "excluded" : "manual only"}
        />
      </div>
      <Evidence items={preview.supporting_evidence} />
      <RelatedNews items={preview.related_news ?? []} tone={tone} />
      <UpcomingEvents items={preview.upcoming_events ?? []} tone={tone} />
      <SourceTraceability items={preview.source_traceability ?? []} tone={tone} />
    </article>
  );
}

function PublicationTierBadge({ tier }: { tier: string }) {
  const label = publicationTierLabel(tier);
  const classes =
    tier === "reduced_confidence"
      ? "border-amber-500 bg-amber-950 text-amber-100"
      : tier === "fallback_preview"
        ? "border-amber-500 bg-slate-950 text-amber-100"
        : tier === "blocked"
          ? "border-red-500 bg-red-950 text-red-100"
          : "border-sky-500 bg-sky-950 text-sky-100";
  return <span className={`border px-2 py-1 text-xs font-semibold ${classes}`}>{label}</span>;
}

function RecommendationQualityPanel({
  row,
  tone,
}: {
  row: RecommendationQuality;
  tone: "slate" | "red";
}) {
  const missingEvidence = row.missing_evidence ?? [];
  const adjustments = row.confidence_adjustments ?? [];
  if (missingEvidence.length === 0 && adjustments.length === 0) return null;

  const border = tone === "red" ? "border-red-900" : "border-slate-800";
  const muted = tone === "red" ? "text-red-200" : "text-slate-400";
  const text = tone === "red" ? "text-red-100" : "text-slate-300";
  return (
    <section className={`mt-4 border-t ${border} pt-4`}>
      <h4 className={`text-xs font-semibold uppercase ${muted}`}>Confidence context</h4>
      {missingEvidence.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {missingEvidence.map((item) => (
            <span
              key={item}
              className="border border-amber-700 bg-amber-950 px-2 py-1 text-xs text-amber-100"
            >
              missing {item}
            </span>
          ))}
        </div>
      )}
      {adjustments.length > 0 && (
        <div className={`mt-3 space-y-1 text-sm ${text}`}>
          {adjustments.map((adjustment) => (
            <p key={`${adjustment.reason}-${adjustment.adjustment}-${adjustment.detail ?? ""}`}>
              {adjustment.reason}: {formatSignedNumber(adjustment.adjustment)}
              {adjustment.detail ? ` · ${adjustment.detail}` : ""}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

function CompanyInfoPanel({
  companyName,
  info,
  tone,
}: {
  companyName: string;
  info?: CompanyInfo;
  tone: "slate" | "red";
}) {
  if (!info || Object.keys(info).length === 0) return null;
  const border = tone === "red" ? "border-red-900" : "border-slate-800";
  const title = tone === "red" ? "text-red-50" : "text-slate-100";
  const text = tone === "red" ? "text-red-100" : "text-slate-300";
  const muted = tone === "red" ? "text-red-200" : "text-slate-400";
  const chips = [
    info.industry,
    info.exchange,
    info.country,
    info.currency,
    info.founded_year ? `Founded ${info.founded_year}` : "",
    info.ipo_year ? `IPO ${info.ipo_year}` : "",
  ].filter(Boolean);

  return (
    <details className={`mt-4 border-t ${border} pt-4`}>
      <summary className={`cursor-pointer text-sm font-semibold ${title}`}>
        Company info
      </summary>
      <div className="mt-3 space-y-3">
        {info.description ? (
          <p className={`text-sm leading-6 ${text}`}>{info.description}</p>
        ) : (
          <p className={`text-sm ${muted}`}>No source-backed company description available.</p>
        )}
        {info.brief_history && (
          <p className={`text-sm leading-6 ${text}`}>{info.brief_history}</p>
        )}
        {chips.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {chips.map((chip) => (
              <span key={String(chip)} className={`border ${border} px-2 py-1 text-xs ${muted}`}>
                {chip}
              </span>
            ))}
          </div>
        )}
        <InfoList label="Top products" items={info.top_products ?? []} tone={tone} />
        <InfoList label="Revenue segments" items={info.revenue_segments ?? []} tone={tone} />
        {info.competitive_position && (
          <InfoText label="Position" value={info.competitive_position} tone={tone} />
        )}
        <InfoList label="Static risks" items={info.key_static_risks ?? []} tone={tone} />
        <div className={`grid gap-3 text-sm md:grid-cols-2 ${text}`}>
          {info.headquarters && <Fact label="Headquarters" value={info.headquarters} />}
          {info.market_cap && <Fact label="Market cap" value={formatMarketCap(info.market_cap)} />}
          {info.website && (
            <div>
              <div className="text-xs uppercase text-slate-500">Website</div>
              <a
                className={`mt-1 inline-block underline underline-offset-4 ${muted}`}
                href={info.website}
                target="_blank"
                rel="noreferrer"
              >
                {companyName}
              </a>
            </div>
          )}
          {(info.metadata_source || info.metadata_as_of) && (
            <div>
              <div className="text-xs uppercase text-slate-500">Metadata</div>
              {info.metadata_source_url ? (
                <a
                  className={`mt-1 inline-block underline underline-offset-4 ${muted}`}
                  href={info.metadata_source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {info.metadata_source ?? "Source"}
                </a>
              ) : (
                <div className={`mt-1 ${muted}`}>{info.metadata_source}</div>
              )}
              {info.metadata_as_of && (
                <div className={`mt-1 text-xs ${muted}`}>As of {formatShortDate(info.metadata_as_of)}</div>
              )}
            </div>
          )}
          {(info.logo_source || info.logo_checked_at) && (
            <div>
              <div className="text-xs uppercase text-slate-500">Logo</div>
              {info.logo_source_url ? (
                <a
                  className={`mt-1 inline-block underline underline-offset-4 ${muted}`}
                  href={info.logo_source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {info.logo_source ?? "Source"}
                </a>
              ) : (
                <div className={`mt-1 ${muted}`}>{info.logo_source}</div>
              )}
              {info.logo_checked_at && (
                <div className={`mt-1 text-xs ${muted}`}>
                  Checked {formatShortDate(info.logo_checked_at)}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </details>
  );
}

function InfoList({
  label,
  items,
  tone,
}: {
  label: string;
  items: string[];
  tone: "slate" | "red";
}) {
  if (items.length === 0) return null;
  const muted = tone === "red" ? "text-red-200" : "text-slate-400";
  const text = tone === "red" ? "text-red-100" : "text-slate-300";
  return (
    <div>
      <div className={`text-xs font-semibold uppercase ${muted}`}>{label}</div>
      <div className={`mt-1 text-sm leading-6 ${text}`}>{items.join(", ")}</div>
    </div>
  );
}

function InfoText({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "slate" | "red";
}) {
  const muted = tone === "red" ? "text-red-200" : "text-slate-400";
  const text = tone === "red" ? "text-red-100" : "text-slate-300";
  return (
    <div>
      <div className={`text-xs font-semibold uppercase ${muted}`}>{label}</div>
      <p className={`mt-1 text-sm leading-6 ${text}`}>{value}</p>
    </div>
  );
}

function NeededEvidence({ items }: { items: NeededEvidence[] }) {
  if (items.length === 0) return null;
  return (
    <section className="mt-4 border-t border-amber-900 pt-4">
      <h4 className="text-xs font-semibold uppercase text-amber-200">
        Needed evidence plan
      </h4>
      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        {items.map((item) => (
          <div key={item.gap_type} className="border border-amber-900 bg-amber-950 p-3">
            <div className="flex items-start justify-between gap-2">
              <h5 className="text-sm font-semibold text-amber-50">{item.title}</h5>
              <span className="border border-amber-700 px-2 py-1 text-xs text-amber-100">
                {item.status}
              </span>
            </div>
            <p className="mt-2 text-sm leading-5 text-amber-100">{item.collection_plan}</p>
            {item.source_candidates.length > 0 && (
              <p className="mt-2 text-xs text-amber-200">
                Sources: {item.source_candidates.join(", ")}
              </p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function TickerLogo({
  ticker,
  companyName,
  logoUrl,
  tone = "slate",
}: {
  ticker: string;
  companyName: string;
  logoUrl?: string | null;
  tone?: "slate" | "red";
}) {
  const [imageFailed, setImageFailed] = useState(false);
  const initials = companyName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join("") || ticker.slice(0, 2).toUpperCase();
  const base =
    tone === "red"
      ? "border-red-800 bg-red-900 text-red-100"
      : "border-slate-700 bg-slate-800 text-slate-100";
  return (
    <div
      className={`flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden border text-xs font-semibold ${base}`}
      aria-label={`${ticker} logo`}
    >
      {logoUrl && !imageFailed ? (
        <img
          src={logoUrl}
          alt=""
          className="h-full w-full object-contain"
          onError={() => setImageFailed(true)}
        />
      ) : (
        initials
      )}
    </div>
  );
}

function PriceChartPanel({
  chart,
  tone,
}: {
  chart?: PriceChart | null;
  tone: "slate" | "red";
}) {
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  if (!chart || chart.candles.length < 2) {
    return (
      <div
        className={`mt-4 border-t pt-4 text-sm ${
          tone === "red"
            ? "border-red-900 text-red-200"
            : "border-slate-800 text-slate-400"
        }`}
      >
        Static price chart unavailable for this publication.
      </div>
    );
  }

  const candles = chart.candles
    .map((candle) => ({
      ...candle,
      open: toNumber(candle.open),
      high: toNumber(candle.high),
      low: toNumber(candle.low),
      close: toNumber(candle.close),
      volume: Number(candle.volume) || 0,
    }))
    .filter((candle) =>
      [candle.open, candle.high, candle.low, candle.close].every(Number.isFinite),
    );
  if (candles.length < 2) return null;

  const width = 720;
  const plotRight = 662;
  const yAxisX = 674;
  const priceTop = 12;
  const priceHeight = 150;
  const volumeTop = 184;
  const volumeHeight = 38;
  const xPadding = 18;
  const step = (plotRight - xPadding) / Math.max(1, candles.length - 1);
  const candleWidth = Math.max(3, Math.min(9, step * 0.52));
  const support = chart.support == null ? null : toNumber(chart.support);
  const resistance = chart.resistance == null ? null : toNumber(chart.resistance);
  const trend = chart.trend_line
    ? {
        start: toNumber(chart.trend_line.start_value),
        end: toNumber(chart.trend_line.end_value),
        slope: toNumber(chart.trend_line.slope_per_session),
      }
    : null;
  const smaPoints = chart.sma_20
    .map((point) => ({
      x: xForDate(candles, point.date, step, xPadding),
      value: toNumber(point.value),
    }))
    .filter((point) => point.x !== null && Number.isFinite(point.value));
  const priceValues = [
    ...candles.flatMap((candle) => [candle.high, candle.low]),
    ...smaPoints.map((point) => point.value),
    support,
    resistance,
    trend?.start,
    trend?.end,
  ].filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const minPrice = Math.min(...priceValues);
  const maxPrice = Math.max(...priceValues);
  const priceRange = maxPrice - minPrice || Math.max(1, maxPrice * 0.02);
  const paddedMin = minPrice - priceRange * 0.08;
  const paddedMax = maxPrice + priceRange * 0.08;
  const maxVolume = Math.max(...candles.map((candle) => candle.volume), 1);
  const yForPrice = (value: number) =>
    priceTop + ((paddedMax - value) / (paddedMax - paddedMin)) * priceHeight;
  const xForIndex = (index: number) => xPadding + index * step;
  const latest = candles[candles.length - 1];
  const first = candles[0];
  const periodReturn = ((latest.close - first.close) / first.close) * 100;
  const trendLabel =
    trend && Number.isFinite(trend.slope)
      ? `${trend.slope >= 0 ? "+" : ""}${formatCompactNumber(trend.slope)} / session`
      : "n/a";
  const strokeBase = tone === "red" ? "stroke-red-900" : "stroke-slate-800";
  const textBase = tone === "red" ? "text-red-100" : "text-slate-200";
  const mutedText = tone === "red" ? "text-red-200" : "text-slate-400";
  const borderBase = tone === "red" ? "border-red-900" : "border-slate-800";
  const smaPath = linePath(smaPoints.map((point) => [point.x ?? 0, yForPrice(point.value)]));
  const trendPath =
    trend && Number.isFinite(trend.start) && Number.isFinite(trend.end)
      ? `M ${xForIndex(0)} ${yForPrice(trend.start)} L ${xForIndex(
          candles.length - 1,
        )} ${yForPrice(trend.end)}`
      : "";
  const priceTicks = [paddedMax, paddedMin + (paddedMax - paddedMin) / 2, paddedMin];
  const hovered =
    hoveredIndex == null || !candles[hoveredIndex] ? null : candles[hoveredIndex];
  const hoverX = hoveredIndex == null ? 0 : xForIndex(hoveredIndex);
  const tooltipX = Math.min(Math.max(hoverX - 68, xPadding), plotRight - 136);

  return (
    <div className={`mt-4 border-t ${borderBase} pt-4`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className={`text-xs uppercase ${mutedText}`}>Static price chart</div>
          <div className={`mt-1 text-sm ${textBase}`}>
            {chart.period_start} to {chart.period_end}
            {chart.currency ? ` · ${chart.currency}` : ""}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-x-5 gap-y-1 text-xs md:grid-cols-4">
          <ChartStat label="Close" value={formatPrice(latest.close)} tone={tone} />
          <ChartStat
            label="Return"
            value={`${periodReturn >= 0 ? "+" : ""}${periodReturn.toFixed(1)}%`}
            tone={tone}
          />
          <ChartStat
            label="Support"
            value={support == null ? "n/a" : formatPrice(support)}
            tone={tone}
          />
          <ChartStat label="Trend" value={trendLabel} tone={tone} />
        </div>
      </div>

      <svg
        viewBox={`0 0 ${width} 240`}
        role="img"
        aria-label={`Static OHLCV chart from ${chart.period_start} to ${chart.period_end}`}
        className="h-56 w-full overflow-visible"
        preserveAspectRatio="none"
        onMouseLeave={() => setHoveredIndex(null)}
      >
        <line x1="0" y1={priceTop} x2={plotRight} y2={priceTop} className={strokeBase} />
        <line
          x1="0"
          y1={priceTop + priceHeight}
          x2={plotRight}
          y2={priceTop + priceHeight}
          className={strokeBase}
        />
        <line x1="0" y1={volumeTop} x2={plotRight} y2={volumeTop} className={strokeBase} />
        <line x1={plotRight} y1={priceTop} x2={plotRight} y2={priceTop + priceHeight} className={strokeBase} />
        {priceTicks.map((tick) => (
          <g key={tick.toFixed(4)}>
            <line
              x1="0"
              y1={yForPrice(tick)}
              x2={plotRight}
              y2={yForPrice(tick)}
              className={strokeBase}
              opacity="0.45"
            />
            <text
              x={yAxisX}
              y={yForPrice(tick) + 4}
              className={tone === "red" ? "fill-red-200 text-[10px]" : "fill-slate-400 text-[10px]"}
            >
              {formatPrice(tick)}
            </text>
          </g>
        ))}

        {support != null && (
          <ReferenceLine
            y={yForPrice(support)}
            label="support"
            width={plotRight}
            color="stroke-sky-400"
          />
        )}
        {resistance != null && (
          <ReferenceLine
            y={yForPrice(resistance)}
            label="resistance"
            width={plotRight}
            color="stroke-amber-300"
          />
        )}
        {trendPath && (
          <path d={trendPath} fill="none" className="stroke-violet-300" strokeWidth="2" />
        )}
        {smaPath && (
          <path d={smaPath} fill="none" className="stroke-cyan-300" strokeWidth="2" />
        )}

        {candles.map((candle, index) => {
          const x = xForIndex(index);
          const bodyTop = yForPrice(Math.max(candle.open, candle.close));
          const bodyBottom = yForPrice(Math.min(candle.open, candle.close));
          const isUp = candle.close >= candle.open;
          const bodyHeight = Math.max(2, bodyBottom - bodyTop);
          const volumeHeightValue = (candle.volume / maxVolume) * volumeHeight;
          return (
            <g key={`${candle.date}-${index}`} onMouseEnter={() => setHoveredIndex(index)}>
              <line
                x1={x}
                y1={yForPrice(candle.high)}
                x2={x}
                y2={yForPrice(candle.low)}
                className={isUp ? "stroke-emerald-300" : "stroke-red-300"}
                strokeWidth="1.5"
              />
              <rect
                x={x - candleWidth / 2}
                y={bodyTop}
                width={candleWidth}
                height={bodyHeight}
                rx="1"
                className={isUp ? "fill-emerald-300" : "fill-red-300"}
              />
              <rect
                x={x - candleWidth / 2}
                y={volumeTop + volumeHeight - volumeHeightValue}
                width={candleWidth}
                height={Math.max(1, volumeHeightValue)}
                className={isUp ? "fill-emerald-700" : "fill-red-700"}
                opacity="0.6"
              />
              <rect
                x={x - Math.max(step / 2, candleWidth)}
                y={priceTop}
                width={Math.max(step, candleWidth + 4)}
                height={volumeTop + volumeHeight - priceTop}
                fill="transparent"
              />
            </g>
          );
        })}
        {hovered && (
          <g pointerEvents="none">
            <line
              x1={hoverX}
              y1={priceTop}
              x2={hoverX}
              y2={volumeTop + volumeHeight}
              className="stroke-white"
              strokeWidth="1"
              opacity="0.45"
            />
            <rect
              x={tooltipX}
              y="18"
              width="136"
              height="74"
              className={tone === "red" ? "fill-red-950" : "fill-slate-950"}
              opacity="0.94"
            />
            <text x={tooltipX + 8} y="34" className="fill-slate-100 text-[10px]">
              {hovered.date}
            </text>
            <text x={tooltipX + 8} y="48" className="fill-slate-200 text-[10px]">
              O {formatPrice(hovered.open)} H {formatPrice(hovered.high)}
            </text>
            <text x={tooltipX + 8} y="62" className="fill-slate-200 text-[10px]">
              L {formatPrice(hovered.low)} C {formatPrice(hovered.close)}
            </text>
            <text x={tooltipX + 8} y="76" className="fill-slate-300 text-[10px]">
              Vol {formatVolume(hovered.volume)}
            </text>
          </g>
        )}
      </svg>

      <div className={`mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs ${mutedText}`}>
        <span>SMA20</span>
        <span>Trend</span>
        <span>Support {support == null ? "n/a" : formatPrice(support)}</span>
        <span>Resistance {resistance == null ? "n/a" : formatPrice(resistance)}</span>
      </div>
    </div>
  );
}

function ChartStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "slate" | "red";
}) {
  return (
    <div>
      <div className={tone === "red" ? "text-red-300" : "text-slate-500"}>{label}</div>
      <div className={tone === "red" ? "text-red-50" : "text-slate-100"}>{value}</div>
    </div>
  );
}

function ReferenceLine({
  y,
  label,
  width,
  color,
}: {
  y: number;
  label: string;
  width: number;
  color: string;
}) {
  return (
    <g>
      <line
        x1="0"
        y1={y}
        x2={width}
        y2={y}
        className={color}
        strokeWidth="1.5"
        strokeDasharray="6 5"
      />
      <text x="6" y={Math.max(11, y - 4)} className="fill-slate-300 text-[10px] uppercase">
        {label}
      </text>
    </g>
  );
}

function toNumber(value: number | string): number {
  return typeof value === "number" ? value : Number(value);
}

function xForDate(
  candles: Array<{ date: string }>,
  dateValue: string,
  step: number,
  xPadding: number,
) {
  const index = candles.findIndex((candle) => candle.date === dateValue);
  if (index < 0) return null;
  return xPadding + index * step;
}

function linePath(points: Array<[number, number]>) {
  if (points.length < 2) return "";
  return points
    .map(([x, y], index) => `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`)
    .join(" ");
}

function formatPrice(value: number) {
  return value >= 100 ? value.toFixed(1) : value.toFixed(2);
}

function formatVolume(value: number) {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toString();
}

function formatCompactNumber(value: number) {
  if (Math.abs(value) >= 1) return value.toFixed(2);
  return value.toFixed(4);
}

function formatMarketCap(value: number | string) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  if (numeric >= 1_000_000_000_000) return `$${(numeric / 1_000_000_000_000).toFixed(2)}T`;
  if (numeric >= 1_000_000_000) return `$${(numeric / 1_000_000_000).toFixed(1)}B`;
  if (numeric >= 1_000_000) return `$${(numeric / 1_000_000).toFixed(1)}M`;
  return `$${numeric.toLocaleString()}`;
}

function publicationTier(row: RecommendationQuality) {
  return row.publication_tier ?? "decision_grade";
}

function publicationTierLabel(tier: string) {
  switch (tier) {
    case "reduced_confidence":
      return "lower confidence";
    case "fallback_preview":
      return "preview only";
    case "blocked":
      return "blocked";
    default:
      return "reviewed";
  }
}

function formatSignedNumber(value: number) {
  return `${value > 0 ? "+" : ""}${value}`;
}

function totalReasonCount(entries: Array<[string, number]>) {
  return entries.reduce((total, [, count]) => total + count, 0);
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
      {dedupeText(items).slice(0, 3).map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

function SourceTraceability({
  items,
  tone,
}: {
  items: SignalSource[];
  tone: "slate" | "red";
}) {
  if (items.length === 0) return null;
  const border = tone === "red" ? "border-red-900" : "border-slate-800";
  const muted = tone === "red" ? "text-red-200" : "text-slate-400";
  const labels = items
    .map((item) => [item.provider, item.observed_at ? formatShortDate(item.observed_at) : ""].filter(Boolean).join(" · "))
    .filter(Boolean);
  if (labels.length === 0) return null;
  return (
    <section className={`mt-4 border-t ${border} pt-4`}>
      <h4 className={`text-xs font-semibold uppercase ${muted}`}>Source traceability</h4>
      <div className="mt-2 flex flex-wrap gap-2">
        {dedupeText(labels).map((label) => (
          <span key={label} className={`border ${border} px-2 py-1 text-xs ${muted}`}>
            {label}
          </span>
        ))}
      </div>
    </section>
  );
}

function RelatedNews({
  items,
  tone,
}: {
  items: RelatedNewsArticle[];
  tone: "slate" | "red";
}) {
  const border = tone === "red" ? "border-red-900" : "border-slate-800";
  const title = tone === "red" ? "text-red-100" : "text-slate-100";
  const muted = tone === "red" ? "text-red-200" : "text-slate-400";
  return (
    <section className={`mt-4 border-t ${border} pt-4`}>
      <h4 className={`text-xs font-semibold uppercase ${muted}`}>Related news</h4>
      {items.length === 0 ? (
        <p className={`mt-2 text-sm ${muted}`}>No recent ticker-related articles available.</p>
      ) : (
        <div className="mt-3 space-y-3">
          {items.slice(0, 3).map((item) => {
            const heading = item.url ? (
              <a
                href={item.url}
                target="_blank"
                rel="noreferrer"
                className={`${title} underline decoration-slate-500 underline-offset-4`}
              >
                {item.title}
              </a>
            ) : (
              <span className={title}>{item.title}</span>
            );
            return (
              <article key={`${item.source}-${item.title}`}>
                <div className="text-sm font-medium leading-5">{heading}</div>
                <p className={`mt-1 text-xs ${muted}`}>
                  {item.source}
                  {item.published_at ? ` · ${formatShortDate(item.published_at)}` : ""}
                </p>
                {item.summary && (
                  <p className={`mt-1 text-sm leading-5 ${tone === "red" ? "text-red-100" : "text-slate-300"}`}>
                    {item.summary}
                  </p>
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function UpcomingEvents({
  items,
  tone,
}: {
  items: UpcomingTickerEvent[];
  tone: "slate" | "red";
}) {
  const border = tone === "red" ? "border-red-900" : "border-slate-800";
  const muted = tone === "red" ? "text-red-200" : "text-slate-400";
  return (
    <section className={`mt-4 border-t ${border} pt-4`}>
      <h4 className={`text-xs font-semibold uppercase ${muted}`}>Upcoming events</h4>
      {items.length === 0 ? (
        <p className={`mt-2 text-sm ${muted}`}>No upcoming earnings or dividend events available.</p>
      ) : (
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {items.slice(0, 4).map((item) => (
            <div key={`${item.event_type}-${item.event_date}`} className={`border ${border} p-3`}>
              <div className="text-sm font-medium text-slate-100">{item.title}</div>
              <p className={`mt-1 text-xs ${muted}`}>
                {formatShortDate(item.event_date)}
                {item.provider ? ` · ${item.provider}` : ""}
              </p>
              {item.source_url && (
                <a
                  href={item.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className={`mt-2 inline-block text-xs underline underline-offset-4 ${muted}`}
                >
                  Source
                </a>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function dedupeText(items: string[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = item.trim().toLowerCase().replace(/\s+/g, " ");
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
