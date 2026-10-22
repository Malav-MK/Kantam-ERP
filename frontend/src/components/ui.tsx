import { ReactNode, useEffect } from "react";
import Icon from "./Icon";
import { toneFor } from "../format";

export function Pill({ value, label }: { value?: string; label?: string }) {
  if (!value) return null;
  return <span className={`pill ${toneFor(value)}`}>{label ?? value.replace(/_/g, " ")}</span>;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header><h3>{title}</h3><button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="close" size={20} /></button></header>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorBox({ error }: { error?: string }) {
  return error ? <div className="alert danger" role="alert">{error}</div> : null;
}

export function Help({ help }: { help?: { why?: string; steps?: string; effect?: string; boundary?: string } }) {
  if (!help || !Object.keys(help).length) return null;
  return (
    <details className="help">
      <summary>Help / why</summary>
      {help.why && <p><b>Why:</b> {help.why}</p>}
      {help.steps && <p><b>Steps:</b> {help.steps}</p>}
      {help.effect && <p><b>Effect:</b> {help.effect}</p>}
      {help.boundary && <p><b>Important boundary:</b> {help.boundary}</p>}
    </details>
  );
}

export function Stat({ label, value, tone, sub, onClick }: { label: string; value: ReactNode; tone?: string; sub?: ReactNode; onClick?: () => void }) {
  return (
    <button className={`stat ${tone ?? ""}`} onClick={onClick} disabled={!onClick}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </button>
  );
}
