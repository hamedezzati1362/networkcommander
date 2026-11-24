import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { get, post } from "../api";
import { Badge, Card, DiffView, ErrorBox, useAction } from "../components/ui";
import { SchemaForm } from "../components/SchemaForm";
import { ChangeFlow } from "../components/ChangePanel";

const TABS = ["Overview", "Configure", "Backups", "Monitoring", "Diagnostics", "History"];

export default function DeviceDetail() {
  const { id } = useParams();
  const [dev, setDev] = useState<any>(null); const [tab, setTab] = useState("Overview"); const [err, setErr] = useState<unknown>(null);
  const load = () => get(`/devices/${id}`).then(setDev).catch(setErr);
  useEffect(() => { load(); }, [id]);
  if (err) return <ErrorBox e={err} />;
  if (!dev) return <p className="muted">Loading…</p>;
  return (
    <>
      <div className="top"><h2>{dev.name}{dev.simulated && <span className="sim">SIMULATED</span>}</h2><div><Badge v={dev.status} /> <span className="muted">{dev.host}:{dev.port} · {dev.vendor}</span></div></div>
      <div className="tabs">{TABS.map((t) => <button key={t} className={t === tab ? "on" : ""} onClick={() => setTab(t)}>{t}</button>)}</div>
      {tab === "Overview" && <Overview dev={dev} reload={load} />}
      {tab === "Configure" && <Configure dev={dev} />}
      {tab === "Backups" && <Backups dev={dev} />}
      {tab === "Monitoring" && <Monitoring dev={dev} reload={load} />}
      {tab === "Diagnostics" && <Diagnostics dev={dev} />}
      {tab === "History" && <History dev={dev} />}
    </>
  );
}

function Overview({ dev, reload }: { dev: any; reload: () => void }) {
  const [hk, setHk] = useState<any>(null);
  const fetchKey = useAction(async () => setHk(await post(`/devices/${dev.id}/host-key`)));
  const trust = useAction(async () => { await post(`/devices/${dev.id}/trust-host-key`, { fingerprint: hk.fingerprint }); setHk(null); reload(); });
  const ident = useAction(async () => { await post(`/devices/${dev.id}/identify`); reload(); });
  const caps = Object.entries(dev.capabilities ?? {}) as [string, any][];
  return (
    <>
      <Card title="Identity (read from the device)">
        <table><tbody>
          {([["Hostname", dev.hostname], ["Model", dev.model], ["OS family", dev.os_family], ["OS version", dev.os_version], ["Serial", dev.serial], ["Uptime", dev.uptime], ["Role (heuristic + measured)", dev.role], ["Last identified", dev.last_identified && new Date(dev.last_identified).toLocaleString()]] as [string, any][]).map(([k, v]) => <tr key={k}><td className="muted" style={{ width: 220 }}>{k}</td><td>{v ?? "— not identified yet"}</td></tr>)}
        </tbody></table>
        {dev.status_detail && <div className="err">{dev.status_detail}</div>}
        <div className="row"><button onClick={() => ident.run()} disabled={ident.busy || !dev.host_key_trusted}>{ident.busy ? "Identifying…" : "Identify & detect capabilities"}</button>{!dev.host_key_trusted && <span className="warn">Trust the SSH host key first.</span>}</div>
        <ErrorBox e={ident.error} />
      </Card>
      {!dev.simulated && (
        <Card title="SSH host key">
          <p>Pinned: <code>{dev.host_key_fp ?? "none"}</code> {dev.host_key_trusted ? <Badge v="ok" /> : <Badge v="unknown" />}</p>
          <button className="secondary" onClick={() => fetchKey.run()} disabled={fetchKey.busy}>Fetch current key from device</button><ErrorBox e={fetchKey.error} />
          {hk && (
            <div className={hk.changed ? "err" : "warn"}>
              {hk.changed && <b>WARNING: the key differs from the pinned one — possible interception or device replacement. </b>}
              Device offers <code>{hk.type} {hk.fingerprint}</code> (banner: {hk.banner}). Compare this with the fingerprint shown on the device console before trusting it.<br />
              <button style={{ marginTop: 8 }} onClick={() => trust.run()}>I verified it — trust this key</button><ErrorBox e={trust.error} />
            </div>
          )}
        </Card>
      )}
      <Card title="Capability matrix (measured with read-only probe commands)">
        {caps.length === 0 ? <p className="muted">Not detected yet. SSH login alone does not make a device “supported”.</p> : (
          <table><thead><tr><th>Capability</th><th>Status</th><th>Why</th></tr></thead><tbody>
            {caps.map(([k, c]) => <tr key={k}><td>{k}</td><td><Badge v={c.status} /></td><td className="muted">{c.reason}</td></tr>)}
          </tbody></table>
        )}
      </Card>
    </>
  );
}

function Configure({ dev }: { dev: any }) {
  const [mods, setMods] = useState<any[]>([]); const [schemas, setSchemas] = useState<any>({}); const [cur, setCur] = useState<string>(""); const [val, setVal] = useState<any>({});
  useEffect(() => { get(`/devices/${dev.id}/modules`).then(setMods); get("/modules/schema").then(setSchemas); }, [dev.id]);
  const m = mods.find((x) => x.module === cur);
  const spec = useMemo(() => ({ module: cur, ...val }), [cur, val]);
  const allowed = m?.module === "vlan" ? { kind: m.kinds.filter((k: string) => k !== "vlan_database" || true) } : undefined;
  return (
    <div className="grid" style={{ gridTemplateColumns: "230px 1fr", alignItems: "start" }}>
      <Card title="Modules">
        {mods.map((x) => (
          <div key={x.module} style={{ marginBottom: 6 }}>
            <button className={x.module === cur ? "" : "secondary"} style={{ width: "100%", textAlign: "left" }} onClick={() => { setCur(x.module); setVal({}); }}>{x.title}</button>
            <Badge v={x.status} />{x.status !== "supported" && <div className="help">{x.reason}</div>}
          </div>
        ))}
      </Card>
      <div>
        {!cur && <Card><p className="muted">Choose a module. Only what this device actually supports can be applied; reasons are shown for the rest.</p></Card>}
        {cur && m && schemas[cur] && (
          <Card title={m.title} right={<Badge v={m.status} />}>
            {["unsupported", "not_detected"].includes(m.status) ? <div className="err">{m.reason}</div> : (
              <>
                <SchemaForm key={cur} schema={schemas[cur].schema} value={val} onChange={setVal} allowed={allowed} />
                <hr style={{ border: 0, borderTop: "1px solid var(--line)", margin: "14px 0" }} />
                <ChangeFlow key={JSON.stringify(spec)} deviceId={dev.id} deviceName={dev.name} spec={spec} />
              </>
            )}
          </Card>
        )}
      </div>
    </div>
  );
}

function Backups({ dev }: { dev: any }) {
  const [list, setList] = useState<any[]>([]); const [view, setView] = useState<any>(null); const [diff, setDiff] = useState<string>(""); const [pick, setPick] = useState<number[]>([]); const [restore, setRestore] = useState<any>(null);
  const load = () => get(`/devices/${dev.id}/backups`).then(setList);
  useEffect(() => { load(); }, [dev.id]);
  const make = useAction(async () => { await post(`/devices/${dev.id}/backups`); await load(); });
  const show = useAction(async (id: number, reveal = false) => setView(await get(`/backups/${id}?reveal=${reveal}`)));
  const doDiff = useAction(async () => setDiff((await get(`/backups/diff/${pick[0]}/${pick[1]}`)).diff || "(identical)"));
  const prev = useAction(async (id: number) => setRestore(await post(`/backups/${id}/restore-preview`)));
  return (
    <>
      <Card title="Configuration backups (encrypted at rest)" right={<button onClick={() => make.run()} disabled={make.busy}>{make.busy ? "Backing up…" : "Backup now"}</button>}>
        <ErrorBox e={make.error || show.error || doDiff.error || prev.error} />
        <table><thead><tr><th></th><th>#</th><th>Time</th><th>Kind</th><th>Reason</th><th>Size</th><th>SHA-256</th><th></th></tr></thead><tbody>
          {list.map((b) => <tr key={b.id}><td><input type="checkbox" checked={pick.includes(b.id)} onChange={(e) => setPick(e.target.checked ? [...pick, b.id].slice(-2) : pick.filter((x) => x !== b.id))} /></td><td>{b.id}</td><td>{new Date(b.created_at).toLocaleString()}</td><td>{b.kind}</td><td>{b.reason}</td><td>{b.size}</td><td className="muted">{b.sha256.slice(0, 12)}…</td>
            <td><button className="secondary" onClick={() => show.run(b.id)}>View</button> <button className="secondary" onClick={() => prev.run(b.id)}>Restore preview</button></td></tr>)}
        </tbody></table>
        <p><button className="secondary" disabled={pick.length !== 2} onClick={() => doDiff.run()}>Compare the 2 selected</button></p>
        {diff && <DiffView text={diff} />}
        {restore && <div className="warn"><b>{restore.lines_changed} lines would change.</b> {restore.note}<DiffView text={restore.diff || "(identical)"} /></div>}
      </Card>
      {view && <Card title={`Backup #${view.id} ${view.masked ? "(secrets masked)" : "(SECRETS VISIBLE)"}`} right={view.masked && <button className="secondary" onClick={() => confirm("Show secrets in clear text? This is written to the audit log.") && show.run(view.id, true)}>Reveal secrets</button>}><pre>{view.content}</pre></Card>}
    </>
  );
}

function Monitoring({ dev, reload }: { dev: any; reload: () => void }) {
  const [cpu, setCpu] = useState<any[]>([]); const [mem, setMem] = useState<any[]>([]); const [up, setUp] = useState<any[]>([]); const [ev, setEv] = useState<any[]>([]);
  const load = async () => { setCpu(await get(`/devices/${dev.id}/metrics?metric=cpu`)); setMem(await get(`/devices/${dev.id}/metrics?metric=mem`)); setUp(await get(`/devices/${dev.id}/metrics?metric=up`)); setEv(await get(`/events?device_id=${dev.id}&limit=30`)); };
  useEffect(() => { load(); }, [dev.id]);
  const poll = useAction(async () => { await post(`/devices/${dev.id}/poll`); await load(); reload(); });
  return (
    <>
      <Card title="Live metrics" right={<button onClick={() => poll.run()} disabled={poll.busy}>Poll now</button>}>
        <ErrorBox e={poll.error} />
        <div className="grid"><Spark title="CPU %" data={cpu} /><Spark title="Memory %" data={mem} /><Spark title="Reachable (1/0)" data={up} max={1} /></div>
        <p className="help">Samples are collected by the background poller (interval in Settings). Metrics need an identified device whose capability “metrics” is supported.</p>
      </Card>
      <Card title="Events"><table><tbody>{ev.map((e) => <tr key={e.id}><td>{new Date(e.ts).toLocaleString()}</td><td><Badge v={e.severity === "info" ? "ok" : e.severity === "warning" ? "medium" : "critical"} /></td><td>{e.message}</td></tr>)}</tbody></table>{ev.length === 0 && <p className="muted">No events.</p>}</Card>
    </>
  );
}

function Spark({ title, data, max = 100 }: { title: string; data: any[]; max?: number }) {
  const w = 240, h = 60;
  const pts = data.map((d, i) => `${(i / Math.max(1, data.length - 1)) * w},${h - (Math.min(d.value, max) / max) * h}`).join(" ");
  return (
    <div><div className="muted">{title}: <b>{data.length ? data[data.length - 1].value : "—"}</b></div>
      {data.length ? <svg width={w} height={h} style={{ background: "var(--code)", borderRadius: 6 }}><polyline fill="none" stroke="var(--brand)" strokeWidth="2" points={pts} /></svg> : <div className="muted">no samples</div>}</div>
  );
}

function Diagnostics({ dev }: { dev: any }) {
  const [target, setTarget] = useState(""); const [out, setOut] = useState<any>(null); const [dns, setDns] = useState<any>(null);
  const run = useAction(async (kind: string) => setOut({ kind, ...(await post(`/devices/${dev.id}/diagnostics`, { kind, target: target || null })) }));
  const lookup = useAction(async () => setDns(await get(`/diagnostics/dns?name=${encodeURIComponent(target)}`)));
  return (
    <Card title="Diagnostics run from the device">
      <div className="row"><input placeholder="IP or host (for ping/traceroute/DNS)" value={target} onChange={(e) => setTarget(e.target.value)} style={{ minWidth: 260 }} />
        {["ping", "traceroute"].map((k) => <button key={k} disabled={!target || run.busy} onClick={() => run.run(k)}>{k}</button>)}
        {["arp", "mac", "routes", "interfaces", "neighbors"].map((k) => <button key={k} className="secondary" disabled={run.busy} onClick={() => run.run(k)}>{k}</button>)}
        <button className="secondary" disabled={!target} onClick={() => lookup.run()}>DNS lookup (this PC)</button></div>
      <ErrorBox e={run.error || lookup.error} />
      {out?.summary && <div className="ok">{out.summary}</div>}
      {out && <pre>{out.output || "(empty output)"}</pre>}
      {dns && <div className={dns.addresses.length ? "ok" : "err"}>{dns.name} → {dns.addresses.join(", ") || dns.error} <span className="muted">({dns.from})</span></div>}
      <p className="help">Output is shown as the device returned it; no parsing is applied to tables.</p>
    </Card>
  );
}

function History({ dev }: { dev: any }) {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => { get(`/changes?device_id=${dev.id}`).then(setRows); }, [dev.id]);
  return (
    <Card title="Change history">
      {rows.map((c) => <details key={c.id}><summary><Badge v={c.status} /> #{c.id} {c.module} — {c.plan.summary.join("; ")} <span className="muted">{new Date(c.created_at).toLocaleString()}</span></summary>
        <ul className="timeline">{c.report.map((r: any, i: number) => <li key={i}><Badge v={r.status} /> <b>{r.step}</b> {r.detail}</li>)}</ul>{c.after_state && <DiffView text={c.after_state} />}</details>)}
      {rows.length === 0 && <p className="muted">No changes yet.</p>}
    </Card>
  );
}
