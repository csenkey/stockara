import { FormEvent, useState } from "react";
import type { AuthConfig, AuthSession } from "../services/auth";

interface Props { config: AuthConfig; session: AuthSession }

export default function HoldingAnalysis({ config, session }: Props) {
  const [ticker, setTicker] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [price, setPrice] = useState("");
  const [portfolioValue, setPortfolioValue] = useState("");
  const [objective, setObjective] = useState("balanced");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault(); setLoading(true); setError(""); setResult(null);
    try {
      const response = await fetch(`${config.apiBaseUrl}/api/holding-reviews`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.idToken}` },
        body: JSON.stringify({ ticker, quantity: Number(quantity), buying_price: price,
          portfolio_total_value: portfolioValue || null, objective }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail?.error ?? body.detail ?? `HTTP ${response.status}`);
      setResult(body as Record<string, unknown>);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Analysis failed"); }
    finally { setLoading(false); }
  }

  return <main className="min-h-screen bg-slate-950 px-5 py-8 text-slate-100">
    <div className="mx-auto max-w-4xl space-y-6">
      <div><h1 className="text-2xl font-semibold">Holding analysis</h1>
        <p className="mt-2 text-sm text-slate-400">Review whether capital in an existing holding should remain allocated there.</p></div>
      <form onSubmit={submit} className="grid gap-4 border border-slate-800 bg-slate-900 p-5 md:grid-cols-2">
        <Field label="Ticker" value={ticker} setValue={setTicker} />
        <Field label="Quantity" value={quantity} setValue={setQuantity} type="number" />
        <Field label="Average buying price" value={price} setValue={setPrice} type="number" />
        <Field label="Total portfolio value (optional)" value={portfolioValue} setValue={setPortfolioValue} type="number" />
        <label className="text-sm">Objective<select value={objective} onChange={(e) => setObjective(e.target.value)} className="mt-1 w-full border border-slate-700 bg-slate-950 p-3"><option value="income">Income</option><option value="balanced">Balanced</option><option value="growth">Growth</option></select></label>
        <div className="flex items-end"><button disabled={loading} className="h-11 w-full bg-emerald-600 px-4 font-medium hover:bg-emerald-500 disabled:opacity-50">{loading ? "Analyzing…" : "Analyze holding"}</button></div>
      </form>
      {error && <div className="border border-red-800 bg-red-950 p-4 text-red-100">{error}</div>}
      {result && <pre className="overflow-auto border border-slate-800 bg-slate-900 p-5 text-xs text-slate-200">{JSON.stringify(result, null, 2)}</pre>}
    </div>
  </main>;
}

function Field({ label, value, setValue, type = "text" }: { label: string; value: string; setValue: (v: string) => void; type?: string }) {
  return <label className="text-sm">{label}<input required={label !== "Total portfolio value (optional)"} type={type} min={type === "number" ? "0.01" : undefined} step="any" value={value} onChange={(e) => setValue(e.target.value.toUpperCase())} className="mt-1 w-full border border-slate-700 bg-slate-950 p-3" /></label>;
}
