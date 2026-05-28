import { useMemo } from "react";

export interface Holding {
  ticker: string;
  quantity: number;
  buying_price: number;
  added_date?: string;
}

export interface StockInfo {
  ticker: string;
  company_name: string;
  sector: string;
  company_size: string;
}

export interface SellSuggestion {
  ticker: string;
  recommendation: string;
  risk_level: string;
  timeframe: string;
}

interface PortfolioTableProps {
  holdings: Holding[];
  stocksInfo: StockInfo[];
  sellSuggestions: SellSuggestion[];
  latestPrices: Record<string, number>;
}

export default function PortfolioTable({
  holdings,
  stocksInfo,
  sellSuggestions,
  latestPrices,
}: PortfolioTableProps) {
  const stockInfoMap = useMemo(() => {
    const map: Record<string, StockInfo> = {};
    for (const stock of stocksInfo) {
      map[stock.ticker] = stock;
    }
    return map;
  }, [stocksInfo]);

  const sellTickers = useMemo(() => {
    return new Set(sellSuggestions.map((s) => s.ticker));
  }, [sellSuggestions]);

  if (holdings.length === 0) {
    return (
      <div className="text-center py-8 text-gray-500">
        <p>No holdings in your portfolio yet.</p>
        <p className="text-sm mt-1">Add stocks to get started.</p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
              Ticker
            </th>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
              Company Name
            </th>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
              Sector
            </th>
            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
              Size
            </th>
            <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
              Buying Price
            </th>
            <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
              Profit/Loss
            </th>
            <th className="px-6 py-3 text-center text-xs font-medium text-gray-500 uppercase tracking-wider">
              Signal
            </th>
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-gray-200">
          {holdings.map((holding) => {
            const info = stockInfoMap[holding.ticker];
            const latestPrice = latestPrices[holding.ticker];
            const profitLoss =
              latestPrice != null
                ? (latestPrice - holding.buying_price) * holding.quantity
                : null;
            const isSell = sellTickers.has(holding.ticker);

            return (
              <tr key={holding.ticker} className="hover:bg-gray-50">
                <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                  {holding.ticker}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">
                  {info?.company_name ?? "—"}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                  {info?.sector ?? "—"}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 capitalize">
                  {info?.company_size?.replace("_", " ") ?? "—"}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700 text-right">
                  ${holding.buying_price.toFixed(2)}
                </td>
                <td
                  className={`px-6 py-4 whitespace-nowrap text-sm font-medium text-right ${
                    profitLoss == null
                      ? "text-gray-400"
                      : profitLoss >= 0
                      ? "text-green-600"
                      : "text-red-600"
                  }`}
                >
                  {profitLoss == null
                    ? "N/A"
                    : `${profitLoss >= 0 ? "+" : ""}$${profitLoss.toFixed(2)}`}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-center">
                  {isSell && (
                    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
                      Sell
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
