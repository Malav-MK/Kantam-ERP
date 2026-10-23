import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, get, newIdempotencyKey } from "../api";
import { useAuth } from "../auth";
import Icon from "../components/Icon";
import { Empty, ErrorBox, Pill } from "../components/ui";
import { fmtDateTime } from "../format";

type Approval = {
  id: string; summary: string; kind: string; status: string; created_by: string; created_by_label: string; created_at: string;
  required_approvals: number; amount?: string; expires_at: string | null; decided_by_label?: string; resource?: string; reason?: string;
  changes?: Record<string, [any, any]>; approvals?: { user: string; name: string; at: string; note: string }[]; decision_note?: string;
  executed_at?: string | null; result?: any; policy_label?: string;
  _extra?: { proposed_changes: Record<string, [any, any]>; progress: string; you_can_decide: boolean; you_are_maker: boolean; target: { resource: string; id: string } | null };
};

const TABS = [
  { key: "inbox", label: "Waiting for me" },
  { key: "mine", label: "My requests" },
  { key: "history", label: "Decided" },
] as const;

const show = (v: any) => (v === null || v === undefined || v === "" ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));

export function ApprovalsInbox() {
  const { profile } = useAuth();
  const nav = useNavigate();
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("inbox");
  const [rows, setRows] = useState<Approval[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    const params: Record<string, string> =
      tab === "inbox" ? { status: "pending" } : tab === "mine" ? { created_by: profile!.id } : { status: "approved,rejected,withdrawn,expired,stale" };
    get<{ results: Approval[] }>("/api/r/core.approvalrequest", { ...params, page_size: 100 })
      .then((r) => setRows(tab === "inbox" ? r.results.filter((a) => a.created_by !== profile!.id) : r.results))
      .catch((e) => setError(e.message));
  }, [tab, profile]);
  useEffect(load, [load]);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1><Icon name="check" size={22} /> Approvals</h1>
          <p className="muted small">Requests that need a second person: sales order changes after lock, stock-count adjustments, extra material issues and quotation margin exceptions.</p>
        </div>
      </header>
      <div className="seg">{TABS.map((t) => <button key={t.key} className={tab === t.key ? "on" : ""} onClick={() => setTab(t.key)}>{t.label}</button>)}</div>
      <ErrorBox error={error} />
      {!rows.length ? <Empty>{tab === "inbox" ? "Nothing is waiting for your approval." : "No requests."}</Empty> : (
        <div className="table-wrap">
          <table className="data">
            <thead><tr><th>Request</th><th>Requested by</th><th>Submitted</th><th>Approvals</th><th>Status</th><th>Expires</th></tr></thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id} onClick={() => nav(`/approvals/${a.id}`)}>
                  <td><b>{a.summary}</b><div className="small muted">{a.kind.replace(/_/g, " ")}</div></td>
                  <td>{a.created_by_label}</td>
                  <td>{fmtDateTime(a.created_at)}</td>
                  <td>{a.required_approvals > 1 ? `needs ${a.required_approvals}` : "1 checker"}</td>
                  <td><Pill value={a.status} /></td>
                  <td className="small">{a.status === "pending" ? fmtDateTime(a.expires_at) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function ApprovalDetail() {
  const { id = "" } = useParams();
  const [a, setA] = useState<Approval | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    get<Approval>(`/api/r/core.approvalrequest/${id}`).then(setA).catch((e: ApiError) => setError(e.status === 404 ? "Request not found or not yours to see." : e.message));
  }, [id]);
  useEffect(load, [load]);

  const decide = (action: "approve" | "reject" | "withdraw") => async (ev: FormEvent) => {
    ev.preventDefault();
    setBusy(true);
    setError("");
    try {
      const body = action === "approve" ? { note } : { reason: note };
      await api("POST", `/api/r/core.approvalrequest/${id}/actions/${action}`, body, { idempotencyKey: newIdempotencyKey() });
      setNote("");
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!a) return <div className="page"><ErrorBox error={error} />{!error && <p className="muted">Loading…</p>}</div>;
  const x = a._extra!;
  const changes = Object.entries(x.proposed_changes ?? {});
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <div className="crumbs"><Link to="/approvals">Approvals</Link></div>
          <h1>{a.summary} <Pill value={a.status} /></h1>
          <p className="muted small">Raised by <b>{a.created_by_label}</b> on {fmtDateTime(a.created_at)} · {x.progress}{a.policy_label ? ` · policy: ${a.policy_label}` : ""}</p>
        </div>
        {x.target && <Link className="btn" to={`/r/${x.target.resource}/${x.target.id}`}><Icon name="audit" size={16} />Open record</Link>}
      </header>

      <section className="card">
        <header className="card-head"><h4>Reason given</h4></header>
        <p>{a.reason || <span className="muted">—</span>}</p>
        {a.amount && <p className="small">Amount evaluated by policy: <b>₹{Number(a.amount).toLocaleString("en-IN")}</b></p>}
      </section>

      <section className="card">
        <header className="card-head"><h4>Proposed change</h4><span className="small muted">Validated when submitted; re-checked when approved</span></header>
        {!changes.length ? <p className="muted small">This action has no field-level change preview (see the request details).</p> : (
          <table className="mini diff">
            <thead><tr><th>Field</th><th>Current</th><th>Proposed</th></tr></thead>
            <tbody>{changes.map(([k, v]) => (
              <tr key={k}><td>{k.replace(/_/g, " ")}</td><td className="old">{show(Array.isArray(v) ? v[0] : "")}</td><td className="new">{show(Array.isArray(v) ? v[1] : v)}</td></tr>
            ))}</tbody>
          </table>
        )}
      </section>

      <section className="card">
        <header className="card-head"><h4>Checkers</h4></header>
        {!a.approvals?.length ? <p className="muted small">No approvals yet.</p> : (
          <ol className="timeline">{a.approvals.map((c, i) => <li key={i}><b>{c.name}</b> approved · {fmtDateTime(c.at)}{c.note && <div className="small">{c.note}</div>}</li>)}</ol>
        )}
        {a.decision_note && a.status !== "pending" && <p className="small">Decision note: {a.decision_note}</p>}
        {a.status === "stale" && <div className="alert warn">The record changed after this request was raised, so it was not applied. The maker must review and resubmit.</div>}
      </section>

      <ErrorBox error={error} />
      {x.you_can_decide && (
        <form className="card stack" onSubmit={decide("approve")}>
          <label className="field"><span>Your note (required to reject)</span><textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></label>
          <div className="row gap">
            <button className="btn primary" disabled={busy}><Icon name="check" size={16} />Approve{a.required_approvals > 1 ? ` (${(a.approvals?.length ?? 0) + 1}/${a.required_approvals})` : ""}</button>
            <button type="button" className="btn danger" disabled={busy || note.trim().length < 5} onClick={decide("reject")}><Icon name="close" size={16} />Reject</button>
          </div>
        </form>
      )}
      {x.you_are_maker && a.status === "pending" && (
        <form className="card stack" onSubmit={decide("withdraw")}>
          <p className="small muted">You raised this request, so you cannot approve it. You can withdraw it.</p>
          <label className="field"><span>Reason for withdrawal</span><input value={note} onChange={(e) => setNote(e.target.value)} required minLength={5} /></label>
          <div><button className="btn" disabled={busy}>Withdraw request</button></div>
        </form>
      )}
    </div>
  );
}
