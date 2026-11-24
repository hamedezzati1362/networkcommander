import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { del, get, post } from "../api";
import { Badge, Card, ErrorBox, useAction } from "../components/ui";

export default function Inventory() {
  const [devs, setDevs] = useState<any[]>([]);
  const [creds, setCreds] = useState<any[]>([]);
  const [meta, setMeta] = useState<any>({ vendors: [] });
  const [q, setQ] = useState(""); const [vendor, setVendor] = useState(""); const [status, setStatus] = useState(""); const [groupBy, setGroupBy] = useState(false);
  const [sel, setSel] = useState<number[]>([]);
  const [form, setForm] = useState<any>({ name: "", host: "", port: 22, vendor: "cisco", transport: "ssh", credential_id: "", group: "", location: "" });
  const [cform, setCform] = useState<any>({ name: "", username: "", password: "", private_key: "", enable: "" });
  const [scan, setScan] = useState({ cidr: "", result: null as any });
  const [csv, setCsv] = useState("");
  const [note, setNote] = useState<string>("");

  const load = async () => {
    const p = new URLSearchParams(); if (q) p.set("q", q); if (vendor) p.set("vendor", vendor); if (status) p.set("status", status);
    setDevs(await get("/devices?" + p)); setCreds(await get("/credentials")); setMeta(await get("/meta"));
  };
  useEffect(() => { load().catch(() => {}); }, [q, vendor, status]);

  const addCred = useAction(async () => { await post("/credentials", { ...cform, private_key: cform.private_key || null, enable: cform.enable || null, password: cform.password || null }); setCform({ name: "", username: "", password: "", private_key: "", enable: "" }); await load(); });
  const addDev = useAction(async () => { await post("/devices", { ...form, port: Number(form.port), credential_id: form.credential_id ? Number(form.credential_id) : null, group: form.group || null, location: form.location || null }); setForm({ ...form, name: "", host: "" }); await load(); });
  const doScan = useAction(async () => setScan({ ...scan, result: await post("/discovery/scan", { cidr: scan.cidr }) }));
  const doImport = useAction(async () => { const r = await post("/devices/import", { csv }); setNote(`Imported ${r.added}; ${r.errors.length} rows rejected ${r.errors.map((e: any) => `(line ${e.line}: ${e.error})`).join(" ")}`); await load(); });
  const bulk = useAction(async (kind: string) => { const j = await post(`/bulk/${kind}`, { device_ids: sel }); setNote(`Job #${j.id} started — see Jobs & Changes.`); });
  const rm = useAction(async (id: number) => { if (confirm("Delete this device and its backups/history?")) { await del(`/devices/${id}`); await load(); } });

  const groups = groupBy ? [...new Set(devs.map((d) => d.group ?? "(no group)"))] : [""];
  return (
    <>
      <div className="top"><h2>Device Inventory</h2></div>
      <Card>
        <div className="row">
          <input placeholder="Search name, IP, model, location" value={q} onChange={(e) => setQ(e.target.value)} style={{ minWidth: 260 }} />
          <select value={vendor} onChange={(e) => setVendor(e.target.value)}><option value="">All vendors</option>{meta.vendors.map((v: string) => <option key={v}>{v}</option>)}</select>
          <select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any status</option>{["online", "offline", "auth_failed", "error", "unknown"].map((s) => <option key={s}>{s}</option>)}</select>
          <label style={{ margin: 0 }}><input type="checkbox" checked={groupBy} onChange={(e) => setGroupBy(e.target.checked)} /> Group</label>
          <span className="muted">{sel.length} selected</span>
          <button disabled={!sel.length || bulk.busy} onClick={() => bulk.run("backup")}>Backup selected</button>
          <button disabled={!sel.length || bulk.busy} onClick={() => bulk.run("identify")}>Re-identify selected</button>
        </div>
        <ErrorBox e={bulk.error || rm.error} />{note && <div className="ok">{note}</div>}
        {groups.map((g) => (
          <div key={g}>
            {groupBy && <h3>{g}</h3>}
            <table>
              <thead><tr><th></th><th>Name</th><th>Address</th><th>Vendor / OS</th><th>Model</th><th>Role</th><th>Status</th><th></th></tr></thead>
              <tbody>
                {devs.filter((d) => !groupBy || (d.group ?? "(no group)") === g).map((d) => (
                  <tr key={d.id}>
                    <td><input type="checkbox" checked={sel.includes(d.id)} onChange={(e) => setSel(e.target.checked ? [...sel, d.id] : sel.filter((x) => x !== d.id))} /></td>
                    <td><Link to={`/devices/${d.id}`}>{d.name}</Link>{d.simulated && <span className="sim">SIMULATED</span>}</td>
                    <td>{d.host}:{d.port}</td>
                    <td>{d.vendor}{d.os_family ? ` / ${d.os_family} ${d.os_version ?? ""}` : <span className="muted"> (not identified)</span>}</td>
                    <td>{d.model ?? "—"}</td><td>{d.role ?? "—"}</td>
                    <td><Badge v={d.status} />{!d.host_key_trusted && !d.simulated && <div className="muted">host key not trusted</div>}</td>
                    <td><button className="secondary" onClick={() => rm.run(d.id)}>Delete</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
        {devs.length === 0 && <p className="muted">No devices match.</p>}
      </Card>

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))" }}>
        <Card title="1. Credential profile">
          <label>Name</label><input value={cform.name} onChange={(e) => setCform({ ...cform, name: e.target.value })} />
          <label>Username</label><input value={cform.username} onChange={(e) => setCform({ ...cform, username: e.target.value })} />
          <label>Password</label><input type="password" autoComplete="new-password" value={cform.password} onChange={(e) => setCform({ ...cform, password: e.target.value })} />
          <label>…or SSH private key (PEM, unencrypted)</label><textarea rows={3} value={cform.private_key} onChange={(e) => setCform({ ...cform, private_key: e.target.value })} />
          <label>Enable secret (Cisco, optional)</label><input type="password" autoComplete="new-password" value={cform.enable} onChange={(e) => setCform({ ...cform, enable: e.target.value })} />
          <p className="help">Secrets are encrypted with Windows DPAPI and never shown again. Existing: {creds.map((c) => c.name).join(", ") || "none"}</p>
          <button disabled={addCred.busy} onClick={() => addCred.run()}>Save profile</button><ErrorBox e={addCred.error} />
        </Card>
        <Card title="2. Add device">
          <label>Name</label><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <label>IP / host</label><input value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} />
          <div className="row"><div><label>Vendor</label><select value={form.vendor} onChange={(e) => setForm({ ...form, vendor: e.target.value })}>{meta.vendors.map((v: string) => <option key={v}>{v}</option>)}</select></div>
            <div><label>SSH port</label><input type="number" style={{ width: 90 }} value={form.port} onChange={(e) => setForm({ ...form, port: e.target.value })} /></div></div>
          <label>Credential profile</label><select value={form.credential_id} onChange={(e) => setForm({ ...form, credential_id: e.target.value })}><option value="">(choose)</option>{creds.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          <div className="row"><div><label>Group</label><input value={form.group} onChange={(e) => setForm({ ...form, group: e.target.value })} /></div><div><label>Location</label><input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} /></div></div>
          {meta.simulator_enabled && <label><input type="checkbox" checked={form.transport === "simulator"} onChange={(e) => setForm({ ...form, transport: e.target.checked ? "simulator" : "ssh" })} /> SIMULATED device (not real)</label>}
          <p className="help">After adding: open the device, review &amp; trust the SSH host key, then “Identify”.</p>
          <button disabled={addDev.busy} onClick={() => addDev.run()}>Add device</button><ErrorBox e={addDev.error} />
        </Card>
        <Card title="Discover (TCP/SSH scan)">
          <label>CIDR (max 1024 addresses)</label><input placeholder="192.168.1.0/24" value={scan.cidr} onChange={(e) => setScan({ ...scan, cidr: e.target.value })} />
          <p className="help">Looks for SSH listeners and shows their banner. It does not log in. Vendor is only a hint.</p>
          <button disabled={doScan.busy || !scan.cidr} onClick={() => doScan.run()}>{doScan.busy ? "Scanning…" : "Scan"}</button><ErrorBox e={doScan.error} />
          {scan.result && <table><tbody>{scan.result.found.map((f: any) => <tr key={f.ip}><td>{f.ip}</td><td>{f.banner}</td><td>{f.suggested_vendor ?? "?"}</td><td>{f.known ? "in inventory" : <button className="secondary" onClick={() => setForm({ ...form, host: f.ip, name: f.ip, vendor: f.suggested_vendor ?? form.vendor })}>Use</button>}</td></tr>)}{scan.result.found.length === 0 && <tr><td className="muted">Nothing found.</td></tr>}</tbody></table>}
        </Card>
        <Card title="Import CSV">
          <textarea rows={5} style={{ width: "100%" }} placeholder={"name,host,vendor,port,group,location,credential\nsw1,10.0.0.2,cisco,22,core,HQ,lab"} value={csv} onChange={(e) => setCsv(e.target.value)} />
          <button disabled={!csv || doImport.busy} onClick={() => doImport.run()}>Import</button><ErrorBox e={doImport.error} />
        </Card>
      </div>
    </>
  );
}
