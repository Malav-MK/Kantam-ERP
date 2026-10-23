import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { get } from "../api";
import { useAuth } from "../auth";
import { resourceMeta } from "../components/Field";
import { ActionDialog } from "../components/Forms";
import Icon from "../components/Icon";
import { Empty, ErrorBox, Stat } from "../components/ui";
import { fmtDateTime } from "../format";
import type { ResourceMeta } from "../types";

type Row = {
  id: string; code: string; name: string; kind: string; kind_label: string; group: string; uom: string; min_stock: string; safety_stock: string;
  on_hand: string; usable: string; pending_qc: string; held: string; reserved: string; available: string; machine_side: string; incoming: string;
  locations: number; status: "ok" | "low" | "out" | "none"; value?: string;
};
type Overview = { generated_at: string; totals: { items: number; in_stock: number; low: number; out: number; value: string | null }; rows: Row[] };

const STATUS: Record<Row["status"], { label: string; tone: string }> = {
  ok: { label: "In stock", tone: "ok" }, low: { label: "Below minimum", tone: "warn" }, out: { label: "Out of stock", tone: "danger" }, none: { label: "No stock", tone: "muted" },
};
const qty = (v: string) => {
  const n = Number(v);
  return n ? n.toLocaleString("en-IN", { maximumFractionDigits: 3 }) : "—";
};
const rupees = (v: string) => `₹${Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

// ---------------------------------------------------------------- Stock inventory
export function StockInventory() {
  const { can } = useAuth();
  const nav = useNavigate();
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const [show, setShow] = useState<"all" | "stocked" | "attention">("stocked");
  const [meta, setMeta] = useState<ResourceMeta | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => get<Overview>("/api/dash/inventory").then((d) => { setData(d); setError(""); }).catch((e) => setError(e.message)), []);
  useEffect(() => {
    load();
    const t = window.setInterval(load, 120_000);
    return () => window.clearInterval(t);
  }, [load]);
  useEffect(() => { resourceMeta("inventory.stockbalance").then(setMeta).catch(() => undefined); }, []);
  const openingAction = meta?.actions.find((a) => a.name === "opening_stock");
  const canAdd = !!openingAction && can("inventory.stockbalance.opening_stock");

  const kinds = useMemo(() => [...new Map((data?.rows ?? []).map((r) => [r.kind, r.kind_label])).entries()], [data]);
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (data?.rows ?? []).filter((r) =>
      (!kind || r.kind === kind)
      && (show === "all" || (show === "stocked" ? r.status !== "none" : r.status === "low" || r.status === "out"))
      && (!q || `${r.code} ${r.name} ${r.group}`.toLowerCase().includes(q)));
  }, [data, search, kind, show]);
  const showValue = data?.totals.value != null;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1><Icon name="boxes" size={22} /> Stock inventory</h1>
          <p className="muted small">
            Item-wise stock in the stores, from the stock ledger. Available = usable (QC released) − reserved.
            {data && <> · Updated {fmtDateTime(data.generated_at)}</>}
          </p>
        </div>
        <div className="row gap">
          <Link className="btn" to="/r/inventory.stockbalance">By lot &amp; location</Link>
          <Link className="btn" to="/r/inventory.stockmove">Stock ledger</Link>
          {canAdd && <button className="btn primary" onClick={() => setAdding(true)}><Icon name="plus" size={16} />Add opening stock</button>}
        </div>
      </header>
      <ErrorBox error={error} />

      {data && (
        <div className="stats">
          <Stat label="Active items" value={data.totals.items} onClick={() => setShow("all")} />
          <Stat label="Items in stock" value={data.totals.in_stock} tone="ok" onClick={() => setShow("stocked")} />
          <Stat label="Below minimum" value={data.totals.low} tone={data.totals.low ? "warn" : undefined} onClick={() => setShow("attention")} />
          <Stat label="Out of stock" value={data.totals.out} tone={data.totals.out ? "danger" : undefined} onClick={() => setShow("attention")} />
          {showValue && <Stat label="Stock value" value={rupees(data.totals.value!)} sub="On hand × standard cost" />}
        </div>
      )}

      <div className="filters">
        <input placeholder="Search item code, name or group…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All item types</option>
          {kinds.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <div className="seg">
          {([["stocked", "With stock"], ["attention", "Needs attention"], ["all", "All items"]] as const).map(([k, l]) => (
            <button key={k} type="button" className={show === k ? "on" : ""} onClick={() => setShow(k)}>{l}</button>
          ))}
        </div>
      </div>

      {data && data.rows.length === 0 && <Empty>No active items yet. Add items under Masters → Items first.</Empty>}
      {data && data.rows.length > 0 && rows.length === 0 && (
        <Empty>
          {show === "stocked" && !search && !kind
            ? <>No stock recorded yet. {canAdd ? "Use “Add opening stock” to enter what is already in the stores, or receive goods against a purchase order." : "Stock appears here once goods are received."}</>
            : "No items match these filters."}
        </Empty>
      )}
      {rows.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Item</th><th>Type</th><th className="num">On hand</th><th className="num">Awaiting QC</th><th className="num">On hold</th>
                <th className="num">Reserved</th><th className="num">Available</th><th className="num">Min.</th><th className="num">Machine side</th>
                <th className="num">On order</th><th>Unit</th>{showValue && <th className="num">Value</th>}<th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} tabIndex={0} className={r.status === "low" || r.status === "out" ? "attention" : ""}
                  onClick={() => nav(`/r/inventory.stockbalance?item=${r.id}`)} onKeyDown={(e) => e.key === "Enter" && nav(`/r/inventory.stockbalance?item=${r.id}`)}>
                  <td><b>{r.code}</b><div className="small muted">{r.name}{r.group && ` · ${r.group}`}</div></td>
                  <td>{r.kind_label}</td>
                  <td className="num">{qty(r.on_hand)}</td>
                  <td className="num">{qty(r.pending_qc)}</td>
                  <td className="num">{qty(r.held)}</td>
                  <td className="num">{qty(r.reserved)}</td>
                  <td className="num"><b>{qty(r.available)}</b></td>
                  <td className="num">{qty(Number(r.safety_stock) > Number(r.min_stock) ? r.safety_stock : r.min_stock)}</td>
                  <td className="num">{qty(r.machine_side)}</td>
                  <td className="num">{qty(r.incoming)}</td>
                  <td>{r.uom}</td>
                  {showValue && <td className="num">{Number(r.value) ? rupees(r.value!) : "—"}</td>}
                  <td><span className={`pill ${STATUS[r.status].tone}`}>{STATUS[r.status].label}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > 0 && <p className="small muted">Click an item to see its stock by lot and location. Showing {rows.length} of {data?.rows.length} items.</p>}

      {adding && meta && openingAction && (
        <ActionDialog meta={meta} action={openingAction} onClose={() => setAdding(false)} onDone={() => { setAdding(false); load(); }} />
      )}
    </div>
  );
}
