import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { LineChart, Line } from "recharts";
import api from "../services/api";

interface LeaderboardEntry {
  rank: number;
  account_name: string;
  portfolio_value: number;
  cash_balance: number;
  gain_loss_pct: number;
  transaction_count: number;
  sparkline_data: number[];
}

interface LeaderboardResponse {
  entries: LeaderboardEntry[];
  last_updated: string;
}

export default function DemoLeaderboard() {
  const [data, setData] = useState<LeaderboardResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    async function fetchLeaderboard() {
      try {
        const response = await api.get<LeaderboardResponse>(
          "/api/demo/leaderboard"
        );
        setData(response.data);
        setError("");
      } catch {
        setError("Failed to load leaderboard.");
      } finally {
        setIsLoading(false);
      }
    }
    fetchLeaderboard();
  }, []);

  function handleRowClick(name: string) {
    navigate(`/demo/${encodeURIComponent(name)}`);
  }

  function formatCurrency(value: number): string {
    return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  function formatPercent(value: number): string {
    const sign = value >= 0 ? "+" : "";
    return `${sign}${value.toFixed(2)}%`;
  }

  function formatTimestamp(iso: string): string {
    return new Date(iso).toLocaleString();
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">
          Demo Trading Leaderboard
        </h1>
        {data?.last_updated && (
          <span className="text-sm text-gray-500">
            Last updated: {formatTimestamp(data.last_updated)}
          </span>
        )}
      </div>

      {error && (
        <div
          className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm"
          role="alert"
        >
          {error}
        </div>
      )}

      {isLoading ? (
        <p className="text-gray-500">Loading leaderboard...</p>
      ) : data && data.entries.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="py-3 px-4 text-sm font-medium text-gray-600">
                  Rank
                </th>
                <th className="py-3 px-4 text-sm font-medium text-gray-600">
                  Name
                </th>
                <th className="py-3 px-4 text-sm font-medium text-gray-600">
                  Portfolio Value
                </th>
                <th className="py-3 px-4 text-sm font-medium text-gray-600">
                  Cash Balance
                </th>
                <th className="py-3 px-4 text-sm font-medium text-gray-600">
                  Gain/Loss
                </th>
                <th className="py-3 px-4 text-sm font-medium text-gray-600">
                  Transactions
                </th>
                <th className="py-3 px-4 text-sm font-medium text-gray-600">
                  Trend
                </th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map((entry) => (
                <tr
                  key={entry.account_name}
                  onClick={() => handleRowClick(entry.account_name)}
                  className="border-b border-gray-100 hover:bg-gray-50 cursor-pointer"
                >
                  <td className="py-3 px-4 text-sm font-medium text-gray-900">
                    {entry.rank}
                  </td>
                  <td className="py-3 px-4 text-sm font-medium text-gray-900">
                    {entry.account_name}
                  </td>
                  <td className="py-3 px-4 text-sm text-gray-700">
                    {formatCurrency(entry.portfolio_value)}
                  </td>
                  <td className="py-3 px-4 text-sm text-gray-700">
                    {formatCurrency(entry.cash_balance)}
                  </td>
                  <td
                    className={`py-3 px-4 text-sm font-medium ${
                      entry.gain_loss_pct >= 0
                        ? "text-green-600"
                        : "text-red-600"
                    }`}
                  >
                    {formatPercent(entry.gain_loss_pct)}
                  </td>
                  <td className="py-3 px-4 text-sm text-gray-700">
                    {entry.transaction_count}
                  </td>
                  <td className="py-3 px-4">
                    <Sparkline
                      data={entry.sparkline_data}
                      positive={entry.gain_loss_pct >= 0}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-gray-500">No leaderboard data available.</p>
      )}
    </div>
  );
}

function Sparkline({
  data,
  positive,
}: {
  data: number[];
  positive: boolean;
}) {
  const chartData = data.map((value) => ({ value }));
  const color = positive ? "#16a34a" : "#dc2626";

  return (
    <LineChart width={100} height={30} data={chartData}>
      <Line
        type="monotone"
        dataKey="value"
        stroke={color}
        strokeWidth={1.5}
        dot={false}
        isAnimationActive={false}
      />
    </LineChart>
  );
}
