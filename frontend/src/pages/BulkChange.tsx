import { useEffect, useState } from "react";
import { get, post } from "../api";
import { Badge, Card, ErrorBox, useAction } from "../components/ui";
import { SchemaForm } from "../components/SchemaForm";

export default function BulkChange() {
  const [devs, setDevs] = useState<any[]>([]); const [schemas, setSchemas] = useState<any>({}); const [mod, setMod] = useState("ntp"); const [val, setVal] = useState<any>({});
  const [sel, setSel] = useState<number[]>([]); const [res, setRes] = useState<any>(null); const [excl, setExcl] = useState<number[]>([]);
  const [mode, setMode] = useState("sequential"); const [conc, setConc] = useState(3); const [stop, setStop] = useState(true); const [ack, setAck] = useState(false); const [msg, setMsg] = useState("");
  useEffect(() => { get("/devices").then(setDevs); get("/modules/schema").then(setSchemas); }, []);
  const plan = useAction(async () => { setMsg(""); setExcl([]); setRes(await post("/bulk/plan", { device_ids: sel, spec: { module: mod, ...val } })); });
  const ok = res?.results.filter((r: any) => r.ok && !excl.includes(r.device_id)) ?? [];
  const risky = ok.some((r: any) => r.change.risk === "high");
  const apply = useAction(async () => { const j = await post("/bulk/apply", { change_ids: ok.map((r: any) => r.change.id), mode, concurrency: conc, stop_on_failure: stop, ack_risk: ack }); setMsg(`Job #${j.id} started${j.forced_sequential ? " (forced sequential because of high-risk changes)" : ""}. Follow it in Jobs & Changes.`); });
  return (
    <>
      <h2>Multi-device change</h2>
      <Card title="1. What to change">
        <select value={mod} onChange={(e) => { setMod(e.target.value); setVal({}); setRes(null); }}>{Object.entries(schemas).map(([k, s]: any) => <option key={k} value={k}>{s.title}</option>)}</select>
        {schemas[mod] && <SchemaForm key={mod} schema={schemas[mod].schema} value={val} onChange={setVal} />}
      </Card>
      <Card title="2. On which devices">
        <div className="row">{devs.map((d) => <label key={d.id} style={{ margin: 0 }}><input type="checkbox" checked={sel.includes(d.id)} onChange={(e) => setSel(e.target.checked ? [...sel, d.id] : sel.filter((x) => x !== d.id))} /> {d.name} <span className="muted">({d.vendor}{d.os_family ? "/" + d.os_family : ""})</span></label>)}</div>
        <p><button onClick={() => plan.run()} disabled={!sel.length || plan.busy}>Check compatibility &amp; preview</button></p><ErrorBox e={plan.error} />
      </Card>
      {res && (
        <Card title={`3. Review: ${res.compatible} compatible, ${res.unsupported} not possible`}>
          <table><thead><tr><th>Include</th><th>Device</th><th>Result</th></tr></thead><tbody>
            {res.results.map((r: any) => <tr key={r.device_id}><td>{r.ok && <input type="checkbox" checked={!excl.includes(r.device_id)} onChange={(e) => setExcl(e.target.checked ? excl.filter((x) => x !== r.device_id) : [...excl, r.device_id])} />}</td><td>{r.device}</td>
              <td>{r.ok ? <><Badge v={r.change.risk} /> {r.change.already_compliant ? "already compliant (will be skipped)" : r.change.plan.summary.join("; ")}<details><summary>operations</summary><pre>{r.change.plan.commands.join("\n")}</pre></details></> : <span className="err" style={{ display: "inline-block", margin: 0 }}>{r.error}</span>}</td></tr>)}
          </tbody></table>
          <div className="row"><label style={{ margin: 0 }}>Execution <select value={mode} onChange={(e) => setMode(e.target.value)}><option value="sequential">sequential (in the order shown)</option><option value="parallel">parallel</option></select></label>
            {mode === "parallel" && <label style={{ margin: 0 }}>concurrency <input type="number" min={1} max={10} style={{ width: 70 }} value={conc} onChange={(e) => setConc(Number(e.target.value))} /></label>}
            <label style={{ margin: 0 }}><input type="checkbox" checked={stop} onChange={(e) => setStop(e.target.checked)} /> stop at first failure</label></div>
          {risky && <label><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> Some changes are <b>high</b> risk. I accept; they run one device at a time.</label>}
          <p><button disabled={!ok.length || apply.busy || (risky && !ack)} onClick={() => apply.run()}>Apply to {ok.length} device(s)</button></p>
          <ErrorBox e={apply.error} />{msg && <div className="ok">{msg}</div>}
          <p className="help">Critical-risk changes (reboot, management interface) are never run in bulk.</p>
        </Card>
      )}
    </>
  );
}
