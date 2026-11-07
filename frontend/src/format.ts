import type { FieldMeta, Row } from "./types";

const dt = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
const d = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
const num = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 });

export function fmtDateTime(v?: string | null) {
  return v ? dt.format(new Date(v)) : "";
}
export function fmtDate(v?: string | null) {
  return v ? d.format(new Date(v.length === 10 ? v + "T00:00:00+05:30" : v)) : "";
}

const SHORT_WORDS = new Set(["uom", "qty", "so", "po", "pr", "grn", "qc", "fg", "sfg", "iml", "moq", "gst", "pm", "kg", "pcs"]);

/** "planned_qty" -> "Planned QTY": a data key as a column heading. */
export function humanize(key: string) {
  const text = key.split("_").map((w) => (SHORT_WORDS.has(w.toLowerCase()) ? w.toUpperCase() : w)).join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Structured data as one line of text, for messages that cannot hold a table. */
export function textOf(v: any): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (Array.isArray(v)) return v.map(textOf).join(", ");
  if (typeof v === "object") return Object.entries(v).map(([k, x]) => `${humanize(k)}: ${textOf(x)}`).join("; ");
  return String(v);
}

export function display(row: Row, name: string, meta?: FieldMeta): string {
  const v = row[name];
  if (row[`${name}_label`] !== undefined) return row[`${name}_label`] ?? "";
  if (v === null || v === undefined || v === "") return "";
  if (!meta) return textOf(v);
  switch (meta.type) {
    case "boolean": return v ? "Yes" : "No";
    case "datetime": return fmtDateTime(v);
    case "date": return fmtDate(v);
    case "decimal":
    case "integer": return num.format(Number(v));
    case "choice": return meta.choices?.find((c) => c.value === v)?.label ?? String(v);
    case "json": return Array.isArray(v) ? `${v.length} item(s)` : typeof v === "object" ? `${Object.keys(v).length} field(s)` : String(v);
    default: return textOf(v);
  }
}

const TONES: Record<string, string> = {
  // danger
  breakdown: "danger", rejected: "danger", cancelled: "muted", blocked: "danger", stopped: "danger", no_report: "warn", held: "warn",
  quality_hold: "warn", lost: "danger", failed: "danger", discrepancy: "danger", critical: "danger", overdue: "danger",
  // warn
  pending: "warn", pending_approval: "warn", awaiting_approval: "warn", draft: "muted", setup: "warn", maintenance: "warn",
  submitted: "info", reopened: "warn", partial: "warn", on_hold: "warn", planned_idle: "muted", rework: "warn",
  // ok
  running: "ok", released: "ok", approved: "ok", confirmed: "ok", verified: "ok", closed: "muted", posted: "ok", delivered: "ok",
  inside: "info", exited: "muted", won: "ok", in_progress: "info", active: "ok", available: "ok", done: "ok", decided: "ok",
};
export const toneFor = (status?: string) => TONES[String(status ?? "")] ?? "info";

export const titleOf = (row: Row, field: string) =>
  row[`${field}_label`] || row[field] || row.doc_no || row.name || row.code || row.title || row.id?.slice(0, 8);
