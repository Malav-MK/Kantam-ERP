import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, get, openProtected } from "../api";
import { useAuth } from "../auth";
import { resourceMeta, FieldInput } from "../components/Field";
import { ActionDialog, DeleteDialog, RecordForm } from "../components/Forms";
import { Attachments, History } from "../components/Evidence";
import { Empty, ErrorBox, Help, Modal, Pill } from "../components/ui";
import Icon from "../components/Icon";
import { display, titleOf } from "../format";
import type { ActionMeta, ResourceMeta, Row } from "../types";

const DOCS: Record<string, { kind: string; label: string }[]> = {
  "sales.salesorder": [{ kind: "so", label: "SO document" }],
  "planning.productionrequest": [{ kind: "pr", label: "Shift PR / material slip" }],
  "purchase.purchaseorder": [{ kind: "po", label: "PO document" }],
  "purchase.grn": [{ kind: "grn", label: "GRN" }],
  "quality.inspection": [{ kind: "coa", label: "COA" }],
  "fg.dispatch": [{ kind: "picklist", label: "Pick / packing list" }],
};
const ATTACHABLE = new Set(["gate.visit", "gate.gatematerial", "gate.gatepass", "gate.courier", "tasks.task", "tasks.breakdown", "quality.inspection",
  "quality.complaint", "masters.artwork", "tasks.incident", "fg.dispatch", "purchase.grn", "gate.person", "masters.product"]);

function useMeta(key: string) {
  const [meta, setMeta] = useState<ResourceMeta | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setMeta(null);
    resourceMeta(key).then(setMeta).catch((e: ApiError) => setError(e.status === 403 ? "You do not have access to this workspace." : e.message));
  }, [key]);
  return { meta, error };
}

export function DataTable({ meta, rows, onOpen, onDelete }: { meta: ResourceMeta; rows: Row[]; onOpen: (r: Row) => void; onDelete?: (r: Row) => void }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr>{meta.list_fields.map((f) => <th key={f}>{meta.fields[f]?.label ?? f}</th>)}{onDelete && <th className="row-tools"><span className="sr-only">Delete</span></th>}</tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} onClick={() => onOpen(r)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen(r)}>
              {meta.list_fields.map((f) => (
                <td key={f} className={["integer", "decimal"].includes(meta.fields[f]?.type) ? "num" : ""}>
                  {f === meta.status_field ? <Pill value={r[f]} label={display(r, f, meta.fields[f])} /> : display(r, f, meta.fields[f])}
                </td>
              ))}
              {onDelete && (
                <td className="row-tools">
                  <button className="icon-btn danger" title="Delete" aria-label={`Delete ${titleOf(r, meta.title_field)}`}
                    onClick={(e) => { e.stopPropagation(); onDelete(r); }}><Icon name="trash" size={16} /></button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ResourceList() {
  const { key = "" } = useParams();
  const { meta, error } = useMeta(key);
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<{ count: number; results: Row[] } | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [creating, setCreating] = useState(false);
  const [action, setAction] = useState<ActionMeta | null>(null);
  const [deleting, setDeleting] = useState<Row | null>(null);
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState(params.get("search") ?? "");
  const page = Number(params.get("page") ?? 1);

  const query = useMemo(() => Object.fromEntries(params.entries()), [params]);
  const load = useCallback(() => {
    if (!meta) return;
    get<{ count: number; results: Row[] }>(`/api/r/${key}`, query).then(setData).catch((e) => setLoadErr(e.message));
  }, [key, meta, query]);
  useEffect(load, [load]);

  if (error) return <div className="page"><ErrorBox error={error} /></div>;
  if (!meta) return <div className="page muted">Loading…</div>;
  const setParam = (k: string, v?: string | null) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    if (k !== "page") next.delete("page");
    setParams(next);
  };
  const collectionActions = meta.actions.filter((a) => !a.detail);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{meta.label}</h1>
          <Help help={meta.help} />
        </div>
        <div className="row gap">
          {collectionActions.map((a) => <button key={a.name} className={`btn ${a.tone}`} onClick={() => setAction(a)}>{a.label}</button>)}
          {meta.can_export && <button className="btn" onClick={() => openProtected(`/api/r/${key}/export?${new URLSearchParams(query)}`)}><Icon name="download" size={16} />Export CSV</button>}
          {meta.can_create && <button className="btn primary" onClick={() => setCreating(true)}><Icon name="plus" size={16} />New {meta.singular.toLowerCase()}</button>}
        </div>
      </header>
      <div className="filters">
        {meta.searchable && (
          <form onSubmit={(e) => { e.preventDefault(); setParam("search", search); }} className="search">
            <input placeholder={`Search ${meta.label.toLowerCase()}…`} value={search} onChange={(e) => setSearch(e.target.value)} />
          </form>
        )}
        {meta.filters.filter((f) => f.type === "choice" || f.type === "boolean" || f.type === "date").map((f) => (
          <label key={f.name} className="filter">
            <span>{f.label}</span>
            {f.type === "choice" ? (
              <select value={params.get(f.name) ?? ""} onChange={(e) => setParam(f.name, e.target.value)}>
                <option value="">All</option>
                {f.choices?.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            ) : f.type === "boolean" ? (
              <select value={params.get(f.name) ?? ""} onChange={(e) => setParam(f.name, e.target.value)}>
                <option value="">All</option><option value="true">Yes</option><option value="false">No</option>
              </select>
            ) : <input type="date" value={params.get(f.name) ?? ""} onChange={(e) => setParam(f.name, e.target.value)} />}
          </label>
        ))}
        {meta.filters.filter((f) => (f.type === "fk" || f.type === "fk_multi") && f.target).slice(0, 3).map((f) => (
          <label key={f.name} className="filter wide">
            <span>{f.label}</span>
            <FieldInput meta={{ ...f, type: "fk", readonly: false }} value={params.get(f.name)} values={{}} onChange={(v) => setParam(f.name, v)} />
          </label>
        ))}
      </div>
      <ErrorBox error={loadErr} />
      {notice && <div className="alert ok" role="status">{notice}</div>}
      {data && data.results.length === 0 && (
        <Empty>
          {params.toString() ? "Nothing matches these filters." : "Nothing here yet."}
          {params.toString() ? <div><button className="btn small" onClick={() => { setSearch(""); setParams(new URLSearchParams()); }}>Clear filters</button></div>
            : meta.can_create && <div><button className="btn primary" onClick={() => setCreating(true)}><Icon name="plus" size={16} />Add the first {meta.singular.toLowerCase()}</button></div>}
        </Empty>
      )}
      {data && data.results.length > 0 && <p className="tiny muted">Click a row to open it.</p>}
      {data && data.results.length > 0 && <DataTable meta={meta} rows={data.results} onOpen={(r) => nav(`/r/${key}/${r.id}`)}
        onDelete={meta.can_delete ? setDeleting : undefined} />}
      {data && data.count > 50 && (
        <div className="pager">
          <button className="btn" disabled={page <= 1} onClick={() => setParam("page", String(page - 1))}>Previous</button>
          <span className="muted">Page {page} of {Math.ceil(data.count / 50)} · {data.count} records</span>
          <button className="btn" disabled={page * 50 >= data.count} onClick={() => setParam("page", String(page + 1))}>Next</button>
        </div>
      )}
      {creating && (
        <Modal title={`New ${meta.singular.toLowerCase()}`} onClose={() => setCreating(false)} wide>
          <RecordForm meta={meta} onCancel={() => setCreating(false)} onSaved={(r) => {
            setCreating(false);
            if (r._extra?.enrollment_code) window.alert(`One-time enrolment code (valid 24h): ${r._extra.enrollment_code}`);
            nav(`/r/${key}/${r.id}`);
          }} />
        </Modal>
      )}
      {action && <ActionDialog meta={meta} action={action} onClose={() => setAction(null)} onDone={(r) => {
        setAction(null);
        if (r?._resource && r.id) nav(`/r/${r._resource}/${r.id}`); else load();
      }} />}
      {deleting && <DeleteDialog meta={meta} record={deleting} onClose={() => setDeleting(null)} onDeleted={() => {
        setNotice(`${titleOf(deleting, meta.title_field)} deleted.`);
        setDeleting(null);
        load();
      }} />}
    </div>
  );
}

function InlineTable({ spec, parent, parentKey }: { spec: { resource: string; fk: string; label: string }; parent: Row; parentKey: string }) {
  const nav = useNavigate();
  const [meta, setMeta] = useState<ResourceMeta | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<Row | null>(null);
  const load = useCallback(() => {
    get<{ results: Row[] }>(`/api/r/${spec.resource}`, { [spec.fk]: parent.id, page_size: 200 }).then((r) => setRows(r.results)).catch(() => setRows([]));
  }, [spec, parent.id]);
  useEffect(() => { resourceMeta(spec.resource).then(setMeta).catch(() => undefined); load(); }, [spec.resource, load]);
  if (!meta) return null;
  return (
    <section className="card">
      <header className="card-head">
        <h4>{spec.label}</h4>
        {meta.can_create && <button className="btn small" onClick={() => setAdding(true)}><Icon name="plus" size={14} />Add</button>}
      </header>
      {rows.length ? <DataTable meta={{ ...meta, list_fields: meta.list_fields.filter((f) => f !== spec.fk) }} rows={rows} onOpen={(r) => nav(`/r/${spec.resource}/${r.id}`)}
        onDelete={meta.can_delete ? setDeleting : undefined} />
        : <p className="muted small">None yet.</p>}
      {deleting && <DeleteDialog meta={meta} record={deleting} onClose={() => setDeleting(null)} onDeleted={() => { setDeleting(null); load(); }} />}
      {adding && (
        <Modal title={`Add to ${spec.label.toLowerCase()}`} onClose={() => setAdding(false)} wide>
          <RecordForm meta={meta} defaults={{ [spec.fk]: parent.id }} parent={parent} onCancel={() => setAdding(false)} onSaved={() => { setAdding(false); load(); }} />
        </Modal>
      )}
      <span hidden>{parentKey}</span>
    </section>
  );
}

function ExtraPanel({ extra }: { extra: Record<string, any> }) {
  const entries = Object.entries(extra ?? {}).filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && !v.length));
  if (!entries.length) return null;
  return (
    <section className="card">
      <header className="card-head"><h4>More information</h4></header>
      <div className="extra">
        {entries.map(([k, v]) => (
          <div key={k} className="extra-item">
            <div className="extra-key">{k.replace(/_/g, " ")}</div>
            <Value v={v} />
          </div>
        ))}
      </div>
    </section>
  );
}

function Value({ v }: { v: any }): JSX.Element {
  if (v === null || v === undefined) return <span className="muted">—</span>;
  if (typeof v === "boolean") return <span>{v ? "Yes" : "No"}</span>;
  if (typeof v !== "object") return <span>{String(v)}</span>;
  if (Array.isArray(v)) {
    if (v.length && typeof v[0] === "object" && !Array.isArray(v[0])) {
      const cols = Array.from(new Set(v.flatMap((r) => Object.keys(r)))).filter((c) => typeof v[0][c] !== "object" || v[0][c] === null);
      return (
        <div className="table-wrap"><table className="mini">
          <thead><tr>{cols.map((c) => <th key={c}>{c.replace(/_/g, " ")}</th>)}</tr></thead>
          <tbody>{v.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{typeof r[c] === "object" ? JSON.stringify(r[c]) : String(r[c] ?? "")}</td>)}</tr>)}</tbody>
        </table></div>
      );
    }
    return <span>{v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ")}</span>;
  }
  return (
    <dl className="kv">
      {Object.entries(v).map(([k, x]) => (
        <div key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{typeof x === "object" && x !== null ? <Value v={x} /> : String(x ?? "—")}</dd></div>
      ))}
    </dl>
  );
}

export function ResourceDetail() {
  const { key = "", id = "" } = useParams();
  const { meta, error } = useMeta(key);
  const { can } = useAuth();
  const nav = useNavigate();
  const [row, setRow] = useState<Row | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [editing, setEditing] = useState(false);
  const [action, setAction] = useState<ActionMeta | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [rev, setRev] = useState(0);

  const load = useCallback(() => {
    get<Row>(`/api/r/${key}/${id}`).then((r) => { setRow(r); setLoadErr(""); })
      .catch((e: ApiError) => setLoadErr(e.status === 404 ? "Record not found or outside your scope." : e.message));
  }, [key, id]);
  useEffect(load, [load]);

  if (error || loadErr) return <div className="page"><ErrorBox error={error || loadErr} /></div>;
  if (!meta || !row) return <div className="page muted">Loading…</div>;
  const fields = Array.from(new Set([...meta.list_fields, ...meta.create_only_fields, ...meta.form_fields, ...Object.keys(meta.fields)]));
  const available = meta.actions.filter((a) => a.detail && row._meta?.actions.includes(a.name));
  const canUpload = ATTACHABLE.has(key) && (meta.can_update || can(`${key}.attach`));

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <div className="crumbs"><Link to={`/r/${key}`}>← Back to {meta.label}</Link></div>
          <h1>{titleOf(row, meta.title_field)} {meta.status_field && <Pill value={row[meta.status_field]} label={display(row, meta.status_field, meta.fields[meta.status_field])} />}</h1>
          {row._meta?.locked_reason && <div className="lock"><Icon name="lock" size={15} /> {row._meta.locked_reason}</div>}
          {!!row._meta?.pending_approvals?.length && (
            <div className="lock"><Icon name="check" size={15} /> A change to this record is awaiting checker approval -{" "}
              {row._meta.pending_approvals.map((pid) => <Link key={pid} to={`/approvals/${pid}`}>view request</Link>)}</div>
          )}
        </div>
        <div className="row gap wrap">
          {(DOCS[key] ?? []).map((d) => <button key={d.kind} className="btn" onClick={() => openProtected(`/api/docs/${d.kind}/${id}`)}><Icon name="printer" size={16} />{d.label}</button>)}
          {row._meta?.editable && meta.form_fields.length > 0 && <button className="btn" onClick={() => setEditing(true)}><Icon name="edit" size={16} />Edit</button>}
          {available.map((a) => <button key={a.name} className={`btn ${a.tone}`} onClick={() => setAction(a)}>{a.label}</button>)}
          {row._meta?.deletable && <button className="btn danger" onClick={() => setDeleting(true)}><Icon name="trash" size={16} />Delete</button>}
        </div>
      </header>
      <Help help={meta.help} />
      <section className="card">
        <dl className="detail-grid">
          {fields.filter((f) => row[f] !== undefined || row[`${f}_label`] !== undefined).map((f) => (
            <div key={f} className={meta.fields[f]?.type === "json" || meta.fields[f]?.type === "textarea" ? "span2" : ""}>
              <dt>{meta.fields[f]?.label ?? f.replace(/_/g, " ")}</dt>
              <dd>{meta.fields[f]?.type === "json" ? <Value v={row[f]} /> : display(row, f, meta.fields[f]) || <span className="muted">—</span>}</dd>
            </div>
          ))}
        </dl>
      </section>
      {row._extra && <ExtraPanel extra={row._extra} />}
      {meta.inlines.map((s) => <InlineTable key={s.resource + s.fk} spec={s} parent={row} parentKey={key} />)}
      {ATTACHABLE.has(key) && <Attachments resource={key} id={id} canUpload={canUpload} />}
      <History resource={key} id={id} refreshKey={rev} />
      {editing && (
        <Modal title={`Edit ${meta.singular.toLowerCase()}`} onClose={() => setEditing(false)} wide>
          <RecordForm meta={meta} record={row} onCancel={() => setEditing(false)} onSaved={(r) => { setEditing(false); setRow({ ...r, _extra: row._extra }); setRev((x) => x + 1); load(); }} />
        </Modal>
      )}
      {action && <ActionDialog meta={meta} action={action} record={row} onClose={() => setAction(null)} onDone={(r) => {
        setAction(null);
        setRev((x) => x + 1);
        if (r?._resource && r.id && (r._resource !== key || r.id !== id)) nav(`/r/${r._resource}/${r.id}`); else load();
      }} />}
      {deleting && <DeleteDialog meta={meta} record={row} onClose={() => setDeleting(false)} onDeleted={() => nav(`/r/${key}`, { replace: true })} />}
    </div>
  );
}
