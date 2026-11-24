import { useEffect, useState } from "react";
import { get, put } from "../api";
import { Card, ErrorBox, useAction } from "../components/ui";

export default function Settings() {
  const [m, setM] = useState<any>(null); const [meta, setMeta] = useState<any>(null);
  useEffect(() => { get("/monitoring/settings").then(setM); get("/meta").then(setMeta); }, []);
  const save = useAction(async () => setM(await put("/monitoring/settings", m)));
  if (!m) return <p className="muted">Loading…</p>;
  const f = (k: string, label: string) => <><label>{label}</label><input type="number" value={m[k]} onChange={(e) => setM({ ...m, [k]: Number(e.target.value) })} /></>;
  return (
    <>
      <h2>Settings</h2>
      <Card title="Monitoring">
        <label><input type="checkbox" checked={m.enabled} onChange={(e) => setM({ ...m, enabled: e.target.checked })} /> Background polling enabled</label>
        {f("interval_s", "Polling interval (seconds, min 30)")}{f("cpu_warn", "CPU warning threshold (%)")}{f("mem_warn", "Memory warning threshold (%)")}{f("retention_days", "Keep samples (days)")}
        <p><button onClick={() => save.run()} disabled={save.busy}>Save</button></p><ErrorBox e={save.error} />
      </Card>
      <Card title="Security"><p>Secret store: <b>{meta?.secret_store}</b>. Access is limited to this PC (localhost) with a session cookie and CSRF protection. LAN access is not enabled.</p></Card>
    </>
  );
}
