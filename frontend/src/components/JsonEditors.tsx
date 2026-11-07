import { useState } from "react";
import type { FieldMeta, JsonColumn } from "../types";
import Icon from "./Icon";

/** The structured fields are stored as JSON but entered as rows, so nobody has to type brackets and quotes. */
export default function StructuredInput({ meta, value, onChange, disabled }: { meta: FieldMeta; value: any; onChange: (v: any) => void; disabled?: boolean }) {
  return meta.editor?.columns
    ? <RowsEditor meta={meta} value={value} onChange={onChange} disabled={disabled} />
    : <ChecksEditor meta={meta} value={value} onChange={onChange} disabled={disabled} />;
}

type Row = Record<string, any>;

function CellInput({ col, row, onChange, disabled }: { col: JsonColumn; row: Row; onChange: (v: any) => void; disabled?: boolean }) {
  const v = row[col.name];
  if (col.type === "boolean") return <input type="checkbox" aria-label={col.label} checked={!!v} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />;
  if (col.type === "pass_fail") {
    return (
      <select aria-label={col.label} value={v === true ? "pass" : v === false ? "fail" : ""} disabled={disabled}
        onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value === "pass")}>
        <option value="">—</option><option value="pass">Pass</option><option value="fail">Fail</option>
      </select>
    );
  }
  if (col.choices) {
    return (
      <select aria-label={col.label} value={v ?? ""} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {col.choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
      </select>
    );
  }
  return <input aria-label={col.label} placeholder={col.placeholder} value={v ?? ""} disabled={disabled} onChange={(e) => onChange(e.target.value)} />;
}

function RowsEditor({ meta, value, onChange, disabled }: { meta: FieldMeta; value: any; onChange: (v: any) => void; disabled?: boolean }) {
  const editor = meta.editor!;
  const columns = editor.columns!;
  const [rows, setRows] = useState<Row[]>(() => (Array.isArray(value) ? value : []));
  // Rows nobody has typed in yet stay on screen but are not saved. Other keys on a row are kept as they were.
  const filled = (r: Row) => columns.some((c) => c.type !== "boolean" && c.type !== "pass_fail" && String(r[c.name] ?? "").trim());
  const commit = (next: Row[]) => { setRows(next); onChange(next.filter(filled)); };
  const add = () => {
    const id = Math.max(0, ...rows.map((r) => Number(r.id) || 0)) + 1;
    setRows([...rows, editor.auto_id ? { id } : {}]);
  };
  return (
    <div className="json-editor">
      {rows.length > 0 && (
        <div className="table-wrap"><table className="mini">
          <thead><tr>{columns.map((c) => <th key={c.name}>{c.label}</th>)}<th><span className="sr-only">Remove</span></th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {columns.map((c) => (
                  <td key={c.name}><CellInput col={c} row={r} disabled={disabled}
                    onChange={(v) => commit(rows.map((x, j) => (j === i ? { ...x, [c.name]: v } : x)))} /></td>
                ))}
                <td>{!disabled && <button type="button" className="icon-btn" aria-label="Remove row" onClick={() => commit(rows.filter((_, j) => j !== i))}><Icon name="close" size={14} /></button>}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {!disabled && <button type="button" className="btn small" onClick={add}><Icon name="plus" size={14} />{editor.add_label ?? "Add row"}</button>}
    </div>
  );
}

function ChecksEditor({ meta, value, onChange, disabled }: { meta: FieldMeta; value: any; onChange: (v: any) => void; disabled?: boolean }) {
  const editor = meta.editor ?? {};
  const fixed = editor.fixed ?? [];
  const known = new Set(fixed.map((f) => f.key));
  const start: Record<string, any> = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const [flags, setFlags] = useState<Record<string, any>>(() => Object.fromEntries(Object.entries(start).filter(([k]) => known.has(k))));
  const [rows, setRows] = useState<{ key: string; value: any }[]>(() => Object.entries(start).filter(([k]) => !known.has(k)).map(([key, v]) => ({ key, value: v })));
  const commit = (f: Record<string, any>, r: { key: string; value: any }[]) => {
    setFlags(f);
    setRows(r);
    onChange({ ...f, ...Object.fromEntries(r.filter((x) => x.key.trim()).map((x) => [x.key.trim(), x.value])) });
  };
  return (
    <div className="json-editor">
      {fixed.map((f) => (
        <label key={f.key} className="check">
          <input type="checkbox" checked={!!flags[f.key]} disabled={disabled} onChange={(e) => {
            const next = { ...flags };
            if (e.target.checked) next[f.key] = true; else delete next[f.key];
            commit(next, rows);
          }} /> {f.label}
        </label>
      ))}
      {rows.length > 0 && (
        <div className="table-wrap"><table className="mini">
          <thead><tr><th>{editor.key_label ?? "Check"}</th><th>Result</th><th><span className="sr-only">Remove</span></th></tr></thead>
          <tbody>
            {rows.map((r, i) => {
              const set = (patch: Partial<{ key: string; value: any }>) => commit(flags, rows.map((x, j) => (j === i ? { ...x, ...patch } : x)));
              return (
                <tr key={i}>
                  <td><input aria-label={editor.key_label ?? "Check"} placeholder={editor.key_placeholder} value={r.key} disabled={disabled} onChange={(e) => set({ key: e.target.value })} /></td>
                  <td>{typeof r.value === "boolean"
                    ? <label className="check"><input type="checkbox" checked={r.value} disabled={disabled} onChange={(e) => set({ value: e.target.checked })} /> {r.value ? "OK" : "Not OK"}</label>
                    : <input aria-label="Result" value={String(r.value ?? "")} disabled={disabled} onChange={(e) => set({ value: e.target.value })} />}</td>
                  <td>{!disabled && <button type="button" className="icon-btn" aria-label="Remove row" onClick={() => commit(flags, rows.filter((_, j) => j !== i))}><Icon name="close" size={14} /></button>}</td>
                </tr>
              );
            })}
          </tbody>
        </table></div>
      )}
      {!disabled && <button type="button" className="btn small" onClick={() => setRows([...rows, { key: "", value: false }])}><Icon name="plus" size={14} />{editor.add_label ?? "Add check"}</button>}
    </div>
  );
}
