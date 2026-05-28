import { useState, FormEvent, useEffect, useRef } from "react";
import { AxiosError } from "axios";
import api from "../services/api";

interface AddStockModalProps {
  isOpen: boolean;
  onClose: () => void;
  onStockAdded: () => void;
}

interface FormErrors {
  ticker?: string;
  quantity?: string;
  buying_price?: string;
}

export default function AddStockModal({
  isOpen,
  onClose,
  onStockAdded,
}: AddStockModalProps) {
  const [ticker, setTicker] = useState("");
  const [quantity, setQuantity] = useState("");
  const [buyingPrice, setBuyingPrice] = useState("");
  const [errors, setErrors] = useState<FormErrors>({});
  const [serverError, setServerError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const tickerInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setTicker("");
      setQuantity("");
      setBuyingPrice("");
      setErrors({});
      setServerError("");
      // Focus the ticker input when modal opens
      setTimeout(() => tickerInputRef.current?.focus(), 0);
    }
  }, [isOpen]);

  // Close on Escape key
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && isOpen) {
        onClose();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  function validate(): boolean {
    const newErrors: FormErrors = {};

    if (!ticker.trim()) {
      newErrors.ticker = "Ticker is required";
    }

    const qty = Number(quantity);
    if (!quantity || isNaN(qty) || qty <= 0 || !Number.isInteger(qty)) {
      newErrors.quantity = "Quantity must be a positive integer";
    }

    const price = Number(buyingPrice);
    if (!buyingPrice || isNaN(price) || price <= 0) {
      newErrors.buying_price = "Buying price must be a positive number";
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setServerError("");

    if (!validate()) return;

    setIsLoading(true);
    try {
      await api.put("/api/portfolio/stocks", {
        ticker: ticker.trim().toUpperCase(),
        quantity: Number(quantity),
        buying_price: Number(buyingPrice),
      });
      onStockAdded();
      onClose();
    } catch (err) {
      const axiosError = err as AxiosError<{ detail?: string }>;
      const detail = axiosError.response?.data?.detail;
      setServerError(detail || "Failed to add stock. Please try again.");
    } finally {
      setIsLoading(false);
    }
  }

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-stock-modal-title"
    >
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50"
        aria-hidden="true"
        onClick={onClose}
      />

      {/* Modal content */}
      <div className="relative bg-white rounded-lg shadow-xl w-full max-w-md mx-4 p-6">
        <h2
          id="add-stock-modal-title"
          className="text-lg font-semibold text-gray-900 mb-4"
        >
          Add Stock to Portfolio
        </h2>

        {serverError && (
          <div
            className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm"
            role="alert"
          >
            {serverError}
          </div>
        )}

        <form onSubmit={handleSubmit} noValidate>
          <div className="mb-4">
            <label
              htmlFor="add-stock-ticker"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Ticker Symbol
            </label>
            <input
              ref={tickerInputRef}
              id="add-stock-ticker"
              type="text"
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                errors.ticker ? "border-red-500" : "border-gray-300"
              }`}
              placeholder="e.g. AAPL"
              aria-invalid={!!errors.ticker}
              aria-describedby={errors.ticker ? "ticker-error" : undefined}
            />
            {errors.ticker && (
              <p id="ticker-error" className="mt-1 text-sm text-red-600">
                {errors.ticker}
              </p>
            )}
          </div>

          <div className="mb-4">
            <label
              htmlFor="add-stock-quantity"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Quantity
            </label>
            <input
              id="add-stock-quantity"
              type="number"
              min="1"
              step="1"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                errors.quantity ? "border-red-500" : "border-gray-300"
              }`}
              placeholder="Number of shares"
              aria-invalid={!!errors.quantity}
              aria-describedby={errors.quantity ? "quantity-error" : undefined}
            />
            {errors.quantity && (
              <p id="quantity-error" className="mt-1 text-sm text-red-600">
                {errors.quantity}
              </p>
            )}
          </div>

          <div className="mb-6">
            <label
              htmlFor="add-stock-price"
              className="block text-sm font-medium text-gray-700 mb-1"
            >
              Buying Price
            </label>
            <input
              id="add-stock-price"
              type="number"
              min="0.01"
              step="0.01"
              value={buyingPrice}
              onChange={(e) => setBuyingPrice(e.target.value)}
              className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                errors.buying_price ? "border-red-500" : "border-gray-300"
              }`}
              placeholder="Price per share"
              aria-invalid={!!errors.buying_price}
              aria-describedby={
                errors.buying_price ? "price-error" : undefined
              }
            />
            {errors.buying_price && (
              <p id="price-error" className="mt-1 text-sm text-red-600">
                {errors.buying_price}
              </p>
            )}
          </div>

          <div className="flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-md hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-gray-500"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isLoading}
              className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isLoading ? "Adding..." : "Add Stock"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
