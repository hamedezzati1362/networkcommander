import { useEffect, useState } from "react";
import { get, post } from "../api";
import { Badge, Card, ErrorBox, useAction } from "../components/ui";

export default function Templates() {
  const [list, setList] = useState<any[]>([]); const [devs, setDevs] = useState<any[]>([]); const [schemas, setSchemas] = useState<any>({});
  const [f, setF] = useState({ name: "", module: "vlan", description: "", spec: '{\n  "module": "vlan",\n  "vlan_id": "{{id}}",\n  "name": "{{name}}"\n}' });
  const [use, setUse] = useState<any>(null); const [values, setValues] = useState<Record<string, string>>({}); const [sel, setSel] = useState<number[]>([]); const [out, setOut] = useState<any>(null);
  const load = () => get("/templates").then(setList);
  useEffect(() => { load(); get("/devices").then(setDevs); get("/modules/schema").then(setSchemas); }, []);
  const save = useAction(async () => { await post("/templates", { name: f.name, module: f.module, description: f.description || null, spec: JSON.parse(f.spec) }); await load(); });
  const plan = useAction(async () => setOut(await post(`/templates/${use.id}/plan`, { values, device_ids: sel })));
  return (
    <>
      <h2>Templates</h2>
      <Card title="New template (saving the same name creates a new version)">
        <div className="row"><input placeholder="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          <select value={f.module} onChange={(e) => setF({ ...f, module: e.target.value })}>{Object.entries(schemas).map(([k, s]: any) => <option key={k} value={k}>{s.title}</option>)}</select></div>
        <label>Module values as JSON — use {"{{variable}}"} for per-deployment values</label>
        <textarea rows={7} style={{ width: "100%", fontFamily: "Consolas, monospace" }} value={f.spec} onChange={(e) => setF({ ...f, spec: e.target.value })} />
        <p><button disabled={!f.name || save.busy} onClick={() => save.run()}>Save template</button></p><ErrorBox e={save.error} />
      </Card>
      <Card title="Saved templates">
        <table><thead><tr><th>Name</th><th>Version</th><th>Module</th><th>Variables</th><th></th></tr></thead><tbody>
          {list.map((t) => <tr key={t.id}><td>{t.name}</td><td>v{t.version}</td><td>{t.module}</td><td>{t.variables.join(", ") || "—"}</td><td><button className="secondary" onClick={() => { setUse(t); setValues({}); setOut(null); }}>Deploy…</button></td></tr>)}
        </tbody></table>
      </Card>
      {use && (
        <Card title={`Deploy ${use.name} v${use.version}`}>
          {use.variables.map((v: string) => <div key={v}><label>{v}</label><input value={values[v] ?? ""} onChange={(e) => setValues({ ...values, [v]: e.target.value })} /></div>)}
          <label>Devices</label><div className="row">{devs.map((d) => <label key={d.id} style={{ margin: 0 }}><input type="checkbox" checked={sel.includes(d.id)} onChange={(e) => setSel(e.target.checked ? [...sel, d.id] : sel.filter((x) => x !== d.id))} /> {d.name}</label>)}</div>
          <p><button onClick={() => plan.run()} disabled={!sel.length || plan.busy}>Preview on devices</button></p><ErrorBox e={plan.error} />
          {out && <table><tbody>{out.results.map((r: any) => <tr key={r.device_id}><td>{r.device}</td><td>{r.ok ? <><Badge v={r.change.risk} /> {r.change.plan.summary.join("; ")}</> : <span className="err">{r.error}</span>}</td></tr>)}</tbody></table>}
          <p className="help">To apply, use <b>Multi-device change</b> with the same values (template deployment applies through the same safety flow; direct apply from this page is not implemented).</p>
        </Card>
      )}
    </>
  );
}
