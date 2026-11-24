import { useEffect, useState } from "react";
import { get, post } from "../api";
import { Badge, Card, DiffView, ErrorBox } from "../components/ui";

export default function Jobs() {
  const [jobs, setJobs] = useState<any[]>([]); const [changes, setChanges] = useState<any[]>([]); const [open, setOpen] = useState<any>(null); const [err, setErr] = useState<unknown>(null);
  const load = () => { get("/jobs").then(setJobs).catch(setErr); get("/changes?limit=50").then(setChanges).catch(setErr); };
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, []);
  return (
    <>
      <h2>Jobs &amp; Changes</h2><ErrorBox e={err} />
      <Card title="Multi-device jobs">
        <table><thead><tr><th>#</th><th>Kind</th><th>Status</th><th>Per device</th><th></th></tr></thead><tbody>
          {jobs.map((j) => (
            <tr key={j.id}><td>{j.id}</td><td>{j.kind} <span className="muted">({j.params.mode})</span></td><td><Badge v={j.status} /></td>
              <td>{j.items.map((i: any) => <div key={i.device}>{i.device}: <Badge v={i.status} /> <span className="muted">{i.detail}</span></div>)}</td>
              <td>{["queued", "running"].includes(j.status) && <button className="secondary" onClick={() => post(`/jobs/${j.id}/cancel`).then(load)}>Cancel</button>}</td></tr>
          ))}
        </tbody></table>{jobs.length === 0 && <p className="muted">No jobs yet.</p>}
      </Card>
      <Card title="Change history (execution reports)">
        <table><thead><tr><th>#</th><th>Time</th><th>Module</th><th>Risk</th><th>Status</th><th>Summary</th></tr></thead><tbody>
          {changes.map((c) => <tr key={c.id} onClick={() => setOpen(open?.id === c.id ? null : c)} style={{ cursor: "pointer" }}><td>{c.id}</td><td>{new Date(c.created_at).toLocaleString()}</td><td>{c.module}</td><td><Badge v={c.risk} /></td><td><Badge v={c.status} /></td><td>{c.plan.summary.join("; ")}</td></tr>)}
        </tbody></table>
        {open && (
          <div>
            <h3>Change #{open.id} report</h3>
            <ul className="timeline">{open.report.map((r: any, i: number) => <li key={i}><Badge v={r.status} /> <b>{r.step}</b> {r.detail}</li>)}</ul>
            {open.after_state && <><h3>What changed on the device</h3><DiffView text={open.after_state} /></>}
          </div>
        )}
      </Card>
    </>
  );
}
