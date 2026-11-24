import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get } from "../api";
import { Badge, Card, ErrorBox } from "../components/ui";

export default function Dashboard() {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<unknown>(null);
  const load = () => get("/dashboard").then(setD).catch(setErr);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);
  if (err) return <ErrorBox e={err} />;
  if (!d) return <p className="muted">Loading…</p>;
  return (
    <>
      <div className="top"><h2>Dashboard</h2><button className="secondary" onClick={load}>Refresh</button></div>
      <div className="grid">
        <Card><div className="muted">Devices</div><div className="stat">{d.devices}</div></Card>
        {Object.entries(d.by_status).map(([k, v]) => <Card key={k}><div className="muted"><Badge v={k} /></div><div className="stat">{v as number}</div></Card>)}
        <Card><div className="muted">Not identified yet</div><div className="stat">{d.unidentified}</div></Card>
      </div>
      {d.devices === 0 && <Card><b>Start here:</b> create a credential profile and add a device on <Link to="/devices">Device Inventory</Link>.</Card>}
      <Card title="Recent events">
        {d.recent_events.length === 0 ? <span className="muted">No events yet.</span> : (
          <table><tbody>{d.recent_events.map((e: any) => <tr key={e.id}><td>{new Date(e.ts).toLocaleString()}</td><td><Badge v={e.severity === "info" ? "ok" : e.severity === "warning" ? "medium" : "critical"} /></td><td>{e.message}</td></tr>)}</tbody></table>
        )}
      </Card>
      <Card title="Recent configuration changes">
        {d.recent_changes.length === 0 ? <span className="muted">No changes yet.</span> : (
          <table><thead><tr><th>#</th><th>Module</th><th>Risk</th><th>Status</th><th>Summary</th></tr></thead>
            <tbody>{d.recent_changes.map((c: any) => <tr key={c.id}><td>{c.id}</td><td>{c.module}</td><td><Badge v={c.risk} /></td><td><Badge v={c.status} /></td><td>{c.plan.summary.join("; ")}</td></tr>)}</tbody></table>
        )}
      </Card>
    </>
  );
}
