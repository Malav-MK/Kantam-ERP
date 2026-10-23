import { useEffect, useMemo, useRef, useState } from "react";
import { get } from "../api";
import type { FieldMeta, ResourceMeta, Row } from "../types";
import { RecordForm } from "./Forms";
import Icon from "./Icon";
import { Modal } from "./ui";

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

/** Searchable dropdown for a linked record, with the target's category filters and "+ Create new". */
export function FkSelect({ target, value, label, onChange, filters, filterDefault, disabled, required, exclude }: {
  target: string; value: string | null; label?: string | null; onChange: (v: string | null, label?: string) => void;
  filters?: Record<string, string>; filterDefault?: Record<string, string>; disabled?: boolean; required?: boolean; exclude?: string[];
}) {
  const [meta, setMeta] = useState<ResourceMeta | null>(null);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [shown, setShown] = useState(label ?? "");
  const [err, setErr] = useState("");
  const [picked, setPicked] = useState<Record<string, string>>(filterDefault ?? {});
  const [creating, setCreating] = useState(false);
  const timer = useRef<number>();
  const fixed = filters ?? {};
  const query = { ...picked, ...fixed };
  const filterKey = JSON.stringify(query);
  // Category-style filters of the target the user may change (fixed ones come from earlier selections).
  const choices = (meta?.filters ?? []).filter((f) => f.type === "choice" && !(f.name in fixed)).slice(0, 2);

  useEffect(() => setShown(label ?? ""), [label]);
  useEffect(() => { resourceMeta(target).then(setMeta).catch(() => setErr("No access")); }, [target]);
  useEffect(() => {
    if (!open) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      get<{ results: Row[] }>(`/api/r/${target}`, { search: q, page_size: 25, ...query })
        .then((r) => { setRows(r.results); setErr(""); })
        .catch((e) => setErr(e.message));
    }, 200);
    return () => window.clearTimeout(timer.current);
  }, [q, open, target, filterKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const choose = (r: Row) => {
    const l = optionLabel(r, meta);
    onChange(r.id, l);
    setShown(exclude ? "" : l);
    setOpen(false);
  };
  const formFields = new Set([...(meta?.form_fields ?? []), ...(meta?.create_only_fields ?? [])]);
  const keep = (o: Record<string, string>) => Object.fromEntries(Object.entries(o).filter(([k, v]) => v && formFields.has(k)));
  const listed = rows.filter((r) => !exclude?.includes(r.id));

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
      {value && !disabled && !exclude && <button type="button" className="fk-clear" onClick={() => { onChange(null); setShown(""); }} aria-label="Clear"><Icon name="close" size={16} /></button>}
      {open && (
        <ul className="fk-menu">
          {choices.length > 0 && (
            <li className="fk-filters">
              {choices.map((f) => (
                <label key={f.name}>
                  <span>{f.label}</span>
                  <select value={picked[f.name] ?? ""} onChange={(e) => setPicked((p) => ({ ...p, [f.name]: e.target.value }))}>
                    <option value="">All</option>
                    {f.choices?.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                  </select>
                </label>
              ))}
            </li>
          )}
          {err && <li className="muted">{err}</li>}
          {!err && listed.length === 0 && <li className="muted">No matching records{Object.values(query).some(Boolean) ? " (filtered)" : ""}</li>}
          {listed.map((r) => (
            <li key={r.id}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => choose(r)}>{optionLabel(r, meta)}</button>
            </li>
          ))}
          {meta?.can_create && (
            <li className="fk-create">
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => { setOpen(false); setCreating(true); }}>
                <Icon name="plus" size={14} />Create new {meta.singular.toLowerCase()}
              </button>
            </li>
          )}
        </ul>
      )}
      {creating && meta && (
        <Modal title={`New ${meta.singular.toLowerCase()}`} onClose={() => setCreating(false)} wide>
          <RecordForm meta={meta} defaults={keep(fixed)} initial={keep(picked)} onCancel={() => setCreating(false)}
            onSaved={(r) => { setCreating(false); choose(r); }} />
        </Modal>
      )}
    </div>
  );
}

/** Several linked records (e.g. the customers of an artwork): chips plus a dropdown to add more. */
function FkMultiSelect({ target, value, items, onChange, filters, filterDefault, disabled }: {
  target: string; value: string[]; items: { id: string; label: string }[]; onChange: (v: string[], items: { id: string; label: string }[]) => void;
  filters?: Record<string, string>; filterDefault?: Record<string, string>; disabled?: boolean;
}) {
  const known = new Map(items.map((i) => [i.id, i.label]));
  return (
    <div className="fk-multi">
      {value.length > 0 && (
        <div className="chips">
          {value.map((id) => (
            <span key={id} className="chip">{known.get(id) ?? id}
              {!disabled && <button type="button" aria-label="Remove" onClick={() => onChange(value.filter((x) => x !== id), items.filter((i) => i.id !== id))}><Icon name="close" size={12} /></button>}
            </span>
          ))}
        </div>
      )}
      {!disabled && <FkSelect target={target} value={null} filters={filters} filterDefault={filterDefault} exclude={value}
        onChange={(v, l) => v && onChange([...value, v], [...items, { id: v, label: l ?? v }])} />}
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
  labelValue?: any; onLabel?: (l?: any) => void;
}) {
  const filters = useMemo(() => ({ ...(meta.filter ?? {}), ...resolveDepends(meta.depends, values, parent) }), [meta, values, parent]);
  const disabled = meta.readonly;
  switch (meta.type) {
    case "fk":
      return meta.target ? (
        <FkSelect target={meta.target} value={value ?? null} label={labelValue} filters={filters} filterDefault={meta.filter_default} disabled={disabled}
          required={meta.required} onChange={(v, l) => { onChange(v); onLabel?.(l); }} />
      ) : <input value={value ?? ""} disabled />;
    case "fk_multi":
      return meta.target ? (
        <FkMultiSelect target={meta.target} value={value ?? []} items={Array.isArray(labelValue) ? labelValue : []} filters={filters}
          filterDefault={meta.filter_default} disabled={disabled} onChange={(v, items) => { onChange(v); onLabel?.(items); }} />
      ) : null;
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
