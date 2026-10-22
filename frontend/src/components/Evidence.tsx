import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, get, newIdempotencyKey, openProtected, protectedBlobUrl } from "../api";
import { fmtDateTime } from "../format";
import { ErrorBox } from "./ui";
import Icon from "./Icon";

type Att = { id: string; purpose: string; proof_round: number; name: string; mime_type: string; size: number; sha256: string;
  classification: string; capture_source: string; uploaded_by: string; uploaded_at: string; frozen: boolean; note: string };

const PURPOSES: Record<string, string[]> = {
  "gate.visit": ["visitor_photo", "entry_photo", "exit_photo", "id_check"],
  "gate.gatematerial": ["invoice", "challan", "material", "vehicle", "load", "seal"],
  "gate.gatepass": ["out_photo", "return_photo"],
  "gate.courier": ["parcel", "collection_proof"],
  "tasks.task": ["before_photo", "after_photo", "report", "reading"],
  "tasks.breakdown": ["defect_photo", "before_photo", "after_photo"],
  "quality.inspection": ["defect_photo", "measurement"],
  "quality.complaint": ["customer_photo", "response_document"],
  "masters.artwork": ["kld", "approved_file", "customer_approval"],
};

function Thumb({ att }: { att: Att }) {
  const [src, setSrc] = useState<string>();
  useEffect(() => {
    if (!att.mime_type.startsWith("image/")) return;
    let url: string | undefined;
    protectedBlobUrl(`/api/attachments/${att.id}/content`).then((u) => { url = u; setSrc(u); }).catch(() => undefined);
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [att]);
  return src ? <img src={src} alt={att.purpose} className="thumb" /> : <span className="thumb doc">PDF</span>;
}

export function Attachments({ resource, id, canUpload }: { resource: string; id: string; canUpload: boolean }) {
  const [items, setItems] = useState<Att[]>([]);
  const [error, setError] = useState("");
  const [purpose, setPurpose] = useState((PURPOSES[resource] ?? ["evidence"])[0]);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => get<Att[]>(`/api/r/${resource}/${id}/attachments`).then(setItems).catch((e) => setError(e.message)), [resource, id]);
  useEffect(() => { load(); }, [load]);

  async function upload(file: File | undefined, live: boolean) {
    if (!file) return;
    setBusy(true);
    setError("");
    const form = new FormData();
    form.append("file", file);
    form.append("purpose", purpose);
    form.append("capture_source", live ? "live" : "upload");
    try {
      await api("POST", `/api/r/${resource}/${id}/attachments`, undefined, { form, idempotencyKey: newIdempotencyKey() });
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
      if (cameraRef.current) cameraRef.current.value = "";
    }
  }

  return (
    <section className="card">
      <header className="card-head"><h4>Evidence</h4><span className="muted small">{items.length} file(s) · validated, metadata stripped, SHA-256</span></header>
      {canUpload && (
        <form className="evidence-bar" onSubmit={(e: FormEvent) => e.preventDefault()}>
          <select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
            {(PURPOSES[resource] ?? ["evidence"]).map((p) => <option key={p} value={p}>{p.replace(/_/g, " ")}</option>)}
          </select>
          <input ref={cameraRef} type="file" accept="image/*" capture="environment" hidden onChange={(e) => upload(e.target.files?.[0], true)} />
          <input ref={fileRef} type="file" accept="image/jpeg,image/png,application/pdf" hidden onChange={(e) => upload(e.target.files?.[0], false)} />
          <button type="button" className="btn" disabled={busy} onClick={() => cameraRef.current?.click()}><Icon name="camera" size={16} />Take photo</button>
          <button type="button" className="btn" disabled={busy} onClick={() => fileRef.current?.click()}><Icon name="upload" size={16} />Upload file</button>
        </form>
      )}
      <ErrorBox error={error} />
      <div className="evidence-grid">
        {items.map((a) => (
          <button key={a.id} className="evidence" onClick={() => openProtected(`/api/attachments/${a.id}/content`)} title={`sha256 ${a.sha256}`}>
            <Thumb att={a} />
            <span><b>{a.purpose.replace(/_/g, " ")}</b>{a.proof_round > 1 && ` · round ${a.proof_round}`}</span>
            <span className="muted tiny">{a.capture_source === "live" ? "Live capture" : "Uploaded"} · {a.uploaded_by} · {fmtDateTime(a.uploaded_at)}{a.frozen && " · frozen"}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

type Hist = { at: string; actor: string; action: string; reason: string; version: number | null; approval_ref: string; changes: Record<string, any> };

export function History({ resource, id, refreshKey }: { resource: string; id: string; refreshKey?: number }) {
  const [items, setItems] = useState<Hist[]>([]);
  useEffect(() => { get<Hist[]>(`/api/r/${resource}/${id}/history`).then(setItems).catch(() => setItems([])); }, [resource, id, refreshKey]);
  return (
    <section className="card">
      <header className="card-head"><h4>History</h4><span className="muted small">Tamper-evident audit trail</span></header>
      <ol className="timeline">
        {items.map((h, i) => (
          <li key={i}>
            <div><b>{h.action.replace(/_/g, " ")}</b> <span className="muted">by {h.actor} · {fmtDateTime(h.at)}{h.version ? ` · v${h.version}` : ""}</span></div>
            {h.reason && <div className="small">Reason: {h.reason}</div>}
            {Object.keys(h.changes ?? {}).length > 0 && (
              <details className="small"><summary>{Object.keys(h.changes).length} change(s)</summary>
                <table className="mini"><tbody>
                  {Object.entries(h.changes).map(([k, v]) => (
                    <tr key={k}><td>{k}</td><td>{Array.isArray(v) && v.length === 2 ? <><s>{fmt(v[0])}</s> → {fmt(v[1])}</> : fmt(v)}</td></tr>
                  ))}
                </tbody></table>
              </details>
            )}
          </li>
        ))}
        {!items.length && <li className="muted">No history yet.</li>}
      </ol>
    </section>
  );
}

const fmt = (v: any) => (v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
