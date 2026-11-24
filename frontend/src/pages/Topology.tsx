import { useCallback, useEffect, useState } from "react";
import ReactFlow, { Background, Controls, Edge, MiniMap, Node } from "reactflow";
import "reactflow/dist/style.css";
import { useNavigate } from "react-router-dom";
import { get, post } from "../api";
import { Card, ErrorBox, useAction } from "../components/ui";

const ICON: Record<string, string> = { switch: "⇄", router: "⌘", firewall: "▦", other: "●", unmanaged: "?" };
const COLOR: Record<string, string> = { online: "#15803d", offline: "#b91c1c", auth_failed: "#b91c1c", error: "#b91c1c", unknown: "#b45309" };

export default function Topology() {
  const nav = useNavigate();
  const [nodes, setNodes] = useState<Node[]>([]); const [edges, setEdges] = useState<Edge[]>([]); const [info, setInfo] = useState(""); const [filter, setFilter] = useState(""); const [devs, setDevs] = useState<any[]>([]);
  const load = useCallback(async () => {
    const t = await get("/topology"); setInfo(t.note); setDevs(t.nodes.filter((n: any) => n.inventory));
    const per = Math.max(3, Math.ceil(Math.sqrt(t.nodes.length)));
    setNodes(t.nodes.filter((n: any) => !filter || n.label.toLowerCase().includes(filter.toLowerCase())).map((n: any, i: number) => ({
      id: n.id, position: { x: (i % per) * 230, y: Math.floor(i / per) * 140 }, data: { label: <div><b>{ICON[n.role] ?? "●"} {n.label}</b><div style={{ fontSize: 11 }}>{n.ip ?? ""} {n.role}</div></div>, device_id: n.device_id },
      style: { border: `2px solid ${n.inventory ? COLOR[n.status] ?? "#888" : "#888"}`, borderStyle: n.inventory ? "solid" : "dashed", borderRadius: 8, padding: 6, width: 190, background: "var(--panel)", color: "var(--text)" },
    })));
    setEdges(t.links.map((l: any) => ({ id: String(l.id), source: l.source, target: l.target, label: `${l.local_if}${l.remote_if ? " ↔ " + l.remote_if : ""}`, style: { stroke: l.origin === "manual" ? "#7c3aed" : "#64748b" }, labelStyle: { fontSize: 10 } })));
  }, [filter]);
  useEffect(() => { load().catch(() => {}); }, [load]);
  const collect = useAction(async () => { for (const d of devs) { try { await post(`/devices/${d.device_id}/neighbors/collect`); } catch { /* per-device failures are reported below */ } } await load(); });
  return (
    <>
      <div className="top"><h2>Network Topology</h2><div className="row"><input placeholder="Filter by name" value={filter} onChange={(e) => setFilter(e.target.value)} /><button onClick={() => collect.run()} disabled={collect.busy}>{collect.busy ? "Collecting…" : "Collect LLDP/CDP from all devices"}</button></div></div>
      <ErrorBox e={collect.error} /><p className="muted">{info} Dashed nodes are neighbours seen in a device's neighbour table but not in your inventory. Click an inventory device to open it.</p>
      <Card><div style={{ height: 560 }}>
        <ReactFlow nodes={nodes} edges={edges} fitView onNodeClick={(_, n) => n.data.device_id && nav(`/devices/${n.data.device_id}`)}><Background /><Controls /><MiniMap /></ReactFlow>
      </div>{nodes.length === 0 && <p className="muted">No devices yet.</p>}</Card>
    </>
  );
}
