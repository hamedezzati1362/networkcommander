import { useEffect, useState } from "react";
import { get } from "../api";
import { Badge, Card, ErrorBox } from "../components/ui";

export default function Audit() {
  const [rows, setRows] = useState<any[]>([]); const [ev, setEv] = useState<any[]>([]); const [q, setQ] = useState(""); const [err, setErr] = useState<unknown>(null);
  useEffect(() => { get(`/audit?q=${encodeURIComponent(q)}`).then(setRows).catch(setErr); get(`/events?q=${encodeURIComponent(q)}`).then(setEv).catch(setErr); }, [q]);
  return (
    <>
      <div className="top"><h2>Audit &amp; Events</h2><input placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} /></div><ErrorBox e={err} />
      <Card title="Monitoring events"><table><tbody>{ev.map((e) => <tr key={e.id}><td>{new Date(e.ts).toLocaleString()}</td><td><Badge v={e.severity === "info" ? "ok" : e.severity === "warning" ? "medium" : "critical"} /></td><td>{e.message}</td></tr>)}</tbody></table>{ev.length === 0 && <p className="muted">None.</p>}</Card>
      <Card title="Audit log (secrets are never recorded)"><table><thead><tr><th>Time</th><th>Action</th><th>Target</th><th>Result</th><th>Detail</th></tr></thead><tbody>
        {rows.map((a) => <tr key={a.id}><td>{new Date(a.ts).toLocaleString()}</td><td>{a.action}</td><td>{a.target}</td><td><Badge v={a.result} /></td><td className="muted" style={{ maxWidth: 420, overflow: "hidden", textOverflow: "ellipsis" }}>{JSON.stringify(a.detail)}</td></tr>)}
      </tbody></table></Card>
    </>
  );
}
