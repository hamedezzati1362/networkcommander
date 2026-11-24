import { useState } from "react";
import { post } from "../api";
import { Badge, DiffView, ErrorBox, useAction } from "./ui";

/** Preview -> confirm -> apply -> verified report for one planned change. */
export function ChangeFlow({ deviceId, deviceName, spec, onDone }: { deviceId: number; deviceName: string; spec: any; onDone?: () => void }) {
  const [plan, setPlan] = useState<any>(null);
  const [result, setResult] = useState<any>(null);
  const [ack, setAck] = useState(false); const [confirm, setConfirm] = useState(""); const [rb, setRb] = useState(true); const [save, setSave] = useState(false);
  const planAct = useAction(async () => { setResult(null); setAck(false); setConfirm(""); setPlan(await post(`/devices/${deviceId}/changes/plan`, { spec })); });
  const applyAct = useAction(async () => { const r = await post(`/changes/${plan.id}/apply`, { ack_risk: ack, confirm_text: confirm || null, rollback_on_failure: rb, save }); setResult(r); onDone?.(); });
  const needAck = plan && ["high", "critical"].includes(plan.risk);
  const needName = plan?.risk === "critical";
  const canApply = plan && plan.status === "planned" && !plan.already_compliant && (!needAck || ack) && (!needName || confirm === deviceName);
  return (
    <div>
      <button onClick={() => planAct.run()} disabled={planAct.busy}>{planAct.busy ? "Checking device…" : "Preview change"}</button>
      <ErrorBox e={planAct.error} />
      {plan && (
        <div className="card" style={{ marginTop: 12 }}>
          <div className="row"><b>Risk:</b> <Badge v={plan.risk} /></div>
          {plan.risk_reasons.map((r: string, i: number) => <div key={i} className={plan.risk === "low" ? "muted" : "warn"}>{r}</div>)}
          <h3>What will be done</h3><ul>{plan.plan.summary.map((s: string, i: number) => <li key={i}>{s}</li>)}</ul>
          {plan.plan.warnings.map((w: string, i: number) => <div className="warn" key={i}>{w}</div>)}
          {plan.already_compliant && <div className="ok">The device already matches this request. Nothing needs to be sent.</div>}
          <details><summary>Device operations that will be sent (read-only view)</summary><pre>{plan.plan.commands.length ? plan.plan.commands.join("\n") : plan.plan.interactive.map((x: any) => x[0]).join("\n") || "(none)"}</pre></details>
          {plan.current_state != null && <details><summary>Current state of the affected section</summary><pre>{plan.current_state || "(empty)"}</pre></details>}
          {!result && (
            <>
              {needAck && <label><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I understand this change is <b>{plan.risk}</b> risk.</label>}
              {needName && <><label>Type the device name <b>{deviceName}</b> to confirm</label><input value={confirm} onChange={(e) => setConfirm(e.target.value)} /></>}
              <label><input type="checkbox" checked={rb} onChange={(e) => setRb(e.target.checked)} /> Roll back automatically if it fails (only possible for newly created objects)</label>
              <label><input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} /> Save to startup configuration afterwards (if the device needs it)</label>
              <p><button className={plan.risk === "low" ? "" : "danger"} disabled={!canApply || applyAct.busy} onClick={() => applyAct.run()}>{applyAct.busy ? "Applying…" : "Apply to device"}</button></p>
              <ErrorBox e={applyAct.error} />
            </>
          )}
          {result && (
            <>
              <div className={["applied", "skipped"].includes(result.status) ? "ok" : "err"}>Result: <Badge v={result.status} /></div>
              <ul className="timeline">{result.report.map((r: any, i: number) => <li key={i}><Badge v={r.status} /> <b>{r.step}</b> {r.detail}</li>)}</ul>
              {result.after_state && <><b>What changed</b><DiffView text={result.after_state} /></>}
            </>
          )}
        </div>
      )}
    </div>
  );
}
