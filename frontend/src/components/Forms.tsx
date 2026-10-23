import { FormEvent, useMemo, useRef, useState } from "react";
import { api, ApiError, newIdempotencyKey } from "../api";
import type { ActionMeta, FieldMeta, ResourceMeta, Row } from "../types";
import { FieldInput } from "./Field";
import { ErrorBox, Modal } from "./ui";

function errorText(e: unknown) {
  if (!(e instanceof ApiError)) return String(e);
  const d = e.details;
  if (d && typeof d === "object" && Object.keys(d).length && !("blockers" in d) && !("missing" in d)) {
    const parts = Object.entries(d).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : v}`);
    return `${e.message}${parts.length ? " - " + parts.join("; ") : ""}`;
  }
  return e.message;
}

/** Create / edit form generated from resource metadata. */
/** `defaults` are fixed by the caller (hidden); `initial` and the server's meta.defaults are only pre-filled. */
export function RecordForm({ meta, record, defaults, initial, parent, onSaved, onCancel }: {
  meta: ResourceMeta; record?: Row | null; defaults?: Record<string, any>; initial?: Record<string, any>; parent?: Row | null;
  onSaved: (r: Row) => void; onCancel: () => void;
}) {
  const creating = !record;
  const names = creating ? [...meta.create_only_fields, ...meta.form_fields] : meta.form_fields;
  const [values, setValues] = useState<Record<string, any>>(() => {
    const v: Record<string, any> = creating ? { ...(meta.defaults ?? {}), ...(initial ?? {}), ...(defaults ?? {}) } : { ...(defaults ?? {}) };
    if (record) for (const n of names) v[n] = record[n];
    return v;
  });
  const [labels, setLabels] = useState<Record<string, any>>(() => {
    const l: Record<string, any> = {};
    if (record) for (const n of names) if (record[`${n}_items`] ?? record[`${n}_label`]) l[n] = record[`${n}_items`] ?? record[`${n}_label`];
    return l;
  });
  const uploads = creating ? meta.required_uploads ?? [] : [];
  const [files, setFiles] = useState<Record<string, File>>({});
  const visible = (n: string) => Object.entries(meta.fields[n]?.show_if ?? {}).every(([k, v]) => values[k] === v);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [held, setHeld] = useState<string | null>(null);
  const idem = useRef(newIdempotencyKey());

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body: Record<string, any> = {};
      for (const n of names) {
        if (!visible(n) && creating) continue;
        if (creating && (values[n] === undefined || values[n] === "")) continue;
        if (!creating && values[n] === record![n]) continue;
        body[n] = values[n];
      }
      let saved: Row;
      if (creating && uploads.length) {
        // Required files go in the same request, so the record is never saved without them.
        const form = new FormData();
        form.append("payload", JSON.stringify(body));
        for (const u of uploads) form.append(u.purpose, files[u.purpose]);
        saved = await api("POST", `/api/r/${meta.key}`, undefined, { form, idempotencyKey: idem.current });
      } else if (creating) saved = await api("POST", `/api/r/${meta.key}`, reason ? { ...body, reason } : body, { idempotencyKey: idem.current });
      else saved = await api("PATCH", `/api/r/${meta.key}/${record!.id}`, { ...body, row_version: record!.row_version, reason }, { idempotencyKey: idem.current });
      if ((saved as any).pending_approval) {
        setHeld((saved as any).message);
        return;
      }
      onSaved(saved);
    } catch (e) {
      idem.current = newIdempotencyKey();
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const hidden = new Set(Object.keys(defaults ?? {}));
  if (held) {
    return (
      <div className="stack">
        <div className="alert ok"><b>Sent for approval.</b> {held}</div>
        <div className="form-actions"><button className="btn primary" onClick={onCancel}>Close</button></div>
      </div>
    );
  }
  return (
    <form onSubmit={submit} className="form-grid">
      {names.filter((n) => !hidden.has(n) && visible(n)).map((n) => {
        const f: FieldMeta = meta.fields[n] ?? { name: n, label: n, type: "text" };
        return (
          <label key={n} className={`field ${f.type === "textarea" || f.type === "json" ? "span2" : ""}`}>
            {f.type !== "boolean" && <span>{f.label}{f.required && <b className="req">*</b>}</span>}
            <FieldInput meta={f} value={values[n]} values={values} parent={parent} labelValue={labels[n]}
              onLabel={(l) => setLabels((s) => ({ ...s, [n]: l }))}
              onChange={(v) => setValues((s) => ({ ...s, [n]: v }))} />
            {f.help && <small className="muted">{f.help}</small>}
          </label>
        );
      })}
      {uploads.map((u) => (
        <label key={u.purpose} className="field">
          <span>{u.label}<b className="req">*</b></span>
          <input type="file" accept={u.accept} required onChange={(e) => {
            const file = e.target.files?.[0];
            setFiles((s) => { const next = { ...s }; if (file) next[u.purpose] = file; else delete next[u.purpose]; return next; });
          }} />
          <small className="muted">{u.accept.includes("pdf") ? "PDF file" : "Photo (JPEG or PNG)"}</small>
        </label>
      ))}
      {!creating && (
        <label className="field span2"><span>Reason for change (recorded in history)</span>
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Optional" /></label>
      )}
      <ErrorBox error={error} />
      <div className="form-actions span2">
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
        <button className="btn primary" disabled={busy}>{creating ? `Create ${meta.singular.toLowerCase()}` : "Save changes"}</button>
      </div>
    </form>
  );
}

/** Run a workflow action. One idempotency key per dialog so double taps never double-post. */
export function ActionDialog({ meta, action, record, onDone, onClose }: {
  meta: ResourceMeta; action: ActionMeta; record?: Row | null; onDone: (result: any) => void; onClose: () => void;
}) {
  const [values, setValues] = useState<Record<string, any>>({});
  const [labels, setLabels] = useState<Record<string, string | undefined>>({});
  const [error, setError] = useState("");
  const [details, setDetails] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const idem = useRef(newIdempotencyKey());
  const fields = useMemo(() => action.fields ?? [], [action]);

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    if (action.confirm && !window.confirm(action.confirm)) return;
    setBusy(true);
    setError("");
    setDetails([]);
    try {
      const url = record ? `/api/r/${meta.key}/${record.id}/actions/${action.name}` : `/api/r/${meta.key}/actions/${action.name}`;
      const r = await api("POST", url, values, { idempotencyKey: idem.current });
      if (r && !r.id && !r._resource) setResult(r);
      else onDone(r);
    } catch (e) {
      idem.current = newIdempotencyKey();
      setError(errorText(e));
      if (e instanceof ApiError) {
        const d: any = e.details;
        setDetails([...(d?.blockers ?? []), ...(d?.missing ?? []), ...(d?.shortages ?? []).map((s: any) => `${s.item}: need ${s.required}, available ${s.available}, incoming ${s.incoming}`)]);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={action.label} onClose={onClose} wide={fields.length > 4}>
      {result ? (
        <div className="stack">
          {result.pending_approval ? (
            <div className="alert ok"><b>Sent for approval.</b> {result.message}</div>
          ) : (
            <>
              {result.message && <div className="alert ok">{result.message}</div>}
              <pre className="result">{JSON.stringify(result, null, 2)}</pre>
            </>
          )}
          <div className="form-actions"><button className="btn primary" onClick={() => onDone(result)}>Done</button></div>
        </div>
      ) : (
        <form onSubmit={submit} className="form-grid">
          {action.confirm && <div className="alert warn span2">{action.confirm}</div>}
          {fields.map((f) => (
            <label key={f.name} className={`field ${["textarea", "checklist", "multiselect"].includes(f.type) ? "span2" : ""}`}>
              {f.type !== "boolean" && <span>{f.label}{f.required && <b className="req">*</b>}</span>}
              <FieldInput meta={{ ...f, label: f.label }} value={values[f.name]} values={values} record={record} labelValue={labels[f.name]}
                onLabel={(l) => setLabels((s) => ({ ...s, [f.name]: l }))}
                onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />
            </label>
          ))}
          {action.reason_required && (
            <label className="field span2"><span>Reason<b className="req">*</b></span>
              <textarea rows={2} required minLength={5} value={values.reason ?? ""} onChange={(e) => setValues((s) => ({ ...s, reason: e.target.value }))} /></label>
          )}
          {!fields.length && !action.reason_required && !action.confirm && <p className="muted span2">Confirm: {action.label}?</p>}
          <ErrorBox error={error} />
          {details.length > 0 && <ul className="blockers span2">{details.map((d, i) => <li key={i}>{d}</li>)}</ul>}
          <div className="form-actions span2">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button className={`btn ${action.tone === "danger" ? "danger" : "primary"}`} disabled={busy}>{action.label}</button>
          </div>
        </form>
      )}
    </Modal>
  );
}

/** Owner-only permanent delete. Records still used elsewhere are refused by the server with a plain explanation. */
export function DeleteDialog({ meta, record, onClose, onDeleted }: { meta: ResourceMeta; record: Row; onClose: () => void; onDeleted: () => void }) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const name = String(record[meta.title_field] ?? record[`${meta.title_field}_label`] ?? "").trim() || `this ${meta.singular.toLowerCase()}`;

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("DELETE", `/api/r/${meta.key}/${record.id}`, { reason });
      onDeleted();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  return (
    <Modal title={`Delete ${meta.singular.toLowerCase()}`} onClose={onClose}>
      <form onSubmit={submit} className="stack">
        <p>Permanently delete <b>{name}</b>?{meta.inlines.length > 0 && " Lines that belong only to it (such as order lines) are deleted with it."}</p>
        <p className="small muted">This cannot be undone. The deletion and a copy of the record are kept in the audit log.
          If other records still use it, nothing is deleted and you will be told what uses it.</p>
        <label className="field"><span>Reason<b className="req">*</b></span>
          <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Entered by mistake" /></label>
        <ErrorBox error={error} />
        <div className="row gap">
          <span className="spacer" />
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn danger" disabled={busy || reason.trim().length < 5}>{busy ? "Deleting…" : "Delete permanently"}</button>
        </div>
      </form>
    </Modal>
  );
}
