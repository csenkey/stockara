import { useEffect, useState } from "react";

import Calendar from "./pages/Calendar";
import DataHealth from "./pages/DataHealth";
import Dashboard from "./pages/Dashboard";

type AppView = "top-picks" | "calendar" | "data-health";

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
    window.location.hash = nextView === "top-picks" ? "" : nextView;
    setView(nextView);
  }

  if (view === "calendar") {
    return <Calendar onNavigate={navigate} />;
  }
  if (view === "data-health") {
    return <DataHealth onNavigate={navigate} />;
  }
  return <Dashboard onNavigate={navigate} />;
}

function viewFromHash(): AppView {
  const hash = window.location.hash.replace("#", "");
  if (hash === "calendar" || hash === "data-health") return hash;
  return "top-picks";
}

export default App;
