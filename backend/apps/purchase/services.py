"""
Purchase planning and receipt (brief p.13, p.39).

Shortage netting uses one consistent reservation basis:
    free = usable on-hand - active reservations   (released PRs/SO lines already reserved)
    unreserved demand = draft-PR requirements + plan-line requirements not yet in a PR
    shortage(date) = unreserved demand due by date + safety stock - free - PO arrivals promised by that date
Materials already issued against a PR are not demand again; an arrival after the
need date cannot solve an earlier shortage.
"""
import math
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.core import audit
from apps.core.api import BusinessRuleError
from apps.core.services import notify_perm
from apps.inventory import services as inv

from .models import GRN, POLine, PurchaseOrder


def _round_order(item, qty):
    qty = max(Decimal(qty), item.moq or Decimal("0"))
    if item.order_multiple and item.order_multiple > 0:
        qty = Decimal(math.ceil(qty / item.order_multiple)) * item.order_multiple
    return qty


def demand_by_item():
    """Unreserved, dated demand rows per item."""
    from apps.planning.models import PlanLine, PRMaterial, ProductionRequest
    from apps.planning.services import active_recipe, line_metrics, pr_materials

    rows = defaultdict(list)
    for mat in PRMaterial.objects.filter(pr__status="draft").select_related("item", "pr"):
        rows[mat.item_id].append({"date": mat.pr.shift_date, "qty": mat.pending_issue, "source": mat.pr.doc_no})
    for line in PlanLine.objects.filter(status__in=["planned", "released", "running", "stopped"]).select_related("component", "artwork", "demand__so_line"):
        m = line_metrics(line)
        in_prs = ProductionRequest.objects.filter(plan_line=line, status__in=["draft", "released", "in_progress"]).aggregate(t=Sum("planned_pcs"))["t"] or 0
        future = max(m["gross_remaining"] - in_prs, 0)
        if future <= 0:
            continue
        need_date = timezone.localtime(line.forecast_start).date() if line.forecast_start else timezone.localdate()
        recipe = active_recipe(line.component)
        try:
            mats = pr_materials(component=line.component, recipe=recipe, gross_pcs=future, artwork=line.artwork,
                                so_line=line.demand.so_line if line.demand_id else None)
        except Exception:
            continue
        for r in mats:
            rows[r["item"].pk].append({"date": need_date, "qty": Decimal(r["qty"]), "source": f"Plan {line.machine.code} #{line.sequence}"})
    return rows


def shortage_report():
    from apps.masters.models import Item

    demand = demand_by_item()
    item_ids = set(demand) | set(Item.objects.filter(active=True, min_stock__gt=0).values_list("pk", flat=True))
    out = []
    for item in Item.objects.filter(pk__in=item_ids):
        bal = inv.balance_summary(item)
        free = bal["available"]
        arrivals = sorted(
            ((l.promised_date, l.balance_qty, l) for l in POLine.objects.filter(item=item, status__in=["open", "partial"],
                                                                                 po__status__in=["approved", "sent", "partial"])),
            key=lambda x: x[0],
        )
        safety = item.safety_stock or Decimal("0")
        cumulative = Decimal("0")
        first_short = None
        worst = Decimal("0")
        for row in sorted(demand.get(item.pk, []), key=lambda r: r["date"]):
            cumulative += row["qty"]
            supply_by_date = free + sum((q for d, q, _ in arrivals if d <= row["date"]), Decimal("0"))
            gap = cumulative + safety - supply_by_date
            if gap > 0 and first_short is None:
                first_short = {"date": row["date"], "source": row["source"], "gap": gap}
            worst = max(worst, gap)
        if not demand.get(item.pk) and free < item.min_stock:
            worst = item.min_stock - free
            first_short = {"date": timezone.localdate(), "source": "Below minimum stock", "gap": worst}
        if worst <= 0:
            continue
        late = [{"po": l.po.doc_no, "promised": d, "qty": str(q)} for d, q, l in arrivals if first_short and d > first_short["date"]]
        out.append({
            "item": str(item.pk), "item_code": item.code, "item_name": item.name, "uom": item.stock_uom,
            "need_date": first_short["date"], "first_short_source": first_short["source"],
            "usable": str(bal["usable"]), "reserved": str(bal["reserved"]), "free": str(free), "incoming": str(bal["incoming"]),
            "unreserved_demand": str(cumulative), "shortage": str(worst), "suggested_order": str(_round_order(item, worst)),
            "arrivals_too_late": late,
        })
    out.sort(key=lambda r: r["need_date"])
    return out


def post_grn(grn, user):
    """Physical receipt -> lot awaiting QC. PO balance changes only on QC acceptance (p.13)."""
    if grn.status != "draft":
        raise BusinessRuleError("GRN already posted.", "invalid_state")
    lines = list(grn.lines.select_related("item", "po_line", "artwork"))
    if not lines:
        raise BusinessRuleError("Add GRN lines first.", "no_lines")
    for line in lines:
        if line.po_line_id:
            pol = line.po_line
            if pol.item_id != line.item_id:
                raise BusinessRuleError(f"GRN line item differs from PO line {pol}.", "po_mismatch")
            if pol.po.status not in {"approved", "sent", "partial"}:
                raise BusinessRuleError(f"PO {pol.po.doc_no} is not open for receipt.", "po_not_open")
        if line.item.kind == "label":
            if not line.artwork_id:
                raise BusinessRuleError("Label receipts must state the artwork revision.", "artwork_required")
            if line.po_line_id and line.po_line.artwork_id and line.po_line.artwork_id != line.artwork_id:
                raise BusinessRuleError("Received label revision differs from the PO revision.", "wrong_artwork")
        line.lot = inv.receipt_into_stock(
            item=line.item, qty=line.received_qty, location=grn.location, user=user, source="grn", quality_state="pending",
            ref={"resource": "purchase.grn", "id": grn.pk, "doc": grn.doc_no}, artwork=line.artwork, supplier_lot=line.supplier_lot, grn_line=line,
        )
        if line.declared_qty != line.received_qty:
            line.discrepancy = f"Declared {line.declared_qty}, received {line.received_qty}"
        line.save()
        if line.po_line_id:
            POLine.objects.filter(pk=line.po_line_id).update(received_qty=line.po_line.received_qty + line.received_qty)
    grn.status = "posted"
    grn.posted_at = timezone.now()
    grn.row_version += 1
    grn.save()
    if grn.gate_entry_id:
        g = grn.gate_entry
        g.status = "closed" if not any(l.discrepancy for l in lines) else "discrepancy"
        g.save(update_fields=["status", "updated_at"])
    audit.record("grn_posted", "purchase.grn", grn, changes={"lines": len(lines)})
    notify_perm("quality.release", f"Incoming inspection: {grn.doc_no}", resource="purchase.grn", object_id=grn.pk,
                next_action="Inspect and accept / hold / reject", dedupe_key=f"grn-qc:{grn.pk}")
    return grn


def decide_grn_line(line, accepted, held, rejected, user, reason=""):
    """QC acceptance: usable stock and PO balance increase by the accepted amount only (p.13, p.39)."""
    accepted, held, rejected = Decimal(str(accepted or 0)), Decimal(str(held or 0)), Decimal(str(rejected or 0))
    if line.qc_status == "decided":
        raise BusinessRuleError("QC decision already posted for this line.", "invalid_state")
    if accepted + held + rejected != line.received_qty:
        raise BusinessRuleError(f"Accepted + held + rejected must equal received {line.received_qty}.", "not_reconciled")
    lot = line.lot
    if held:
        inv.set_quality(lot, held, "held", user, reason or "Incoming QC hold")
    if rejected:
        inv.set_quality(lot, rejected, "rejected", user, reason or "Incoming QC reject")
    if accepted:
        inv.set_quality(lot, accepted, "released", user, reason or "Incoming QC accept")
    line.accepted_qty, line.held_qty, line.rejected_qty = accepted, held, rejected
    line.qc_status = "decided"
    line.save()
    if line.po_line_id:
        pol = POLine.objects.select_for_update().get(pk=line.po_line_id)
        pol.accepted_qty += accepted
        pol.rejected_qty += rejected
        pol.status = "received" if pol.accepted_qty >= pol.qty else "partial"
        pol.save()
        po = pol.po
        statuses = set(po.lines.values_list("status", flat=True))
        po.status = "closed" if statuses <= {"received", "closed", "cancelled"} else "partial"
        po.save(update_fields=["status", "updated_at"])
    audit.record("grn_qc_decided", "purchase.grnline", line, changes={"accepted": str(accepted), "held": str(held), "rejected": str(rejected)}, reason=reason)
    return line


def overdue_po_lines(today=None):
    today = today or timezone.localdate()
    return POLine.objects.filter(status__in=["open", "partial"], promised_date__lt=today, po__status__in=["approved", "sent", "partial"]).select_related("po__supplier", "item")


def po_value(po):
    return sum((l.qty * l.rate for l in po.lines.all()), Decimal("0")) + (po.freight_charges or 0)


__all__ = ["shortage_report", "post_grn", "decide_grn_line", "overdue_po_lines", "po_value", "GRN", "PurchaseOrder"]
