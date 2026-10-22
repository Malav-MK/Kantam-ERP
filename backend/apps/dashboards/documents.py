"""
Printable documents (brief p.20). Rendered from saved versions only, with
company details, document/version ID, date, approvals and a viewer watermark.
Browsers print these to PDF. Every request re-checks permission and scope.
"""
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import escape
from rest_framework.views import APIView

from apps.core import audit
from apps.core.models import Company
from apps.core.rbac import has_perm
from apps.core.resource_api import scoped_get

_LOGO = None


def _logo_svg():
    """Official Kantam logo, inlined (no external fetch; CSP allows no remote images)."""
    global _LOGO
    if _LOGO is None:
        from pathlib import Path
        raw = (Path(__file__).parent / "assets" / "kantam-logo.svg").read_text()
        _LOGO = raw[raw.index("<svg"):].replace("<svg ", '<svg class="logo" ', 1)
    return _LOGO


CSS = """
.logo{height:56px;width:auto;display:block;margin-bottom:4px}
body{font:12px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#111;margin:24px}
h1{font-size:18px;margin:0}h2{font-size:13px;margin:18px 0 6px}
.head{display:flex;justify-content:space-between;border-bottom:2px solid #111;padding-bottom:8px;margin-bottom:12px}
table{width:100%;border-collapse:collapse;margin-top:6px}th,td{border:1px solid #999;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#eee}.meta td{border:none;padding:2px 8px 2px 0}.wm{position:fixed;bottom:8px;left:24px;right:24px;font-size:10px;color:#666;border-top:1px solid #ccc;padding-top:4px}
.num{text-align:right}.sig{margin-top:40px;display:flex;gap:60px}.sig div{border-top:1px solid #111;padding-top:4px;min-width:160px}
@media print{.noprint{display:none}}
"""


def _company():
    c = Company.objects.first()
    return (c.name, c.address, c.gstin) if c else ("Kantam Kontainers LLP", "Bareja, Ahmedabad, Gujarat", "")


def page(request, title, doc_no, version, body):
    name, address, gstin = _company()
    stamp = timezone.localtime().strftime("%d-%m-%Y %H:%M")
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)} {escape(doc_no)}</title><style>{CSS}</style></head><body>
<div class="head"><div>{_logo_svg()}<h1>{escape(name)}</h1><div>{escape(address)}</div><div>{('GSTIN ' + escape(gstin)) if gstin else ''}</div></div>
<div style="text-align:right"><h1>{escape(title)}</h1><div><b>{escape(doc_no)}</b></div><div>Version {escape(str(version))}</div></div></div>
{body}
<div class="wm">Internal document generated from saved record {escape(doc_no)} v{escape(str(version))} - viewed by {escape(request.user.full_name)} at {stamp} IST. Confidential.</div>
<p class="noprint"><button onclick="window.print()">Print / Save as PDF</button></p></body></html>"""
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"
    return resp


def table(headers, rows):
    th = "".join(f"<th>{escape(h)}</th>" for h in headers)
    tr = "".join("<tr>" + "".join(f"<td>{escape(str(c if c is not None else ''))}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>"


def meta(pairs):
    return "<table class='meta'>" + "".join(f"<tr><td><b>{escape(k)}</b></td><td>{escape(str(v if v is not None else ''))}</td></tr>" for k, v in pairs) + "</table>"


class DocumentView(APIView):
    KINDS = {
        "so": ("sales.salesorder", "Sales order"),
        "pr": ("planning.productionrequest", "Shift PR / material request"),
        "po": ("purchase.purchaseorder", "Purchase order"),
        "grn": ("purchase.grn", "Goods receipt note"),
        "coa": ("quality.inspection", "Certificate of analysis"),
        "picklist": ("fg.dispatch", "Packing / pick list"),
    }

    def get_required_permission(self, request):
        key = self.KINDS.get(self.kwargs.get("kind"), (None,))[0]
        return f"{key}.view" if key else None

    def get(self, request, kind, pk):
        key, title = self.KINDS[kind]
        obj = scoped_get(request, key, pk)
        audit.record("document_viewed", key, obj, changes={"document": kind})
        return getattr(self, f"render_{kind}")(request, obj, title)

    def render_so(self, request, so, title):
        price = has_perm(request.user, "commercial.price.view")
        rows = []
        for l in so.lines.select_related("product", "container_artwork", "lid_artwork", "masterbatch"):
            r = [l.line_no, l.product.code, l.product.name, l.get_scope_display(), l.flavour, l.container_artwork or "", l.lid_artwork or "",
                 l.masterbatch.name if l.masterbatch_id else "", l.qty, l.committed_date or l.requested_date or ""]
            if price:
                r.append(l.unit_price)
            rows.append(r)
        headers = ["#", "Code", "Product", "Scope", "Flavour", "Container art", "Lid art", "Masterbatch", "Qty", "Delivery"] + (["Rate"] if price else [])
        body = meta([("Customer", so.customer.name), ("Customer PO", f"{so.customer_po_no} {so.customer_po_date or ''}"), ("Order date", so.order_date),
                     ("Salesperson", so.salesperson.full_name), ("Payment terms", so.payment_terms), ("Freight", so.freight_basis),
                     ("Status", so.get_status_display()), ("Commercial approval", "Yes" if so.commercial_approved else "Pending")])
        body += "<h2>Lines</h2>" + table(headers, rows)
        return page(request, title, so.doc_no, so.version, body)

    def render_pr(self, request, pr, title):
        mats = [[m.item.code, m.item.name, m.get_kind_display() if hasattr(m, "get_kind_display") else m.kind, m.required_qty, m.uom, m.issued_qty, m.returned_qty]
                for m in pr.materials.select_related("item")]
        body = meta([("Machine", pr.machine), ("Shift", f"{pr.shift_date} {pr.get_shift_display()}"), ("Segment", f"{timezone.localtime(pr.segment_start):%H:%M} - {timezone.localtime(pr.segment_end):%H:%M}"),
                     ("Mould", pr.mould), ("Component", pr.component), ("Artwork", pr.artwork or "Non-IML"), ("SO line", pr.so_line or "Stock job"),
                     ("Planned moulded pcs", pr.planned_pcs), ("Active cavities / cycle", f"{pr.active_cavities} / {pr.cycle_s}s"),
                     ("Status", pr.get_status_display()), ("Released by", pr.released_by.full_name if pr.released_by_id else "-")])
        body += "<h2>Shift material requirement (12-hour shift)</h2>" + table(["Code", "Material", "Kind", "Required", "Unit", "Issued", "Returned"], mats)
        body += "<div class='sig'><div>Store issuer</div><div>Machine-side receiver</div><div>Shift supervisor</div></div>"
        return page(request, title, pr.doc_no, pr.row_version, body)

    def render_po(self, request, po, title):
        rate = has_perm(request.user, "purchase.rate.view")
        rows = [[l.line_no, l.item.code, l.item.name, l.artwork or "", l.qty, l.item.stock_uom, l.promised_date] + ([l.rate] if rate else []) for l in po.lines.select_related("item", "artwork")]
        body = meta([("Supplier", po.supplier.name), ("Order date", po.order_date), ("Terms", po.terms), ("Status", po.get_status_display()),
                     ("Approved by", po.approved_by.full_name if po.approved_by_id else "Not approved")])
        body += table(["#", "Code", "Item", "Artwork rev.", "Qty", "Unit", "Delivery"] + (["Rate"] if rate else []), rows)
        return page(request, title, po.doc_no, po.row_version, body)

    def render_grn(self, request, grn, title):
        rows = [[l.item.code, l.artwork or "", l.supplier_lot, l.declared_qty, l.received_qty, l.accepted_qty, l.held_qty, l.rejected_qty, l.lot.doc_no if l.lot_id else "", l.discrepancy]
                for l in grn.lines.select_related("item", "artwork", "lot")]
        body = meta([("Supplier", grn.supplier.name), ("Invoice", f"{grn.invoice_no} {grn.invoice_date or ''}"), ("Gate entry", grn.gate_entry or "-"),
                     ("Location", grn.location), ("Status", grn.get_status_display())])
        body += table(["Item", "Artwork", "Supplier lot", "Declared", "Received", "Accepted", "Held", "Rejected", "Lot", "Discrepancy"], rows)
        return page(request, title, grn.doc_no, grn.row_version, body)

    def render_coa(self, request, insp, title):
        if insp.status != "decided":
            return HttpResponse("COA is generated only from a decided inspection.", status=409, content_type="text/plain")
        params = {}
        for r in insp.results or []:
            p = params.setdefault(r.get("parameter", "?"), {"unit": r.get("unit", ""), "spec": {}, "container": "", "lid": ""})
            comp = (r.get("component") or "container").lower()
            p["spec"][comp] = r.get("spec", "")
            p[comp if comp in {"container", "lid"} else "container"] = f"{r.get('actual', '')} ({'Pass' if r.get('pass') else 'Fail'})"
        rows = [[k, v["unit"], v["spec"].get("container", ""), v["container"], v["spec"].get("lid", ""), v["lid"]] for k, v in params.items()]
        lot = insp.lot
        body = meta([("Product", lot.product if lot and lot.product_id else "-"), ("Lot / batch", lot.doc_no if lot else "-"),
                     ("Production", f"{lot.pr.doc_no} {lot.shift_date} {lot.shift}" if lot and lot.pr_id else "-"),
                     ("Specification revision", insp.spec_revision), ("Sample size", insp.sample_size), ("Inspector", insp.inspector.full_name),
                     ("Decided", f"{insp.decided_by.full_name if insp.decided_by_id else ''} {timezone.localtime(insp.decided_at):%d-%m-%Y %H:%M}" if insp.decided_at else "")])
        body += "<h2>Results - container and lid recorded separately</h2>" + table(["Parameter", "Unit", "Container spec", "Container result", "Lid spec", "Lid result"], rows)
        body += f"<p>Disposition: released {escape(str(insp.qty_release))}, held {escape(str(insp.qty_hold))}, rejected {escape(str(insp.qty_reject))}.</p>"
        return page(request, title, insp.doc_no, insp.row_version, body)

    def render_picklist(self, request, d, title):
        rows = [[str(l.so_line), l.lot.doc_no, l.lot.item.code, l.lot.flavour, l.qty, l.cartons] for l in d.lines.select_related("so_line__product", "lot__item")]
        body = meta([("Order", d.so.doc_no), ("Customer", d.customer.name if has_perm(request.user, "masters.customer.view") else "(restricted)"),
                     ("Planned date", d.planned_date), ("Vehicle", d.vehicle_no), ("Transporter", d.transporter), ("Status", d.get_status_display())])
        body += table(["SO line", "Lot", "Item", "Flavour", "Qty", "Cartons"], rows)
        body += "<div class='sig'><div>Picked by</div><div>QC verified</div><div>Loading approved</div></div>"
        return page(request, title, d.doc_no, d.row_version, body)
