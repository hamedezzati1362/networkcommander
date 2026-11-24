import { NavLink, Route, Routes } from "react-router-dom";
import { useEffect, useState } from "react";
import { get } from "./api";
import Dashboard from "./pages/Dashboard";
import Inventory from "./pages/Inventory";
import DeviceDetail from "./pages/DeviceDetail";
import Topology from "./pages/Topology";
import Jobs from "./pages/Jobs";
import Audit from "./pages/Audit";
import Settings from "./pages/Settings";
import BulkChange from "./pages/BulkChange";
import Templates from "./pages/Templates";

const nav: [string, string][] = [["/", "Dashboard"], ["/topology", "Network Topology"], ["/devices", "Device Inventory"], ["/bulk", "Multi-device change"],
  ["/templates", "Templates"], ["/jobs", "Jobs & Changes"], ["/audit", "Audit & Events"], ["/settings", "Settings"]];

export default function App() {
  const [meta, setMeta] = useState<any>(null);
  useEffect(() => { get("/health").then(setMeta).catch(() => {}); }, []);
  const toggle = () => {
    const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem("nc-theme", t); } catch { /* ignore */ }
  };
  return (
    <div className="app">
      <nav className="side">
        <h1>Network Commander</h1>
        {nav.map(([to, label]) => <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>{label}</NavLink>)}
        <div style={{ marginTop: 18, padding: "0 8px" }}><button className="secondary" onClick={toggle}>Dark / Light</button></div>
        <div style={{ margin: "14px 8px", fontSize: 11 }}>v{meta?.version ?? "?"}<br />secrets: {meta?.secret_store ?? "?"}</div>
      </nav>
      <main className="main">
        {meta?.simulator_enabled && <div className="warn"><b>SIMULATION mode is on.</b> Devices marked SIMULATED are not real equipment.</div>}
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/devices" element={<Inventory />} />
          <Route path="/devices/:id" element={<DeviceDetail />} />
          <Route path="/topology" element={<Topology />} />
          <Route path="/bulk" element={<BulkChange />} />
          <Route path="/templates" element={<Templates />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
