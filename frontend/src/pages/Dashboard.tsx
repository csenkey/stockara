import { useState, useEffect, useCallback } from "react";
import { AxiosError } from "axios";
import api from "../services/api";
import AddStockModal from "../components/AddStockModal";
import DeleteStockDialog from "../components/DeleteStockDialog";

interface Holding {
  ticker: string;
  quantity: number;
  buying_price: number;
  added_date?: string;
}

export default function Dashboard() {
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  // Add stock modal state
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);

  // Delete dialog state
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const fetchPortfolio = useCallback(async () => {
    try {
      const response = await api.get("/api/portfolio");
      setHoldings(response.data.holdings || []);
      setError("");
    } catch (err) {
      const axiosError = err as AxiosError<{ detail?: string }>;
      if (axiosError.response?.status !== 401) {
        setError("Failed to load portfolio.");
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPortfolio();
  }, [fetchPortfolio]);

  function handleStockAdded() {
    fetchPortfolio();
  }

  function handleStockDeleted() {
    fetchPortfolio();
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-bold text-gray-900">Portfolio</h2>
        <button
          onClick={() => setIsAddModalOpen(true)}
          className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
        >
          + Add Stock
        </button>
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
        <p className="text-gray-500">Loading portfolio...</p>
      ) : holdings.length === 0 ? (
        <p className="text-gray-500">
          No stocks in your portfolio yet. Click &quot;+ Add Stock&quot; to get
          started.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="py-3 px-4 text-sm font-medium text-gray-600">
                  Ticker
                </th>
                <th className="py-3 px-4 text-sm font-medium text-gray-600">
                  Quantity
                </th>
                <th className="py-3 px-4 text-sm font-medium text-gray-600">
                  Buying Price
                </th>
                <th className="py-3 px-4 text-sm font-medium text-gray-600">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {holdings.map((holding) => (
                <tr
                  key={holding.ticker}
                  className="border-b border-gray-100 hover:bg-gray-50"
                >
                  <td className="py-3 px-4 text-sm font-medium text-gray-900">
                    {holding.ticker}
                  </td>
                  <td className="py-3 px-4 text-sm text-gray-700">
                    {holding.quantity}
                  </td>
                  <td className="py-3 px-4 text-sm text-gray-700">
                    ${holding.buying_price.toFixed(2)}
                  </td>
                  <td className="py-3 px-4">
                    <button
                      onClick={() => setDeleteTarget(holding.ticker)}
                      className="text-sm text-red-600 hover:text-red-800 font-medium focus:outline-none focus:underline"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <AddStockModal
        isOpen={isAddModalOpen}
        onClose={() => setIsAddModalOpen(false)}
        onStockAdded={handleStockAdded}
      />

      <DeleteStockDialog
        isOpen={deleteTarget !== null}
        ticker={deleteTarget || ""}
        onClose={() => setDeleteTarget(null)}
        onStockDeleted={handleStockDeleted}
      />
    </div>
  );
}
