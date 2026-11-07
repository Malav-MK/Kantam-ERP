import type { ReactNode } from "react";
import { fmtDate, fmtDateTime, humanize } from "../format";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const DATETIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;
const DECIMAL = /^-?\d+\.\d+$/;
const number = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 });

const isEmpty = (v: any) => v === null || v === undefined || v === "" || (typeof v === "object" && !Object.keys(v).length);
const isNumeric = (v: any) => typeof v === "number" || (typeof v === "string" && DECIMAL.test(v));
// Internal record ids and the "ok" flag mean nothing to the reader.
const hiddenKey = (k: string, v: any) => k === "ok" || k === "id" || k.endsWith("_id") || (typeof v === "string" && UUID.test(v));

function scalar(v: any): string {
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number") return number.format(v);
  if (typeof v !== "string") return String(v);
  if (DATE.test(v)) return fmtDate(v);
  if (DATETIME.test(v)) return fmtDateTime(v);
  if (DECIMAL.test(v)) return number.format(Number(v));
  return v;
}

/** Any structured value as people read it: rows become a table, an object a two-column table, never raw JSON. */
export default function DataView({ value }: { value: any }): JSX.Element {
  if (isEmpty(value)) return <span className="muted">—</span>;
  if (typeof value !== "object") return <span>{scalar(value)}</span>;
  if (Array.isArray(value)) return <ListView rows={value} />;
  const entries = Object.entries(value).filter(([k, v]) => !hiddenKey(k, v));
  if (!entries.length) return <span className="muted">—</span>;
  return (
    <div className="table-wrap"><table className="mini data-view">
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k}><th scope="row">{humanize(k)}</th><td className={isNumeric(v) ? "num" : ""}><DataView value={v} /></td></tr>
        ))}
      </tbody>
    </table></div>
  );
}

function ListView({ rows }: { rows: any[] }): JSX.Element {
  const records = rows.filter((r) => r && typeof r === "object" && !Array.isArray(r));
  if (records.length !== rows.length) {
    const simple = rows.every((r) => typeof r !== "object" || r === null);
    if (simple) return <span>{rows.map(scalar).join(", ")}</span>;
    return <div className="stack">{rows.map((r, i) => <DataView key={i} value={r} />)}</div>;
  }
  const cols = Array.from(new Set(records.flatMap((r) => Object.keys(r))))
    .filter((c) => records.some((r) => !isEmpty(r[c]) && !hiddenKey(c, r[c])));
  if (!cols.length) return <span className="muted">—</span>;
  return (
    <div className="table-wrap"><table className="mini data-view">
      <thead><tr>{cols.map((c) => <th key={c}>{humanize(c)}</th>)}</tr></thead>
      <tbody>
        {records.map((r, i) => (
          <tr key={i}>{cols.map((c) => <td key={c} className={isNumeric(r[c]) ? "num" : ""}><DataView value={r[c]} /></td>)}</tr>
        ))}
      </tbody>
    </table></div>
  );
}

/** A value inside an existing table cell or sentence: plain text when simple, a table when structured. */
export function inlineValue(v: any): ReactNode {
  if (isEmpty(v)) return "—";
  return typeof v === "object" ? <DataView value={v} /> : scalar(v);
}
