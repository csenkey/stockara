import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Settings from "./pages/Settings";
import Dashboard from "./pages/Dashboard";
import DemoLeaderboard from "./pages/DemoLeaderboard";
import DemoAccountDetail from "./pages/DemoAccountDetail";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
        </Route>
        <Route path="/login" element={<Login />} />
        <Route path="/demo" element={<DemoLeaderboard />} />
        <Route path="/demo/:name" element={<DemoAccountDetail />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
