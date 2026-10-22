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
export function RecordForm({ meta, record, defaults, parent, onSaved, onCancel }: {
  meta: ResourceMeta; record?: Row | null; defaults?: Record<string, any>; parent?: Row | null;
  onSaved: (r: Row) => void; onCancel: () => void;
}) {
  const creating = !record;
  const names = creating ? [...meta.create_only_fields, ...meta.form_fields] : meta.form_fields;
  const [values, setValues] = useState<Record<string, any>>(() => {
    const v: Record<string, any> = { ...(defaults ?? {}) };
    if (record) for (const n of names) v[n] = record[n];
    return v;
  });
  const [labels, setLabels] = useState<Record<string, string | undefined>>(() => {
    const l: Record<string, string> = {};
    if (record) for (const n of names) if (record[`${n}_label`]) l[n] = record[`${n}_label`];
    return l;
  });
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [held, setHeld] = useState<string | null>(null);
  const idem = useRef(newIdempotencyKey());
  const rules = (meta.approval_rules ?? []).filter((r) => r.operation === (creating ? "create" : "update"));
  const ruleHint = rules.map((r) => (r.condition === "fields_changed" ? `${r.name} (when ${r.fields.join(", ")} change)` : r.name)).join("; ");

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body: Record<string, any> = {};
      for (const n of names) {
        if (creating && (values[n] === undefined || values[n] === "")) continue;
        if (!creating && values[n] === record![n]) continue;
        body[n] = values[n];
      }
      let saved: Row;
      if (creating) saved = await api("POST", `/api/r/${meta.key}`, reason ? { ...body, reason } : body, { idempotencyKey: idem.current });
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
      {names.filter((n) => !hidden.has(n)).map((n) => {
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
      {rules.length > 0 && <div className="alert warn span2"><b>Maker / checker:</b> {ruleHint} - an authorised checker must approve before it takes effect.</div>}
      {(!creating || rules.length > 0) && (
        <label className="field span2"><span>Reason for change (recorded in history{rules.length ? ", shown to the checker" : ""}){rules.length > 0 && <b className="req">*</b>}</span>
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={rules.length ? "Why is this change needed?" : "Optional for drafts"} /></label>
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
  const rule = (meta.approval_rules ?? []).find((r) => r.operation === "action" && r.action === action.name);

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
          {rule && <div className="alert warn span2"><b>Maker / checker:</b> {rule.name}{rule.condition === "amount_over" ? ` above ₹${Number(rule.amount_threshold).toLocaleString("en-IN")}` : ""} - this will be held until {rule.approvals > 1 ? `${rule.approvals} different checkers approve` : "a different authorised checker approves"}.</div>}
          {fields.map((f) => (
            <label key={f.name} className={`field ${["textarea", "checklist", "multiselect"].includes(f.type) ? "span2" : ""}`}>
              {f.type !== "boolean" && <span>{f.label}{f.required && <b className="req">*</b>}</span>}
              <FieldInput meta={{ ...f, label: f.label }} value={values[f.name]} values={values} record={record} labelValue={labels[f.name]}
                onLabel={(l) => setLabels((s) => ({ ...s, [f.name]: l }))}
                onChange={(v) => setValues((s) => ({ ...s, [f.name]: v }))} />
            </label>
          ))}
          {rule && !action.reason_required && (
            <label className="field span2"><span>Reason for the checker<b className="req">*</b></span>
              <textarea rows={2} minLength={5} value={values.approval_reason ?? ""} onChange={(e) => setValues((s) => ({ ...s, approval_reason: e.target.value }))} /></label>
          )}
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
