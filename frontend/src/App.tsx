import { useEffect, useState } from "react";

import DataHealth from "./pages/DataHealth";
import Dashboard from "./pages/Dashboard";

type AppView = "top-picks" | "data-health";

function App() {
  const [view, setView] = useState<AppView>(() => viewFromHash());

  useEffect(() => {
    function syncHash() {
      setView(viewFromHash());
    }
    window.addEventListener("hashchange", syncHash);
    return () => window.removeEventListener("hashchange", syncHash);
  }, []);

  function navigate(nextView: AppView) {
    window.location.hash = nextView === "data-health" ? "data-health" : "";
    setView(nextView);
  }

  if (view === "data-health") {
    return <DataHealth onNavigate={navigate} />;
  }
  return <Dashboard onNavigate={navigate} />;
}

function viewFromHash(): AppView {
  return window.location.hash.replace("#", "") === "data-health"
    ? "data-health"
    : "top-picks";
}

export default App;
