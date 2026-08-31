import { useEffect, useState } from "react";

import Calendar from "./pages/Calendar";
import DataHealth from "./pages/DataHealth";
import Dashboard from "./pages/Dashboard";
import HoldingAnalysis from "./pages/HoldingAnalysis";
import {
  beginAuth,
  completeAuthCallback,
  loadAuthConfig,
  logout,
  storedSession,
} from "./services/auth";
import type { AuthConfig, AuthSession } from "./services/auth";

type AppView = "top-picks" | "calendar" | "data-health" | "holding-analysis";

function App() {
  const [view, setView] = useState<AppView>(() => viewFromHash());
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [session, setSession] = useState<AuthSession | null>(() => storedSession());
  const [authError, setAuthError] = useState("");

  useEffect(() => {
    loadAuthConfig()
      .then(async (loaded) => {
        setConfig(loaded);
        try {
          setSession(await completeAuthCallback(loaded));
        } catch (reason) {
          setAuthError(reason instanceof Error ? reason.message : "Login failed.");
        }
      })
      .catch(() => setAuthError("Authentication is not configured."));
  }, []);

  useEffect(() => {
    function syncHash() {
      setView(viewFromHash());
    }
    window.addEventListener("hashchange", syncHash);
    return () => window.removeEventListener("hashchange", syncHash);
  }, []);

  function navigate(nextView: AppView) {
    window.location.hash = nextView === "top-picks" ? "" : nextView;
    setView(nextView);
  }

  let page: React.ReactNode;
  if (view === "calendar") page = <Calendar onNavigate={navigate} />;
  else if (view === "data-health") page = <DataHealth onNavigate={navigate} />;
  else if (view === "holding-analysis" && config && session) {
    page = <HoldingAnalysis config={config} session={session} />;
  }
  else page = <Dashboard onNavigate={navigate} />;

  return (
    <>
      <header className="border-b border-slate-800 bg-slate-950 text-slate-100">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-5 py-3">
          <nav className="flex flex-wrap gap-2">
            <button onClick={() => navigate("top-picks")}>Top Picks</button>
            <button onClick={() => navigate("calendar")}>Calendars</button>
            <button onClick={() => navigate("data-health")}>Data Freshness</button>
            {session && (
              <button
                onClick={() => navigate("holding-analysis")}
                className="text-emerald-300"
              >
                Holding Analysis
              </button>
            )}
          </nav>
          <div className="flex items-center gap-2 text-sm">
            {session ? (
              <>
                <span className="hidden text-slate-400 sm:inline">{session.email}</span>
                <button
                  onClick={() => config && logout(config)}
                  className="border border-slate-700 px-3 py-2"
                >
                  Log out
                </button>
              </>
            ) : config ? (
              <>
                <button
                  onClick={() => beginAuth(config)}
                  className="border border-slate-700 px-3 py-2"
                >
                  Log in
                </button>
                <button
                  onClick={() => beginAuth(config, true)}
                  className="bg-emerald-600 px-3 py-2 font-medium"
                >
                  Register
                </button>
              </>
            ) : (
              <span className="text-slate-500">Login unavailable</span>
            )}
          </div>
        </div>
        {authError && (
          <div className="mx-auto max-w-7xl px-5 pb-3 text-sm text-amber-300">
            {authError}
          </div>
        )}
      </header>
      {page}
    </>
  );
}

function viewFromHash(): AppView {
  const hash = window.location.hash.replace("#", "");
  if (
    hash === "calendar" ||
    hash === "data-health" ||
    hash === "holding-analysis"
  ) {
    return hash;
  }
  return "top-picks";
}

export default App;
