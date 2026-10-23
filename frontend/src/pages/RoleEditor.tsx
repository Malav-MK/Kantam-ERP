import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, get, newIdempotencyKey } from "../api";
import { useAuth } from "../auth";
import Icon from "../components/Icon";
import { ErrorBox } from "../components/ui";

type Perm = { code: string; description: string };
type Row = { key: string; label: string; cells: Record<string, Perm[]> };
type Matrix = {
  columns: { key: string; label: string }[];
  modules: { key: string; label: string; rows: Row[] }[];
  special: { key: string; label: string; permissions: Perm[] }[];
  help: Record<string, string>;
};
type Role = {
  id: string; name: string; code: string; level: string; level_label?: string; description: string; is_system: boolean; customized: boolean; row_version: number;
  _extra: { permissions: string[]; users: number; is_owner_role: boolean; baseline: string[]; can_grant: string[] | null };
};
type State = "all" | "some" | "none" | "na";

/** Tick box that can also show "partly ticked" (for a whole module or a shared permission). */
function TriCheck({ state, disabled, title, onChange }: { state: State; disabled?: boolean; title?: string; onChange: (on: boolean) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = state === "some"; }, [state]);
  if (state === "na") return <span className="muted" aria-hidden>—</span>;
  return <input ref={ref} type="checkbox" checked={state === "all"} disabled={disabled} title={title} aria-label={title}
    onChange={() => onChange(state !== "all")} />;
}

export function RoleEditor() {
  const { id } = useParams();
  const nav = useNavigate();
  const { profile } = useAuth();
  const isOwner = !!profile?.levels.includes("owner");
  const [role, setRole] = useState<Role | null>(null);
  const [matrix, setMatrix] = useState<Matrix | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [details, setDetails] = useState({ name: "", description: "" });

  const load = useCallback(() => {
    Promise.all([get<Role>(`/api/r/core.role/${id}`), get<Matrix>("/api/roles/permission-matrix")])
      .then(([r, m]) => {
        setRole(r);
        setMatrix(m);
        setSelected(new Set(r._extra.permissions));
        setDetails({ name: r.name, description: r.description ?? "" });
      })
      .catch((e) => setError(e.message));
  }, [id]);
  useEffect(load, [load]);

  const original = useMemo(() => new Set(role?._extra.permissions ?? []), [role]);
  const baseline = useMemo(() => new Set(role?._extra.baseline ?? []), [role]);
  const readOnly = !!role?._extra.is_owner_role;
  const grantable = (code: string) => !role?._extra.can_grant || role._extra.can_grant.includes(code) || original.has(code);
  const added = [...selected].filter((c) => !original.has(c));
  const removed = [...original].filter((c) => !selected.has(c));
  const dirty = added.length + removed.length > 0;

  const stateOf = (codes: string[]): State => {
    const uniq = [...new Set(codes)];
    if (!uniq.length) return "na";
    if (readOnly) return "all";
    const on = uniq.filter((c) => selected.has(c)).length;
    return on === 0 ? "none" : on === uniq.length ? "all" : "some";
  };
  const toggle = (codes: string[], on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const c of codes) {
        if (baseline.has(c)) continue;
        if (on && grantable(c)) next.add(c);
        if (!on) next.delete(c);
      }
      return next;
    });
  };
  const describe = (perms: Perm[]) => perms.map((p) => p.description).join("\n");
  const lockedFor = (codes: string[]) => codes.length > 0 && codes.every((c) => !grantable(c) || baseline.has(c));

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!role) return;
    setBusy(true); setError(""); setNotice("");
    try {
      if (details.name !== role.name || details.description !== (role.description ?? "")) {
        await api("PATCH", `/api/r/core.role/${role.id}`, { ...details, row_version: role.row_version, reason }, { idempotencyKey: newIdempotencyKey() });
      }
      if (dirty) {
        const r = await api("POST", `/api/r/core.role/${role.id}/actions/set_permissions`, { permissions: [...selected], reason },
          { idempotencyKey: newIdempotencyKey() });
        if (r?.pending_approval) { setNotice(`Sent for approval. ${r.message ?? ""}`); setBusy(false); return; }
      }
      setNotice("Saved. People with this role get the new access on their next action.");
      setReason("");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function resetDefault() {
    if (!role || !window.confirm("Replace this role's permissions with the original built-in set?")) return;
    if (reason.trim().length < 5) { setError("Enter a reason (at least 5 characters) before resetting."); return; }
    setBusy(true); setError("");
    try {
      const r = await api("POST", `/api/r/core.role/${role.id}/actions/reset_default`, { reason }, { idempotencyKey: newIdempotencyKey() });
      setNotice(r?.pending_approval ? `Sent for approval. ${r.message ?? ""}` : "Reset to the built-in default.");
      setReason("");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function deleteRole() {
    if (!role) return;
    if (reason.trim().length < 5) { setError("Enter a reason (at least 5 characters) before deleting."); return; }
    if (!window.confirm(`Delete the role "${role.name}"? People must not hold it any more. This cannot be undone from the screen.`)) return;
    setBusy(true); setError("");
    try {
      await api("POST", `/api/r/core.role/${role.id}/actions/delete_role`, { reason }, { idempotencyKey: newIdempotencyKey() });
      nav("/r/core.role");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setBusy(false);
    }
  }

  if (!role || !matrix) return <div className="page"><ErrorBox error={error} />{!error && <p className="muted">Loading…</p>}</div>;
  const cols = matrix.columns;

  return (
    <form className="page role-editor" onSubmit={save}>
      <header className="page-head">
        <div>
          <div className="small muted"><Link to="/r/core.role">Roles</Link> / {role.code}</div>
          <h1><Icon name="shield" size={22} /> {role.name}</h1>
          <p className="muted small">
            {role.is_system ? (role.customized ? "Built-in role, edited here" : "Built-in role") : "Custom role"} · {role._extra.users} user(s) · level: {role.level}
          </p>
        </div>
        {!readOnly && (
          <div className="row gap">
            {isOwner && <button type="button" className="btn danger" disabled={busy} onClick={deleteRole}
              title={role._extra.users ? "Revoke this role from its users first" : "Delete this role"}><Icon name="trash" size={16} />Delete role</button>}
            {role.is_system && role.customized && <button type="button" className="btn" disabled={busy} onClick={resetDefault}>Reset to built-in default</button>}
            <button className="btn primary" disabled={busy || reason.trim().length < 5 || (!dirty && details.name === role.name && details.description === (role.description ?? ""))}>
              Save changes
            </button>
          </div>
        )}
      </header>

      {readOnly ? (
        <div className="alert ok">The owner role always has full access to everything. It cannot be limited.</div>
      ) : (
        <div className="card stack">
          <div className="form-grid">
            <label className="field"><span>Role name</span><input value={details.name} onChange={(e) => setDetails({ ...details, name: e.target.value })} required /></label>
            <label className="field"><span>Description</span><input value={details.description} onChange={(e) => setDetails({ ...details, description: e.target.value })} /></label>
            <label className="field span2"><span>Reason for this change (recorded in history)<b className="req">*</b></span>
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Stores staff now also record physical counts" /></label>
            {isOwner && role._extra.users > 0 && (
              <p className="small muted span2">{role._extra.users} user(s) hold this role. Revoke it from them (Administration → Users → Revoke role) before deleting it.</p>
            )}
          </div>
          {dirty && <div className="small muted">{added.length} permission(s) to add, {removed.length} to remove.</div>}
        </div>
      )}
      <ErrorBox error={error} />
      {notice && <div className="alert ok">{notice}</div>}

      <section className="card">
        <header className="card-head"><h4>What this role can do</h4></header>
        <p className="small muted pad-x">
          Tick a module row to set every record type in it, or open the module to choose record by record. Hover a tick box to see exactly what it allows.
        </p>
        <div className="table-wrap">
          <table className="data perm-grid">
            <thead>
              <tr>
                <th>Module / record type</th>
                {cols.map((c) => <th key={c.key} title={matrix.help[c.key]} className="perm-col">{c.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {matrix.modules.map((m) => {
                const isOpen = open.has(m.key);
                return [
                  <tr key={m.key} className="perm-module" onClick={() => setOpen((s) => { const n = new Set(s); isOpen ? n.delete(m.key) : n.add(m.key); return n; })}>
                    <td><span className="nav-caret">{isOpen ? "−" : "+"}</span> <b>{m.label}</b> <span className="tiny muted">({m.rows.length})</span></td>
                    {cols.map((c) => {
                      const codes = m.rows.flatMap((r) => r.cells[c.key].map((p) => p.code));
                      return (
                        <td key={c.key} className="perm-col" onClick={(e) => e.stopPropagation()}>
                          <TriCheck state={stateOf(codes)} disabled={readOnly || lockedFor(codes)} title={`${c.label}: all of ${m.label}`} onChange={(on) => toggle(codes, on)} />
                        </td>
                      );
                    })}
                  </tr>,
                  ...(isOpen ? m.rows.map((r) => (
                    <tr key={r.key} className="perm-row">
                      <td className="perm-label">{r.label}</td>
                      {cols.map((c) => {
                        const perms = r.cells[c.key];
                        const codes = perms.map((p) => p.code);
                        return (
                          <td key={c.key} className="perm-col">
                            <TriCheck state={stateOf(codes)} disabled={readOnly || lockedFor(codes)} title={describe(perms) || c.label} onChange={(on) => toggle(codes, on)} />
                          </td>
                        );
                      })}
                    </tr>
                  )) : []),
                ];
              })}
            </tbody>
          </table>
        </div>
        <div className="perm-legend small muted">
          {cols.map((c) => <span key={c.key}><b>{c.label}</b>: {matrix.help[c.key]}</span>)}
          <span><b>—</b>: not applicable to that record type.</span>
        </div>
      </section>

      <section className="card">
        <header className="card-head"><h4>Special permissions</h4></header>
        <p className="small muted pad-x">Access that is not about one record type: seeing prices and costs, dashboards, audit history and security administration.</p>
        <div className="perm-special">
          {matrix.special.map((g) => (
            <fieldset key={g.key}>
              <legend>{g.label}</legend>
              {g.permissions.map((p) => (
                <label key={p.code} className="check">
                  <input type="checkbox" checked={readOnly || selected.has(p.code)} disabled={readOnly || baseline.has(p.code) || !grantable(p.code)}
                    onChange={(e) => toggle([p.code], e.target.checked)} />
                  <span>{p.description}</span>
                </label>
              ))}
            </fieldset>
          ))}
        </div>
      </section>
      {role._extra.can_grant && !readOnly && (
        <p className="small muted">Greyed-out boxes are permissions you do not hold yourself, so you cannot give them to others.</p>
      )}
    </form>
  );
}
