import { useState, useEffect } from "react";
import { AxiosError } from "axios";
import api from "../services/api";

interface DeleteStockDialogProps {
  isOpen: boolean;
  ticker: string;
  onClose: () => void;
  onStockDeleted: () => void;
}

export default function DeleteStockDialog({
  isOpen,
  ticker,
  onClose,
  onStockDeleted,
}: DeleteStockDialogProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isOpen) {
      setError("");
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

  async function handleConfirmDelete() {
    setIsLoading(true);
    setError("");
    try {
      await api.delete(`/api/portfolio/stocks/${ticker}`);
      onStockDeleted();
      onClose();
    } catch (err) {
      const axiosError = err as AxiosError<{ detail?: string }>;
      const detail = axiosError.response?.data?.detail;
      setError(detail || "Failed to remove stock. Please try again.");
    } finally {
      setIsLoading(false);
    }
  }

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="delete-stock-dialog-title"
      aria-describedby="delete-stock-dialog-description"
    >
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50"
        aria-hidden="true"
        onClick={onClose}
      />

      {/* Dialog content */}
      <div className="relative bg-white rounded-lg shadow-xl w-full max-w-sm mx-4 p-6">
        <h2
          id="delete-stock-dialog-title"
          className="text-lg font-semibold text-gray-900 mb-2"
        >
          Remove Stock
        </h2>
        <p
          id="delete-stock-dialog-description"
          className="text-sm text-gray-600 mb-4"
        >
          Are you sure you want to remove <strong>{ticker}</strong> from your
          portfolio? This action cannot be undone.
        </p>

        {error && (
          <div
            className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded text-sm"
            role="alert"
          >
            {error}
          </div>
        )}

        <div className="flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-md hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-gray-500"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleConfirmDelete}
            disabled={isLoading}
            className="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-md hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isLoading ? "Removing..." : "Remove"}
          </button>
        </div>
      </div>
    </div>
  );
}
