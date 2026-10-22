import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, get } from "../api";
import { useAuth } from "../auth";
import { Empty, ErrorBox, Modal, Pill, Stat } from "../components/ui";
import { fmtDate, fmtDateTime } from "../format";
import KantamLogo from "../components/KantamLogo";
import Icon from "../components/Icon";

function usePoll<T>(url: string, ms = 60_000, params?: Record<string, any>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const key = JSON.stringify(params ?? {});
  const load = useCallback(() => get<T>(url, params).then((d) => { setData(d); setError(""); }).catch((e) => setError(e.message)), [url, key]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    load();
    const t = window.setInterval(load, ms);
    return () => window.clearInterval(t);
  }, [load, ms]);
  return { data, error, reload: load };
}

const Fresh = ({ at, source }: { at?: string; source?: string }) =>
  at ? <span className="fresh">Updated {fmtDateTime(at)}{source ? ` · ${source}` : ""}</span> : null;

// ---------------------------------------------------------------- Owner overview
type Card = { key: string; label: string; value: any; tone?: string; basis: string; records?: { resource?: string; id?: string; label: string; detail?: string; customer?: string }[] };

export function OwnerDashboard() {
  const [period, setPeriod] = useState("today");
  const { data, error } = usePoll<{ period: string; generated_at: string; source: string; cards: Card[]; financial: Card[] | null; pending_approvals: number }>(
    "/api/dash/owner", 60_000, { period });
  const [open, setOpen] = useState<Card | null>(null);
  const toneMap: Record<string, string> = { danger: "danger", warn: "warn", ok: "ok" };
  return (
    <div className="page">
      <header className="page-head">
        <div><h1>Owner overview</h1><Fresh at={data?.generated_at} source={data?.source} /></div>
        <div className="seg">
          {[["shift", "Current shift"], ["today", "Today"], ["week", "Week"], ["month", "Month"]].map(([k, l]) => (
            <button key={k} className={period === k ? "on" : ""} onClick={() => setPeriod(k)}>{l}</button>
          ))}
        </div>
      </header>
      <ErrorBox error={error} />
      {data && (
        <>
          <p className="muted small">{data.period}. Every figure comes from posted records - click a card for the records and calculation basis.</p>
          <div className="stats">
            {data.cards.map((c) => <Stat key={c.key} label={c.label} value={c.value} tone={toneMap[c.tone ?? ""]} sub={c.records?.length ? `${c.records.length} record(s)` : undefined} onClick={() => setOpen(c)} />)}
          </div>
          {data.pending_approvals > 0 && <div className="alert warn"><Link to="/r/core.approvalrequest?status=pending">{data.pending_approvals} approval(s) waiting for you</Link></div>}
          {data.financial && (
            <>
              <h2 className="section-title">Protected financial view</h2>
              <div className="stats">{data.financial.map((c) => <Stat key={c.key} label={c.label} value={c.value} sub={c.basis} />)}</div>
            </>
          )}
          <MachineStrip />
        </>
      )}
      {open && (
        <Modal title={open.label} onClose={() => setOpen(null)} wide>
          <p className="basis"><b>Basis:</b> {open.basis}</p>
          {!open.records?.length ? <Empty>No records behind this figure.</Empty> : (
            <ul className="drill">
              {open.records.map((r, i) => (
                <li key={i}>
                  {r.resource && r.id ? <Link to={`/r/${r.resource}/${r.id}`} onClick={() => setOpen(null)}>{r.label}</Link> : <b>{r.label}</b>}
                  {r.customer && <span className="muted"> · {r.customer}</span>}
                  {r.detail && <div className="small muted">{r.detail}</div>}
                </li>
              ))}
            </ul>
          )}
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Machine board
type Machine = { machine: string; machine_id?: string; name?: string; status: string; reason?: string; pr?: string; pr_id?: string; product?: string; component?: string;
  planned_pcs: number; good: number; reject_pct: string; cycle_s?: string; expected_finish?: string; next_free?: string; customer?: string; flavour?: string };

function MachineCard({ m, compact }: { m: Machine; compact?: boolean }) {
  const pct = m.planned_pcs ? Math.min(100, Math.round((m.good / m.planned_pcs) * 100)) : 0;
  const nav = useNavigate();
  return (
    <div className={`machine ${m.status}`} onClick={() => m.pr_id && nav(`/r/planning.productionrequest/${m.pr_id}`)} role={m.pr_id ? "button" : undefined}>
      <div className="machine-head"><b>{m.machine}</b><Pill value={m.status} /></div>
      <div className="machine-prod">{m.product ? `${m.product} · ${m.component}` : <span className="muted">No job</span>}</div>
      {!compact && m.customer && <div className="small muted">{m.customer}{m.flavour ? ` · ${m.flavour}` : ""}</div>}
      <div className="bar"><span style={{ width: `${pct}%` }} /></div>
      <div className="machine-nums"><span>{m.good.toLocaleString("en-IN")} / {m.planned_pcs.toLocaleString("en-IN")}</span><span>Rej {m.reject_pct}%</span></div>
      {m.reason && <div className="small reason">{m.reason}</div>}
      {!compact && m.expected_finish && <div className="tiny muted">Segment end {fmtDateTime(m.expected_finish)}{m.next_free ? ` · next free ${fmtDateTime(m.next_free)}` : ""}</div>}
    </div>
  );
}

function MachineStrip() {
  const { data } = usePoll<{ shift: string; generated_at: string; machines: Machine[] }>("/api/dash/machines", 60_000);
  if (!data) return null;
  return (
    <>
      <h2 className="section-title">Machines · {data.shift}</h2>
      <div className="machines">{data.machines.map((m) => <MachineCard key={m.machine} m={m} compact />)}</div>
    </>
  );
}

export function MachineBoard() {
  const { data, error } = usePoll<{ shift: string; generated_at: string; machines: Machine[] }>("/api/dash/machines", 30_000);
  return (
    <div className="page">
      <header className="page-head"><div><h1>Machine board</h1><Fresh at={data?.generated_at} source={data?.shift} /></div></header>
      <p className="muted small">Running is never inferred from an old record: a released PR with no report for 2 hours shows “no report”.</p>
      <ErrorBox error={error} />
      <div className="machines">{data?.machines.map((m) => <MachineCard key={m.machine} m={m} />)}</div>
    </div>
  );
}

export function TvBoard() {
  const { data, error } = usePoll<{ shift: string; generated_at: string; machines: Machine[] }>("/api/dash/tv", 30_000);
  return (
    <div className="tv">
      <header><span className="row gap"><KantamLogo className="tv-logo" /><b>Production</b></span><span>{data?.shift}</span><span>{data && fmtDateTime(data.generated_at)}</span></header>
      <ErrorBox error={error} />
      <div className="machines tv-grid">{data?.machines.map((m) => <MachineCard key={m.machine} m={m} compact />)}</div>
    </div>
  );
}

// ---------------------------------------------------------------- Gate
export function GateBoard() {
  const { data, error } = usePoll<any>("/api/dash/gate", 30_000);
  const labels: Record<string, string> = {
    people_inside: "Staff / labour inside", visitors_inside: "Visitors inside", vehicles_inside: "Vehicles inside", awaiting_host: "Awaiting host",
    arrivals_pending_receipt: "Arrivals pending receipt", gate_outs_pending: "Gate-outs pending", parcels_awaiting_receiver: "Parcels awaiting receiver",
    overdue_visitors: "Overdue visitors", unreturned_badges: "Unreturned badges", returnable_overdue: "Returnable passes overdue",
  };
  const bad = new Set(["overdue_visitors", "unreturned_badges", "returnable_overdue"]);
  return (
    <div className="page">
      <header className="page-head"><div><h1>Gate & muster</h1><Fresh at={data?.generated_at} /></div><button className="btn" onClick={() => window.print()}><Icon name="printer" size={16} />Print muster list</button></header>
      <ErrorBox error={error} />
      {data && (
        <>
          <div className="alert warn small">{data.freshness_note}</div>
          <div className="stats">{Object.entries(data.counts).map(([k, v]) => <Stat key={k} label={labels[k] ?? k} value={v as number} tone={bad.has(k) && (v as number) > 0 ? "danger" : undefined} />)}</div>
          <div className="cols">
            <section className="card"><header className="card-head"><h4>People last recorded inside</h4></header>
              <table className="mini"><thead><tr><th>Name</th><th>Type</th><th>Employer</th><th>Since</th></tr></thead>
                <tbody>{data.muster.people.map((p: any) => <tr key={p.code}><td>{p.person}</td><td>{p.kind}</td><td>{p.employer}</td><td>{fmtDateTime(p.since)}</td></tr>)}</tbody></table>
            </section>
            <section className="card"><header className="card-head"><h4>Visitors inside</h4></header>
              <table className="mini"><thead><tr><th>Visitor</th><th>Host</th><th>Badge</th><th>Since</th></tr></thead>
                <tbody>{data.muster.visitors.map((v: any, i: number) => <tr key={i}><td>{v.visitor} <span className="muted">{v.organisation}</span></td><td>{v.host}</td><td>{v.badge}</td><td>{fmtDateTime(v.since)}</td></tr>)}</tbody></table>
            </section>
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Stores daily
export function StoresDaily() {
  const { profile } = useAuth();
  const [date, setDate] = useState(profile?.current_shift.date ?? "");
  const [shift, setShift] = useState(profile?.current_shift.shift ?? "day");
  const { data, error } = usePoll<{ shift: string; rows: any[] }>("/api/dash/stores", 60_000, { date, shift });
  return (
    <div className="page">
      <header className="page-head">
        <div><h1>Stores - shift issue screen</h1><p className="muted small">PR / machine summary. Issue only released shift demand; one ledger line per lot movement behind each row.</p></div>
        <div className="row gap">
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          <select value={shift} onChange={(e) => setShift(e.target.value)}><option value="day">Day 09-21</option><option value="night">Night 21-09</option></select>
          <Link className="btn primary" to="/r/inventory.stockmove">Issue / return</Link>
        </div>
      </header>
      <ErrorBox error={error} />
      {data && !data.rows.length && <Empty>No released PRs for {data.shift}.</Empty>}
      {data?.rows.map((r) => (
        <section key={r.pr_id} className="card">
          <header className="card-head">
            <h4><Link to={`/r/planning.productionrequest/${r.pr_id}`}>{r.pr}</Link> · {r.machine} · {r.product} {r.component} {r.flavour && `· ${r.flavour}`}</h4>
            <span className="row gap"><Pill value={r.status} />{r.pending_action.map((a: string) => <span key={a} className="pill warn with-icon"><Icon name="flag" size={12} />{a}</span>)}</span>
          </header>
          <div className="small muted">{r.customer}{r.artwork && ` · artwork ${r.artwork}`}{r.handover && ` · handover: ${r.handover}`}</div>
          <table className="mini">
            <thead><tr><th>Material</th><th>Type</th><th className="num">Required</th><th className="num">Issued</th><th className="num">Returned</th><th className="num">Pending</th><th className="num">Machine side</th><th>Unit</th></tr></thead>
            <tbody>{r.materials.map((m: any) => (
              <tr key={m.item} className={Number(m.pending) > 0 ? "attention" : ""}>
                <td>{m.item}</td><td>{m.kind}</td><td className="num">{m.required}</td><td className="num">{m.issued}</td><td className="num">{m.returned}</td>
                <td className="num">{m.pending}</td><td className="num">{m.machine_side}</td><td>{m.uom}</td>
              </tr>))}</tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- Planning board
export function PlanningBoard() {
  const { data, error, reload } = usePoll<any>("/api/dash/planning", 120_000);
  const [pick, setPick] = useState<{ machine: any } | null>(null);
  return (
    <div className="page">
      <header className="page-head">
        <div><h1>Weekly / shift planning</h1><p className="muted small">Day → Date → Machine → Shift → Select plan. Select an existing eligible plan instead of retyping it.</p></div>
        <Link className="btn" to="/r/planning.monthlyplan">Monthly plans</Link>
      </header>
      <ErrorBox error={error} />
      {data?.machines.map((m: any) => (
        <section key={m.machine} className="card">
          <header className="card-head">
            <h4>{m.machine} <Pill value={m.status} /></h4>
            <span className="row gap"><span className="small muted">Next free {fmtDateTime(m.next_free)}</span><button className="btn small primary" onClick={() => setPick({ machine: m })}>Schedule shift job</button></span>
          </header>
          <div className="segments">
            {m.segments.length === 0 && <span className="muted small">No PRs in range.</span>}
            {m.segments.map((s: any) => (
              <Link key={s.id} to={`/r/planning.productionrequest/${s.id}`} className={`segment ${s.status}`}>
                <b>{fmtDate(s.date)} {s.shift}</b> {s.product} {s.component} · {s.planned_pcs.toLocaleString("en-IN")} pcs <Pill value={s.status} />
              </Link>
            ))}
          </div>
        </section>
      ))}
      {pick && <ScheduleDialog machine={pick.machine} onClose={() => setPick(null)} onDone={() => { setPick(null); reload(); }} />}
    </div>
  );
}

function ScheduleDialog({ machine, onClose, onDone }: { machine: any; onClose: () => void; onDone: () => void }) {
  const { profile } = useAuth();
  const [date, setDate] = useState(profile?.current_shift.date ?? "");
  const [shift, setShift] = useState(profile?.current_shift.shift ?? "day");
  const [plan, setPlan] = useState("");
  const [pcs, setPcs] = useState("");
  const [error, setError] = useState("");
  const day = date ? new Date(date + "T00:00:00").toLocaleDateString("en-IN", { weekday: "long" }) : "";
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api("POST", `/api/r/planning.planline/${plan}/actions/generate_pr`, { shift_date: date, shift, planned_pcs: pcs || undefined }, { idempotencyKey: crypto.randomUUID() });
      onDone();
    } catch (err: any) { setError(err.message); }
  }
  return (
    <Modal title={`Schedule on ${machine.machine}`} onClose={onClose}>
      <form className="form-grid" onSubmit={submit}>
        <label className="field"><span>Day</span><input value={day} disabled /></label>
        <label className="field"><span>Date</span><input type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></label>
        <label className="field"><span>Machine No.</span><input value={machine.machine} disabled /></label>
        <label className="field"><span>Shift</span><select value={shift} onChange={(e) => setShift(e.target.value)}><option value="day">Day 09:00-21:00</option><option value="night">Night 21:00-09:00</option></select></label>
        <label className="field span2"><span>Select plan</span>
          <select value={plan} onChange={(e) => setPlan(e.target.value)} required>
            <option value="">— eligible plans on this machine —</option>
            {machine.eligible_plans.map((p: any) => <option key={p.id} value={p.id}>{p.label} · {p.planned_qty} good · {p.cavities} cav @ {p.cycle_s}s{p.at_risk ? " · AT RISK" : ""}</option>)}
          </select></label>
        <label className="field"><span>Planned moulded pcs (optional)</span><input type="number" value={pcs} onChange={(e) => setPcs(e.target.value)} /></label>
        <ErrorBox error={error} />
        <div className="form-actions span2"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary">Create draft PR</button></div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- Shortages
export function Shortages() {
  const { data, error } = usePoll<any[]>("/api/dash/shortages", 120_000);
  return (
    <div className="page">
      <header className="page-head"><div><h1>Dated material shortages</h1>
        <p className="muted small">Unreserved demand + safety stock − free stock − PO arrivals promised by the need date. Arrivals after the need date do not cover an earlier shortage.</p></div>
        <Link className="btn primary" to="/r/purchase.requisition">Requisitions</Link></header>
      <ErrorBox error={error} />
      {data && !data.length && <Empty>No projected shortages.</Empty>}
      {data && data.length > 0 && (
        <div className="table-wrap"><table className="data">
          <thead><tr><th>Item</th><th>Need date</th><th>First short on</th><th className="num">Free</th><th className="num">Reserved</th><th className="num">Incoming</th><th className="num">Shortage</th><th className="num">Suggested buy</th><th>Late arrivals</th></tr></thead>
          <tbody>{data.map((r) => (
            <tr key={r.item}><td><b>{r.item_code}</b> <span className="muted">{r.item_name}</span></td><td>{fmtDate(r.need_date)}</td><td>{r.first_short_source}</td>
              <td className="num">{r.free}</td><td className="num">{r.reserved}</td><td className="num">{r.incoming}</td><td className="num danger-text"><b>{r.shortage}</b> {r.uom}</td>
              <td className="num">{r.suggested_order}</td><td className="small">{r.arrivals_too_late.map((a: any) => `${a.po} (${a.promised})`).join(", ")}</td></tr>))}</tbody>
        </table></div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Trace
function TraceNode({ n }: { n: any }) {
  if (!n || !n.lot) return null;
  return (
    <li>
      <Link to={`/r/inventory.lot/${n.id}`}><b>{n.lot}</b></Link> {n.item} <Pill value={n.state} /> {n.artwork && <span className="muted small">{n.artwork}</span>} {n.qty && <span className="small">qty {n.qty}</span>}
      {n.production && <div className="small">Made on {n.production.machine} / {n.production.mould} · {n.production.shift_date} {n.production.shift} · PR {n.production.pr} · operators {n.production.operators.join(", ") || "—"}</div>}
      {n.materials?.length > 0 && <div className="small muted">Materials: {n.materials.map((m: any) => `${m.lot} (${m.item})`).join(", ")}</div>}
      {n.dispatches?.length > 0 && <div className="small">Dispatched: {n.dispatches.map((d: any) => `${d.dispatch} → ${d.customer} (${d.qty})`).join(", ")}</div>}
      {["made_from", "used_in", "production_output"].map((k) => n[k]?.length ? <ul key={k} className="tree">{n[k].map((c: any, i: number) => <TraceNode key={i} n={c} />)}</ul> : null)}
    </li>
  );
}

export function Trace() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<any>(null);
  const [error, setError] = useState("");
  return (
    <div className="page">
      <header className="page-head"><div><h1>Traceability</h1><p className="muted small">Lot / batch number or supplier lot → backward to machine, shift, operators, polymer and label lots; forward to packed sets and customer dispatches.</p></div></header>
      <form className="search wide" onSubmit={(e) => { e.preventDefault(); setError(""); get("/api/dash/trace", { q }).then(setRes).catch((er) => setError(er.message)); }}>
        <input placeholder="e.g. LOT-2610-00012" value={q} onChange={(e) => setQ(e.target.value)} /><button className="btn primary"><Icon name="search" size={16} />Trace</button>
      </form>
      <ErrorBox error={error} />
      {res && !res.found && <Empty>No lot found (or it is outside your access).</Empty>}
      {res?.found && (
        <div className="cols">
          <section className="card"><header className="card-head"><h4>Backward (where it came from)</h4></header><ul className="tree"><TraceNode n={res.trace.backward} /></ul></section>
          <section className="card"><header className="card-head"><h4>Forward (where it went)</h4></header><ul className="tree"><TraceNode n={res.trace.forward} /></ul>
            <p className="small"><b>{res.affected.lots.length}</b> affected lot(s), <b>{res.affected.dispatches.length}</b> dispatch(es).</p></section>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Audit
export function AuditLog() {
  const { can } = useAuth();
  const [filters, setFilters] = useState({ search: "", resource: "", action: "" });
  const [applied, setApplied] = useState(filters);
  const [page, setPage] = useState(1);
  const { data, error } = usePoll<{ count: number; results: any[] }>("/api/audit", 300_000, { ...applied, page });
  const [verify, setVerify] = useState<any>(null);
  return (
    <div className="page">
      <header className="page-head"><div><h1>Audit log</h1><p className="muted small">Hash-chained: editing or deleting any past entry breaks the chain.</p></div>
        {can("audit.verify") && <button className="btn" onClick={() => api("POST", "/api/audit/verify").then(setVerify)}>Verify integrity</button>}</header>
      {verify && <div className={`alert ${verify.ok ? "ok" : "danger"}`}>{verify.ok ? `Chain intact - ${verify.checked} entries verified.` : `Chain BROKEN at entry ${verify.broken_at}.`}</div>}
      <form className="filters" onSubmit={(e) => { e.preventDefault(); setPage(1); setApplied(filters); }}>
        <input placeholder="Doc no / user / record id" value={filters.search} onChange={(e) => setFilters({ ...filters, search: e.target.value })} />
        <input placeholder="Resource (e.g. sales.salesorder)" value={filters.resource} onChange={(e) => setFilters({ ...filters, resource: e.target.value })} />
        <input placeholder="Action" value={filters.action} onChange={(e) => setFilters({ ...filters, action: e.target.value })} />
        <button className="btn">Filter</button>
      </form>
      <ErrorBox error={error} />
      <div className="table-wrap"><table className="data">
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Changes</th><th>Reason</th><th>IP</th></tr></thead>
        <tbody>{data?.results.map((e) => (
          <tr key={e.id}><td>{fmtDateTime(e.at)}</td><td>{e.actor}</td><td>{e.action}</td><td>{e.resource} {e.doc_no}</td>
            <td className="small mono clip">{JSON.stringify(e.changes)}</td><td className="small">{e.reason}</td><td className="small">{e.ip}</td></tr>))}</tbody>
      </table></div>
      {data && data.count > 50 && <div className="pager"><button className="btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><span className="muted">Page {page}</span><button className="btn" disabled={page * 50 >= data.count} onClick={() => setPage(page + 1)}>Next</button></div>}
    </div>
  );
}

// ---------------------------------------------------------------- My work
type WorkTab = "today" | "overdue" | "review" | "blocked" | "verified";
const WORK_TABS: { key: WorkTab; label: string }[] = [
  { key: "today", label: "Today" }, { key: "overdue", label: "Overdue" }, { key: "review", label: "Waiting review" },
  { key: "blocked", label: "Blocked" }, { key: "verified", label: "Verified" },
];
type WorkRow = { id: string; ref: string; title: string; source?: string; to: string; due?: string; originalDue?: string; state: string; kind: string };

/** Design book UI-02: one prioritised personal inbox. Seen is not done; Submitted waits for verification. */
export function MyWork() {
  const { profile, can } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = (WORK_TABS.some((t) => t.key === params.get("tab")) ? params.get("tab") : "today") as WorkTab;
  const open = usePoll<{ results: any[] }>("/api/r/tasks.task", 60_000, { state: "assigned,accepted,in_progress,blocked,reopened,submitted", page_size: 100 });
  const verified = usePoll<{ results: any[] }>("/api/r/tasks.task", 300_000, { state: "verified", assignee: profile?.id ?? "", page_size: 25 });
  const approvals = usePoll<{ results: any[] }>("/api/r/core.approvalrequest", 60_000, { status: "pending" });
  const visits = usePoll<{ results: any[] }>("/api/r/gate.myvisit", 60_000, { status: "awaiting_approval" });
  const parcels = usePoll<{ results: any[] }>("/api/r/gate.mycourier", 60_000, { status: "received" });
  const analytics = usePoll<any>("/api/dash/tasks", 120_000);
  const nav = useNavigate();

  const now = Date.now();
  const endOfDay = new Date();
  endOfDay.setHours(23, 59, 59, 999);
  const asRow = (t: any): WorkRow => ({ id: t.id, ref: t.doc_no, title: t.title, source: t.source_label, to: `/r/tasks.task/${t.id}`,
    due: t.due_at, originalDue: t.original_due_at, state: t.state, kind: "Task" });
  const tasks = open.data?.results ?? [];
  const mineOpen = tasks.filter((t) => t.assignee === profile?.id && t.state !== "submitted");
  const rows: Record<WorkTab, WorkRow[]> = {
    today: mineOpen.filter((t) => t.state !== "blocked" && new Date(t.due_at).getTime() <= endOfDay.getTime() && new Date(t.due_at).getTime() >= now).map(asRow),
    overdue: mineOpen.filter((t) => new Date(t.due_at).getTime() < now).map(asRow),
    review: [
      ...tasks.filter((t) => t.state === "submitted" && t.reviewer === profile?.id).map((t) => ({ ...asRow(t), title: `Verify: ${t.title}` })),
      ...(approvals.data?.results ?? []).filter((a) => a.created_by !== profile?.id).map((a) => ({ id: a.id, ref: "Approval", title: a.summary,
        to: `/approvals/${a.id}`, due: a.expires_at, state: "pending", kind: "Approval" })),
      ...(visits.data?.results ?? []).map((v) => ({ id: v.id, ref: v.doc_no ?? "Visit", title: `Visitor at gate: ${v.visitor_name} (${v.organisation})`,
        to: `/r/gate.myvisit/${v.id}`, state: v.status, kind: "Visitor" })),
      ...(parcels.data?.results ?? []).map((p) => ({ id: p.id, ref: p.awb, title: `Parcel to collect: ${p.courier_company}`,
        to: `/r/gate.mycourier/${p.id}`, state: p.status, kind: "Parcel" })),
    ],
    blocked: tasks.filter((t) => t.state === "blocked" && (t.assignee === profile?.id || t.reviewer === profile?.id)).map(asRow),
    verified: (verified.data?.results ?? []).map(asRow),
  };
  const list = rows[tab];
  const pick = (k: WorkTab) => setParams((p) => { p.set("tab", k); return p; }, { replace: true });

  return (
    <div className="page">
      <header className="page-head"><div><h1>My work today</h1>
        <p className="muted small">{profile?.full_name} · {profile?.job_title} · {new Date().toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })} / {profile?.current_shift.label} shift</p></div>
        <div className="row gap">
          <button className="btn primary" disabled={!list.length} onClick={() => list[0] && nav(list[0].to)}>Open next action</button>
          {can("tasks.task.create") && <Link className="btn" to="/r/tasks.task">New follow-up</Link>}
        </div>
      </header>
      {analytics.data && (
        <div className="stats">
          <Stat label="Due today" value={rows.today.length} />
          <Stat label="Overdue" value={analytics.data.overdue} tone={analytics.data.overdue ? "danger" : "ok"} />
          <Stat label="Waiting review" value={rows.review.length} tone={rows.review.length ? "warn" : undefined} />
          <Stat label="Verified on time" value={analytics.data.verified_on_time_pct === null ? "—" : `${analytics.data.verified_on_time_pct}%`} sub="against original due date" />
        </div>
      )}
      <div className="tabs" role="tablist">
        {WORK_TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? "on" : ""} onClick={() => pick(t.key)}>
            {t.label} <span className="tab-count">{rows[t.key].length}</span>
          </button>
        ))}
      </div>
      <section>
        {!list.length ? <p className="muted small pad">{tab === "verified" ? "Nothing verified recently." : "Nothing here. Seen alerts are not counted as done."}</p> : (
          <div className="table-wrap"><table className="data">
            <thead><tr><th>Reference</th><th>Action / source</th><th>Due</th><th>Status</th></tr></thead>
            <tbody>{list.map((r) => {
              const late = r.originalDue && r.due && r.originalDue !== r.due;
              return (
                <tr key={`${r.kind}-${r.id}`} onClick={() => nav(r.to)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && nav(r.to)}>
                  <td><b>{r.ref}</b><div className="tiny muted">{r.kind}</div></td>
                  <td>{r.title}{r.source && <div className="tiny muted">{r.source}</div>}</td>
                  <td>{r.due ? fmtDateTime(r.due) : "—"}{late && <div className="tiny muted">Original {fmtDateTime(r.originalDue!)}</div>}</td>
                  <td><Pill value={r.state} /></td>
                </tr>
              );
            })}</tbody>
          </table></div>
        )}
      </section>
    </div>
  );
}
