import { useEffect, useMemo, useRef, useState } from "react";
import { get } from "../api";
import type { FieldMeta, ResourceMeta, Row } from "../types";
import Icon from "./Icon";

const metaCache = new Map<string, Promise<ResourceMeta>>();
export function resourceMeta(key: string) {
  if (!metaCache.has(key)) metaCache.set(key, get<ResourceMeta>(`/api/r/${key}/meta`).catch((e) => { metaCache.delete(key); throw e; }));
  return metaCache.get(key)!;
}

function optionLabel(row: Row, meta?: ResourceMeta | null) {
  if (!meta) return row.full_name || row.doc_no || row.name || row.code || row.id;
  const parts = meta.list_fields.slice(0, 3).map((f) => row[`${f}_label`] ?? row[f]).filter((v) => v !== null && v !== undefined && v !== "");
  return parts.join(" · ") || row.id;
}

/** Resolve dependent-selection filters: "=const", "@parent.field", or another form field. */
export function resolveDepends(depends: Record<string, string> | undefined, values: Record<string, any>, parent?: Row | null) {
  const out: Record<string, string> = {};
  for (const [param, ref] of Object.entries(depends ?? {})) {
    if (ref.startsWith("=")) out[param] = ref.slice(1);
    else if (ref.startsWith("@parent.")) {
      const v = parent?.[ref.slice(8)];
      if (v) out[param] = String(v);
    } else if (values[ref]) out[param] = String(values[ref]);
  }
  return out;
}

export function FkSelect({ target, value, label, onChange, filters, disabled, required }: {
  target: string; value: string | null; label?: string | null; onChange: (v: string | null, label?: string) => void;
  filters?: Record<string, string>; disabled?: boolean; required?: boolean;
}) {
  const [meta, setMeta] = useState<ResourceMeta | null>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [shown, setShown] = useState(label ?? "");
  const [err, setErr] = useState("");
  const timer = useRef<number>();
  const filterKey = JSON.stringify(filters ?? {});

  useEffect(() => setShown(label ?? ""), [label]);
  useEffect(() => { resourceMeta(target).then(setMeta).catch(() => setErr("No access")); }, [target]);
  useEffect(() => {
    if (!open) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      get<{ results: Row[] }>(`/api/r/${target}`, { search: q, page_size: 25, ...(filters ?? {}) })
        .then((r) => { setRows(r.results); setErr(""); })
        .catch((e) => setErr(e.message));
    }, 200);
    return () => window.clearTimeout(timer.current);
  }, [q, open, target, filterKey]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="fk" onBlur={(e) => !e.currentTarget.contains(e.relatedTarget as Node) && setOpen(false)}>
      <input
        value={open ? q : shown}
        placeholder={value ? shown : "Search…"}
        onFocus={() => { setOpen(true); setQ(""); }}
        onChange={(e) => setQ(e.target.value)}
        disabled={disabled}
        required={required && !value}
      />
      {value && !disabled && <button type="button" className="fk-clear" onClick={() => { onChange(null); setShown(""); }} aria-label="Clear"><Icon name="close" size={16} /></button>}
      {open && (
        <ul className="fk-menu">
          {err && <li className="muted">{err}</li>}
          {!err && rows.length === 0 && <li className="muted">No matching records{filters && Object.keys(filters).length ? " (filtered by earlier selections)" : ""}</li>}
          {rows.map((r) => (
            <li key={r.id}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => {
                const l = optionLabel(r, meta);
                onChange(r.id, l);
                setShown(l);
                setOpen(false);
              }}>{optionLabel(r, meta)}</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function LookupSelect({ source, value, onChange, multiple }: { source: string; value: any; onChange: (v: any) => void; multiple?: boolean }) {
  const [opts, setOpts] = useState<{ value: string; label: string }[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => { get(`/api/lookups/${source}`).then(setOpts).catch(() => setOpts([])); }, [source]);
  if (!multiple) {
    return (
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    );
  }
  const set = new Set<string>(value ?? []);
  const shown = opts.filter((o) => o.label.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="multi">
      <input placeholder="Filter…" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="multi-list">
        {shown.map((o) => (
          <label key={o.value} className="check">
            <input type="checkbox" checked={set.has(o.value)} onChange={(e) => {
              const next = new Set(set);
              if (e.target.checked) next.add(o.value); else next.delete(o.value);
              onChange([...next]);
            }} /> {o.label}
          </label>
        ))}
      </div>
    </div>
  );
}

export function FieldInput({ meta, value, onChange, values, parent, record, labelValue, onLabel }: {
  meta: FieldMeta; value: any; onChange: (v: any) => void; values: Record<string, any>; parent?: Row | null; record?: Row | null;
  labelValue?: string | null; onLabel?: (l?: string) => void;
}) {
  const filters = useMemo(() => ({ ...(meta.filter ?? {}), ...resolveDepends(meta.depends, values, parent) }), [meta, values, parent]);
  const disabled = meta.readonly;
  switch (meta.type) {
    case "fk":
      return meta.target ? (
        <FkSelect target={meta.target} value={value ?? null} label={labelValue} filters={filters} disabled={disabled} required={meta.required}
          onChange={(v, l) => { onChange(v); onLabel?.(l); }} />
      ) : <input value={value ?? ""} disabled />;
    case "boolean":
      return <label className="check"><input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} disabled={disabled} /> {meta.label}</label>;
    case "choice":
      return (
        <select value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} disabled={disabled} required={meta.required}>
          <option value="">—</option>
          {meta.choices?.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select>
      );
    case "textarea":
      return <textarea value={value ?? ""} onChange={(e) => onChange(e.target.value)} rows={3} disabled={disabled} required={meta.required} />;
    case "integer":
      return <input type="number" step={1} inputMode="numeric" value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)} disabled={disabled} required={meta.required} />;
    case "decimal":
      return <input type="number" step="any" inputMode="decimal" value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)} disabled={disabled} required={meta.required} />;
    case "date":
      return <input type="date" value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} disabled={disabled} required={meta.required} />;
    case "datetime":
      return <input type="datetime-local" value={toLocalInput(value)} onChange={(e) => onChange(e.target.value ? new Date(e.target.value).toISOString() : null)} disabled={disabled} required={meta.required} />;
    case "json":
      return <JsonInput value={value} onChange={onChange} disabled={disabled} />;
    case "password":
      return <input type="password" autoComplete="new-password" value={value ?? ""} onChange={(e) => onChange(e.target.value)} required={meta.required} />;
    case "lookup":
      return <LookupSelect source={meta.source!} value={value} onChange={onChange} />;
    case "multiselect":
      return <LookupSelect source={meta.source!} value={value} onChange={onChange} multiple />;
    case "checklist": {
      const items: { id: number | string; text: string; critical?: boolean }[] = record?.checklist ?? [];
      if (!items.length) return <span className="muted small">No checklist on this task.</span>;
      const ans = value ?? record?.answers ?? {};
      return (
        <div className="checklist">
          {items.map((it) => (
            <label key={it.id} className="checklist-row">
              <span>{it.text}{it.critical && <b className="crit"> critical</b>}</span>
              <select value={ans[String(it.id)] ?? ""} onChange={(e) => onChange({ ...ans, [String(it.id)]: e.target.value })}>
                <option value="">—</option><option value="yes">Pass / Yes</option><option value="no">Fail / No</option><option value="na">N/A</option>
              </select>
            </label>
          ))}
        </div>
      );
    }
    default:
      return <input type={meta.type === "email" ? "email" : "text"} value={value ?? ""} onChange={(e) => onChange(e.target.value)} disabled={disabled} required={meta.required} />;
  }
}

function toLocalInput(v?: string | null) {
  if (!v) return "";
  const d = new Date(v);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function JsonInput({ value, onChange, disabled }: { value: any; onChange: (v: any) => void; disabled?: boolean }) {
  const [text, setText] = useState(() => (value === undefined || value === null ? "" : JSON.stringify(value, null, 2)));
  const [bad, setBad] = useState(false);
  return (
    <>
      <textarea className={`mono ${bad ? "invalid" : ""}`} rows={5} value={text} disabled={disabled} onChange={(e) => {
        setText(e.target.value);
        try { onChange(e.target.value.trim() ? JSON.parse(e.target.value) : null); setBad(false); } catch { setBad(true); }
      }} />
      {bad && <span className="small danger-text">Not valid JSON yet</span>}
    </>
  );
}
