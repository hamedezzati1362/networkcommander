import { ReactNode, useState } from "react";
import { ApiError } from "../api";

export const Badge = ({ v }: { v?: string | null }) => <span className={`badge b-${v ?? "unknown"}`}>{(v ?? "unknown").replace(/_/g, " ")}</span>;

export function ErrorBox({ e }: { e: unknown }) {
  if (!e) return null;
  const err = e as ApiError;
  return <div className="err" role="alert"><b>{err.message ?? String(e)}</b>{err.hint && <div>{err.hint}</div>}</div>;
}

export function DiffView({ text }: { text: string }) {
  return (
    <pre className="diff">
      {text.split("\n").map((l, i) => <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : ""}>{l || " "}</div>)}
    </pre>
  );
}

export function useAction<A extends any[], R>(fn: (...a: A) => Promise<R>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = async (...a: A): Promise<R | undefined> => {
    setBusy(true); setError(null);
    try { return await fn(...a); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return { run, busy, error, setError };
}

export const Card = ({ title, children, right }: { title?: string; children: ReactNode; right?: ReactNode }) => (
  <div className="card">{(title || right) && <div className="top"><h3 style={{ margin: 0 }}>{title}</h3>{right}</div>}{children}</div>
);
