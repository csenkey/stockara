import { useCallback, useEffect, useState } from "react";
import api from "../services/api";
import StockFilters, { StockFiltersState } from "./StockFilters";

interface BuySuggestion {
  ticker: string;
  recommendation: string;
  risk_level: string;
  timeframe: string;
  confidence_score: number;
  reasoning: string | null;
}

interface StockMeta {
  ticker: string;
  company_name: string;
  sector: string;
  company_size: string;
}

interface SuggestionsResponse {
  buy_suggestions: BuySuggestion[];
  sell_suggestions: BuySuggestion[];
  analysis_date: string;
}

interface StocksResponse {
  stocks: StockMeta[];
  total: number;
}

function formatCompanySize(size: string): string {
  switch (size) {
    case "blue_chip":
      return "Blue Chip";
    case "mid_cap":
      return "Mid Cap";
    case "startup":
      return "Startup";
    default:
      return size;
  }
}

function riskBadgeColor(risk: string): string {
  switch (risk) {
    case "LOW":
      return "bg-green-100 text-green-800";
    case "MEDIUM":
      return "bg-yellow-100 text-yellow-800";
    case "HIGH":
      return "bg-red-100 text-red-800";
    default:
      return "bg-gray-100 text-gray-800";
  }
}

export default function BuySuggestions() {
  const [suggestions, setSuggestions] = useState<BuySuggestion[]>([]);
  const [stockMetaMap, setStockMetaMap] = useState<Record<string, StockMeta>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<StockFiltersState>({
    sector: "",
    companySize: "",
    maxRisk: "",
  });

  // Fetch stock metadata once
  useEffect(() => {
    async function fetchStocks() {
      try {
        const res = await api.get<StocksResponse>("/api/stocks");
        const map: Record<string, StockMeta> = {};
        for (const stock of res.data.stocks) {
          map[stock.ticker] = stock;
        }
        setStockMetaMap(map);
      } catch {
        // Non-critical: suggestions still display without metadata
      }
    }
    fetchStocks();
  }, []);

  // Fetch suggestions when filters change
  const fetchSuggestions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {};
      if (filters.sector) params.sector = filters.sector;
      if (filters.companySize) params.company_size = filters.companySize;
      if (filters.maxRisk) params.max_risk = filters.maxRisk;

      const res = await api.get<SuggestionsResponse>("/api/suggestions", {
        params,
      });
      setSuggestions(res.data.buy_suggestions.slice(0, 20));
    } catch {
      setError("Failed to load suggestions. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    fetchSuggestions();
  }, [fetchSuggestions]);

  function handleFiltersChange(newFilters: StockFiltersState) {
    setFilters(newFilters);
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
      <h2 className="text-lg font-semibold text-gray-900 mb-4">
        Buy Suggestions
      </h2>

      <StockFilters filters={filters} onChange={handleFiltersChange} />

      <div className="mt-4">
        {loading && (
          <p className="text-sm text-gray-500 py-4 text-center">
            Loading suggestions...
          </p>
        )}

        {error && (
          <p className="text-sm text-red-600 py-4 text-center">{error}</p>
        )}

        {!loading && !error && suggestions.length === 0 && (
          <p className="text-sm text-gray-500 py-4 text-center">
            No suggestions match your criteria
          </p>
        )}

        {!loading && !error && suggestions.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  <th className="py-2 pr-4">Ticker</th>
                  <th className="py-2 pr-4">Company</th>
                  <th className="py-2 pr-4">Sector</th>
                  <th className="py-2 pr-4">Size</th>
                  <th className="py-2">Risk</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {suggestions.map((suggestion) => {
                  const meta = stockMetaMap[suggestion.ticker];
                  return (
                    <tr key={suggestion.ticker} className="hover:bg-gray-50">
                      <td className="py-2 pr-4 font-medium text-gray-900">
                        {suggestion.ticker}
                      </td>
                      <td className="py-2 pr-4 text-gray-700">
                        {meta?.company_name ?? "—"}
                      </td>
                      <td className="py-2 pr-4 text-gray-700">
                        {meta?.sector ?? "—"}
                      </td>
                      <td className="py-2 pr-4 text-gray-700">
                        {meta ? formatCompanySize(meta.company_size) : "—"}
                      </td>
                      <td className="py-2">
                        <span
                          className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${riskBadgeColor(suggestion.risk_level)}`}
                        >
                          {suggestion.risk_level}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
