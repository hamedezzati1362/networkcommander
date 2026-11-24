/** Renders a form from the backend's JSON schema of a configuration module (no vendor logic in the UI). */
import { useMemo } from "react";

type Props = {
  schema: any;
  value: Record<string, any>;
  onChange: (v: Record<string, any>) => void;
  /** restrict enum choices, e.g. {kind: ["vlan_database"]} from the device's detected capabilities */
  allowed?: Record<string, string[]>;
};

const pretty = (s: string) => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

function resolve(p: any): { type: string; enum?: any[]; min?: number; max?: number } {
  const cand = p.anyOf ? p.anyOf.find((x: any) => x.type !== "null") ?? p.anyOf[0] : p;
  if (cand.enum) return { type: "enum", enum: cand.enum };
  if (cand.const !== undefined) return { type: "const" };
  if (cand.type === "array") return { type: "list" };
  return { type: cand.type ?? "string", min: cand.minimum, max: cand.maximum };
}

export function SchemaForm({ schema, value, onChange, allowed }: Props) {
  const props = schema.properties as Record<string, any>;
  const required = new Set<string>(schema.required ?? []);
  const fields = useMemo(() => Object.entries(props).filter(([k]) => k !== "module"), [props]);
  const set = (k: string, v: any) => {
    const next = { ...value };
    if (v === "" || v === undefined || (Array.isArray(v) && v.length === 0)) delete next[k]; else next[k] = v;
    onChange(next);
  };
  return (
    <div>
      {fields.map(([k, p]) => {
        const r = resolve(p);
        const label = p.title && p.title !== pretty(k) ? p.title : pretty(k);
        const id = `f-${k}`;
        let input;
        if (r.type === "enum") {
          const opts = (allowed?.[k] ? r.enum!.filter((o) => allowed[k].includes(o)) : r.enum!) as string[];
          input = (
            <select id={id} value={value[k] ?? p.default ?? ""} onChange={(e) => set(k, e.target.value)}>
              {!required.has(k) && p.default === undefined && <option value="">(unchanged)</option>}
              {opts.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          );
        } else if (r.type === "boolean") {
          input = (
            <select id={id} value={value[k] === undefined ? "" : String(value[k])} onChange={(e) => set(k, e.target.value === "" ? undefined : e.target.value === "true")}>
              <option value="">(unchanged)</option><option value="true">Yes / enabled</option><option value="false">No / disabled</option>
            </select>
          );
        } else if (r.type === "integer") {
          input = <input id={id} type="number" min={r.min} max={r.max} value={value[k] ?? ""} onChange={(e) => set(k, e.target.value === "" ? undefined : Number(e.target.value))} />;
        } else if (r.type === "list") {
          input = <input id={id} placeholder="comma separated" value={(value[k] ?? []).join(", ")} onChange={(e) => set(k, e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />;
        } else {
          input = <input id={id} type={k === "community" ? "password" : "text"} autoComplete="off" value={value[k] ?? ""} onChange={(e) => set(k, e.target.value)} />;
        }
        return (
          <div key={k}>
            <label htmlFor={id}>{label}{required.has(k) && " *"} {p.description && <span className="help">— {p.description}</span>}</label>
            {input}
          </div>
        );
      })}
    </div>
  );
}
