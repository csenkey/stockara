import { ChangeEvent } from "react";

const VALID_SECTORS = [
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

const COMPANY_SIZES = [
  { value: "blue_chip", label: "Blue Chip" },
  { value: "mid_cap", label: "Mid Cap" },
  { value: "startup", label: "Startup" },
];

const RISK_LEVELS = [
  { value: "LOW", label: "Low" },
  { value: "MEDIUM", label: "Medium" },
  { value: "HIGH", label: "High" },
];

export interface StockFiltersState {
  sector: string;
  companySize: string;
  maxRisk: string;
}

interface StockFiltersProps {
  filters: StockFiltersState;
  onChange: (filters: StockFiltersState) => void;
}

export default function StockFilters({ filters, onChange }: StockFiltersProps) {
  function handleChange(e: ChangeEvent<HTMLSelectElement>) {
    const { name, value } = e.target;
    onChange({ ...filters, [name]: value });
  }

  return (
    <div className="flex flex-wrap gap-4 items-end">
      <div className="flex flex-col gap-1">
        <label
          htmlFor="filter-sector"
          className="text-xs font-medium text-gray-600"
        >
          Sector
        </label>
        <select
          id="filter-sector"
          name="sector"
          value={filters.sector}
          onChange={handleChange}
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">All Sectors</option>
          {VALID_SECTORS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label
          htmlFor="filter-company-size"
          className="text-xs font-medium text-gray-600"
        >
          Company Size
        </label>
        <select
          id="filter-company-size"
          name="companySize"
          value={filters.companySize}
          onChange={handleChange}
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">All Sizes</option>
          {COMPANY_SIZES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1">
        <label
          htmlFor="filter-risk"
          className="text-xs font-medium text-gray-600"
        >
          Max Risk Level
        </label>
        <select
          id="filter-risk"
          name="maxRisk"
          value={filters.maxRisk}
          onChange={handleChange}
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="">Any Risk</option>
          {RISK_LEVELS.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
