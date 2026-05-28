import { useState, useEffect } from "react";
import api from "../services/api";

const SECTORS = [
  "Technology",
  "Healthcare",
  "Finance",
  "Energy",
  "Consumer Discretionary",
  "Consumer Staples",
  "Industrials",
  "Materials",
  "Utilities",
  "Real Estate",
  "Communication Services",
  "Telecommunications",
];

const SIZES = [
  { value: "blue_chip", label: "Blue Chip" },
  { value: "mid_cap", label: "Mid Cap" },
  { value: "startup", label: "Startup" },
];

const RISK_LEVELS = ["LOW", "MEDIUM", "HIGH"];

interface Preferences {
  preferred_sectors: string[];
  preferred_sizes: string[];
  max_risk_level: string;
}

export default function Settings() {
  const [preferences, setPreferences] = useState<Preferences>({
    preferred_sectors: [],
    preferred_sizes: [],
    max_risk_level: "HIGH",
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [successMessage, setSuccessMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    async function fetchPreferences() {
      try {
        const response = await api.get("/api/preferences");
        setPreferences(response.data);
      } catch {
        setError("Failed to load preferences");
      } finally {
        setLoading(false);
      }
    }
    fetchPreferences();
  }, []);

  function toggleSector(sector: string) {
    setPreferences((prev) => ({
      ...prev,
      preferred_sectors: prev.preferred_sectors.includes(sector)
        ? prev.preferred_sectors.filter((s) => s !== sector)
        : [...prev.preferred_sectors, sector],
    }));
  }

  function toggleSize(size: string) {
    setPreferences((prev) => ({
      ...prev,
      preferred_sizes: prev.preferred_sizes.includes(size)
        ? prev.preferred_sizes.filter((s) => s !== size)
        : [...prev.preferred_sizes, size],
    }));
  }

  async function handleSave() {
    setSaving(true);
    setSuccessMessage("");
    setError("");
    try {
      await api.put("/api/preferences", preferences);
      setSuccessMessage("Preferences saved successfully");
    } catch {
      setError("Failed to save preferences");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center p-8">
        <p className="text-gray-500">Loading preferences...</p>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto p-6">
      <h1 className="text-2xl font-bold mb-6">Settings</h1>

      {successMessage && (
        <div className="mb-4 p-3 bg-green-100 text-green-800 rounded">
          {successMessage}
        </div>
      )}
      {error && (
        <div className="mb-4 p-3 bg-red-100 text-red-800 rounded">
          {error}
        </div>
      )}

      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-3">Preferred Sectors</h2>
        <div className="grid grid-cols-2 gap-2">
          {SECTORS.map((sector) => (
            <label key={sector} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={preferences.preferred_sectors.includes(sector)}
                onChange={() => toggleSector(sector)}
                className="rounded border-gray-300"
              />
              <span className="text-sm">{sector}</span>
            </label>
          ))}
        </div>
      </section>

      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-3">Preferred Company Sizes</h2>
        <div className="flex gap-4">
          {SIZES.map(({ value, label }) => (
            <label key={value} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={preferences.preferred_sizes.includes(value)}
                onChange={() => toggleSize(value)}
                className="rounded border-gray-300"
              />
              <span className="text-sm">{label}</span>
            </label>
          ))}
        </div>
      </section>

      <section className="mb-8">
        <h2 className="text-lg font-semibold mb-3">Max Risk Level</h2>
        <select
          value={preferences.max_risk_level}
          onChange={(e) =>
            setPreferences((prev) => ({ ...prev, max_risk_level: e.target.value }))
          }
          className="border border-gray-300 rounded px-3 py-2"
        >
          {RISK_LEVELS.map((level) => (
            <option key={level} value={level}>
              {level}
            </option>
          ))}
        </select>
      </section>

      <button
        onClick={handleSave}
        disabled={saving}
        className="bg-blue-600 text-white px-6 py-2 rounded hover:bg-blue-700 disabled:opacity-50"
      >
        {saving ? "Saving..." : "Save Preferences"}
      </button>
    </div>
  );
}
