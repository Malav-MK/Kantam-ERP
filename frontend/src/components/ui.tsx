import { ReactNode, useEffect, useRef } from "react";
import Icon from "./Icon";
import { toneFor } from "../format";

export function Pill({ value, label }: { value?: string; label?: string }) {
  if (!value) return null;
  return <span className={`pill ${toneFor(value)}`}>{label ?? value.replace(/_/g, " ")}</span>;
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Only the top-most dialog closes (a "Create new" form can open over another form).
    const onKey = (e: KeyboardEvent) => {
      const all = document.querySelectorAll(".overlay");
      if (e.key === "Escape" && all[all.length - 1] === ref.current) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div ref={ref} className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
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
      <summary>How to use this screen</summary>
      {help.why && <p><b>What it is for:</b> {help.why}</p>}
      {help.steps && <p><b>How to do it:</b> {help.steps}</p>}
      {help.effect && <p><b>What happens next:</b> {help.effect}</p>}
      {help.boundary && <p><b>Good to know:</b> {help.boundary}</p>}
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
