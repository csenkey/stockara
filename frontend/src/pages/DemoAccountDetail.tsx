import { useState, useEffect, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from "recharts";
import api from "../services/api";

interface DemoHolding {
  ticker: string;
  quantity: number;
  purchase_price: number;
  current_price: number | null;
  unrealized_gain_loss: number | null;
}

interface AllocationEntry {
  label: string;
  value: number;
  percentage: number;
}

interface AccountDetail {
  account_name: string;
  portfolio_value: number;
  cash_balance: number;
  total_gain_loss: number;
  gain_loss_pct: number;
  holdings: DemoHolding[];
  allocation: AllocationEntry[];
}

interface DailySnapshot {
  snapshot_date: string;
  portfolio_value: number;
  cash_balance: number;
  holdings_value: number;
}

interface PerformanceResponse {
  account_name: string;
  data_points: DailySnapshot[];
  initial_value: number;
}

interface DemoTransaction {
  id: number;
  ticker: string;
  action: "BUY" | "SELL";
  quantity: number;
  price_per_share: number;
  total_value: number;
  commission_fee: number;
  cash_after: number;
  executed_at: string;
}

interface PaginatedTransactions {
  transactions: DemoTransaction[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

const PIE_COLORS = [
  "#3b82f6",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#ec4899",
  "#06b6d4",
  "#84cc16",
  "#f97316",
  "#6366f1",
  "#14b8a6",
  "#a855f7",
];

export default function DemoAccountDetail() {
  const { name } = useParams<{ name: string }>();
  const [account, setAccount] = useState<AccountDetail | null>(null);
  const [performance, setPerformance] = useState<PerformanceResponse | null>(null);
  const [transactions, setTransactions] = useState<PaginatedTransactions | null>(null);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!name) return;
    async function fetchData() {
      setIsLoading(true);
      try {
        const [accountRes, perfRes] = await Promise.all([
          api.get<AccountDetail>(`/api/demo/accounts/${encodeURIComponent(name!)}`),
          api.get<PerformanceResponse>(`/api/demo/accounts/${encodeURIComponent(name!)}/performance`),
        ]);
        setAccount(accountRes.data);
        setPerformance(perfRes.data);
        setError("");
      } catch {
        setError("Failed to load account details.");
      } finally {
        setIsLoading(false);
      }
    }
    fetchData();
  }, [name]);

  const fetchTransactions = useCallback(async (pageNum: number) => {
    if (!name) return;
    try {
      const res = await api.get<PaginatedTransactions>(
        `/api/demo/accounts/${encodeURIComponent(name)}/transactions?page=${pageNum}&page_size=20`
      );
      setTransactions(res.data);
    } catch {
      // Silently fail for transactions - account detail still shows
    }
  }, [name]);

  useEffect(() => {
    fetchTransactions(page);
  }, [page, fetchTransactions]);

  function formatCurrency(value: number): string {
    return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  function formatPercent(value: number): string {
    const sign = value >= 0 ? "+" : "";
    return `${sign}${value.toFixed(2)}%`;
  }

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString();
  }

  function formatDateTime(iso: string): string {
    return new Date(iso).toLocaleString();
  }

  if (isLoading) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-8">
        <p className="text-gray-500">Loading account details...</p>
      </div>
    );
  }

  if (error || !account) {
    return (
      <div className="max-w-7xl mx-auto px-4 py-8">
        <Link to="/demo" className="text-blue-600 hover:text-blue-800 text-sm mb-4 inline-block">
          ← Back to Leaderboard
        </Link>
        <div className="p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm" role="alert">
          {error || "Account not found."}
        </div>
      </div>
    );
  }

  const chartData = performance?.data_points.map((dp) => ({
    date: dp.snapshot_date,
    portfolio_value: dp.portfolio_value,
  })) ?? [];

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      {/* Back navigation */}
      <Link to="/demo" className="text-blue-600 hover:text-blue-800 text-sm mb-4 inline-block">
        ← Back to Leaderboard
      </Link>

      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900 mb-2">{account.account_name}</h1>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Portfolio Value</p>
            <p className="text-lg font-semibold text-gray-900">{formatCurrency(account.portfolio_value)}</p>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Cash Balance</p>
            <p className="text-lg font-semibold text-gray-900">{formatCurrency(account.cash_balance)}</p>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Total Gain/Loss</p>
            <p className={`text-lg font-semibold ${account.total_gain_loss >= 0 ? "text-green-600" : "text-red-600"}`}>
              {formatCurrency(account.total_gain_loss)}
            </p>
          </div>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <p className="text-sm text-gray-500">Return</p>
            <p className={`text-lg font-semibold ${account.gain_loss_pct >= 0 ? "text-green-600" : "text-red-600"}`}>
              {formatPercent(account.gain_loss_pct)}
            </p>
          </div>
        </div>
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
        {/* Portfolio value line chart */}
        <div className="lg:col-span-2 bg-white border border-gray-200 rounded-lg p-4">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Portfolio Value Over Time</h2>
          {chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tick={{ fontSize: 12 }} tickFormatter={formatDate} />
                <YAxis tick={{ fontSize: 12 }} tickFormatter={(v: number) => `$${(v / 1000).toFixed(1)}k`} />
                <Tooltip
                  formatter={(value) => [formatCurrency(Number(value)), "Portfolio Value"]}
                  labelFormatter={(label) => formatDate(String(label))}
                />
                <ReferenceLine
                  y={10000}
                  stroke="#9ca3af"
                  strokeDasharray="5 5"
                  label={{ value: "$10K Start", position: "right", fontSize: 12 }}
                />
                <Line
                  type="monotone"
                  dataKey="portfolio_value"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-gray-500 text-sm">No performance data available yet.</p>
          )}
        </div>

        {/* Pie chart */}
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Portfolio Composition</h2>
          {account.allocation.length > 0 ? (
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie
                  data={account.allocation}
                  dataKey="value"
                  nameKey="label"
                  cx="50%"
                  cy="50%"
                  outerRadius={90}
                  label={(props: any) => `${props.name || ''} (${((props.percent || 0) * 100).toFixed(1)}%)`}
                  labelLine={false}
                >
                  {account.allocation.map((_, index) => (
                    <Cell key={`cell-${index}`} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Legend />
                <Tooltip formatter={(value) => formatCurrency(Number(value))} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-gray-500 text-sm">No allocation data available.</p>
          )}
        </div>
      </div>

      {/* Holdings table */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 mb-8">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Current Holdings</h2>
        {account.holdings.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="py-2 px-3 text-sm font-medium text-gray-600">Ticker</th>
                  <th className="py-2 px-3 text-sm font-medium text-gray-600">Quantity</th>
                  <th className="py-2 px-3 text-sm font-medium text-gray-600">Purchase Price</th>
                  <th className="py-2 px-3 text-sm font-medium text-gray-600">Current Price</th>
                  <th className="py-2 px-3 text-sm font-medium text-gray-600">Unrealized P&L</th>
                </tr>
              </thead>
              <tbody>
                {account.holdings.map((h) => (
                  <tr key={h.ticker} className="border-b border-gray-100">
                    <td className="py-2 px-3 text-sm font-medium text-gray-900">{h.ticker}</td>
                    <td className="py-2 px-3 text-sm text-gray-700">{h.quantity}</td>
                    <td className="py-2 px-3 text-sm text-gray-700">{formatCurrency(h.purchase_price)}</td>
                    <td className="py-2 px-3 text-sm text-gray-700">
                      {h.current_price != null ? formatCurrency(h.current_price) : "—"}
                    </td>
                    <td className={`py-2 px-3 text-sm font-medium ${
                      h.unrealized_gain_loss != null && h.unrealized_gain_loss >= 0
                        ? "text-green-600"
                        : "text-red-600"
                    }`}>
                      {h.unrealized_gain_loss != null ? formatCurrency(h.unrealized_gain_loss) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-gray-500 text-sm">No holdings.</p>
        )}
      </div>

      {/* Transaction history */}
      <div className="bg-white border border-gray-200 rounded-lg p-4">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Transaction History</h2>
        {transactions && transactions.transactions.length > 0 ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-gray-200">
                    <th className="py-2 px-3 text-sm font-medium text-gray-600">Date</th>
                    <th className="py-2 px-3 text-sm font-medium text-gray-600">Ticker</th>
                    <th className="py-2 px-3 text-sm font-medium text-gray-600">Action</th>
                    <th className="py-2 px-3 text-sm font-medium text-gray-600">Quantity</th>
                    <th className="py-2 px-3 text-sm font-medium text-gray-600">Price</th>
                    <th className="py-2 px-3 text-sm font-medium text-gray-600">Total Value</th>
                    <th className="py-2 px-3 text-sm font-medium text-gray-600">Commission</th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.transactions.map((txn) => (
                    <tr key={txn.id} className="border-b border-gray-100">
                      <td className="py-2 px-3 text-sm text-gray-700">{formatDateTime(txn.executed_at)}</td>
                      <td className="py-2 px-3 text-sm font-medium text-gray-900">{txn.ticker}</td>
                      <td className={`py-2 px-3 text-sm font-medium ${
                        txn.action === "BUY" ? "text-green-600" : "text-red-600"
                      }`}>
                        {txn.action}
                      </td>
                      <td className="py-2 px-3 text-sm text-gray-700">{txn.quantity}</td>
                      <td className="py-2 px-3 text-sm text-gray-700">{formatCurrency(txn.price_per_share)}</td>
                      <td className="py-2 px-3 text-sm text-gray-700">{formatCurrency(txn.total_value)}</td>
                      <td className="py-2 px-3 text-sm text-gray-700">{formatCurrency(txn.commission_fee)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="flex items-center justify-between mt-4">
              <p className="text-sm text-gray-500">
                Page {transactions.page} of {transactions.total_pages} ({transactions.total} total)
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Previous
                </button>
                <button
                  onClick={() => setPage((p) => Math.min(transactions!.total_pages, p + 1))}
                  disabled={page >= transactions.total_pages}
                  className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Next
                </button>
              </div>
            </div>
          </>
        ) : (
          <p className="text-gray-500 text-sm">No transactions recorded yet.</p>
        )}
      </div>
    </div>
  );
}
