"""Sales order services (brief p.7, p.18)."""
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core import audit
from apps.core.api import BusinessRuleError
from apps.core.rbac import has_perm
from apps.core.services import approval_handler, notify, notify_perm, request_approval

from .models import ComponentDemand, SalesOrder, SOLine, SOVersion

SCOPE_COMPONENTS = {"set": None, "container": {"container"}, "lid": {"lid"}, "handle": {"handle"}}
AMENDABLE_LINE_FIELDS = {"qty", "committed_date", "requested_date", "flavour", "container_artwork", "lid_artwork", "masterbatch",
                         "container_colour", "lid_colour", "unit_price", "priority", "special_instructions", "impact_modifier"}


def validate_line(line):
    """Dependent-selection rules enforced on the server (p.5): prevent incompatible combinations."""
    product = line.product
    if not product.active:
        raise BusinessRuleError("Product is inactive.", "invalid_product")
    if product.customer_id and product.customer_id != line.so.customer_id:
        raise BusinessRuleError("This customer-specific product belongs to another customer.", "incompatible_product")
    comps = {c.component_type for c in product.components.all()}
    needed = SCOPE_COMPONENTS[line.scope] or comps
    if not needed <= comps:
        raise BusinessRuleError(f"Product has no {', '.join(needed - comps)} component.", "incompatible_scope")
    for ctype, art in (("container", line.container_artwork), ("lid", line.lid_artwork)):
        if art is None:
            continue
        if art.product_id != product.id or art.component_type != ctype:
            raise BusinessRuleError(f"{ctype.title()} artwork {art} does not belong to {product.code} {ctype}.", "incompatible_artwork")
        if art.customer_id and art.customer_id != line.so.customer_id and not art.common_design:
            raise BusinessRuleError(f"Artwork {art} is another customer's design.", "incompatible_artwork")
        if line.flavour and art.flavour and art.flavour != line.flavour:
            raise BusinessRuleError(f"Artwork {art} is for flavour '{art.flavour}', line is '{line.flavour}'.", "incompatible_artwork")
    if line.masterbatch and line.masterbatch.kind != "masterbatch":
        raise BusinessRuleError("Selected masterbatch item is not a masterbatch.", "invalid_masterbatch")
    if line.iml and not product.iml:
        raise BusinessRuleError("Product is non-IML.", "incompatible_iml")


def regenerate_demand(line):
    """Complete sets create linked component demand per BOM; component-only lines create one (p.7)."""
    wanted = SCOPE_COMPONENTS[line.scope]
    open_qty = line.qty - line.cancelled_qty
    seen = set()
    for comp in line.product.components.select_related("item"):
        if wanted is not None and comp.component_type not in wanted:
            continue
        art = line.container_artwork if comp.component_type == "container" else line.lid_artwork if comp.component_type == "lid" else None
        dem, created = ComponentDemand.objects.get_or_create(
            so_line=line, component=comp,
            defaults={"component_type": comp.component_type, "item": comp.item, "artwork": art, "required_qty": open_qty * comp.qty_per_set},
        )
        if not created:
            dem.required_qty = open_qty * comp.qty_per_set
            dem.artwork = art
            dem.active = open_qty > 0 and line.status != "cancelled"
            dem.save()
        seen.add(dem.pk)
    ComponentDemand.objects.filter(so_line=line).exclude(pk__in=seen).update(active=False)


def so_snapshot(so):
    return {
        "doc_no": so.doc_no, "version": so.version, "customer": str(so.customer_id), "customer_po_no": so.customer_po_no,
        "payment_terms": so.payment_terms, "freight_basis": so.freight_basis,
        "lines": [
            {k: audit._jsonable(getattr(l, k + "_id", None) if k in {"product", "container_artwork", "lid_artwork", "masterbatch"} else getattr(l, k))
             for k in ["line_no", "product", "scope", "flavour", "container_artwork", "lid_artwork", "masterbatch", "container_colour",
                       "lid_colour", "impact_modifier", "qty", "unit_price", "requested_date", "committed_date", "status", "cancelled_qty"]}
            for l in so.lines.all()
        ],
    }


def confirm(so, user):
    if so.status != "draft":
        raise BusinessRuleError("Only draft orders can be confirmed.", "invalid_state")
    lines = list(so.lines.select_related("product", "container_artwork", "lid_artwork", "masterbatch", "so"))
    if not lines:
        raise BusinessRuleError("Add at least one line before confirming.", "no_lines")
    for line in lines:
        validate_line(line)
        regenerate_demand(line)
    so.status = "confirmed"
    so.row_version += 1
    so.save()
    SOVersion.objects.get_or_create(so=so, version=so.version, defaults={"snapshot": so_snapshot(so), "reason": "Confirmed", "created_by": user})
    audit.record("so_confirmed", "sales.salesorder", so)
    notify_perm("planning.planline.create", f"New confirmed order {so.doc_no} - {so.customer.name}", resource="sales.salesorder",
                object_id=so.pk, next_action="Plan component demand", dedupe_key=f"so-confirmed:{so.pk}")
    return so


def request_line_amendment(line, changes, reason, user):
    """After the 24-hour lock, changes need reason + approval (maker/checker) and create a new version (p.7)."""
    bad = set(changes) - AMENDABLE_LINE_FIELDS
    if bad:
        raise BusinessRuleError(f"Fields not amendable: {', '.join(sorted(bad))}", "validation")
    old = {k: audit._jsonable(getattr(line, k + "_id" if k in {"container_artwork", "lid_artwork", "masterbatch"} else k)) for k in changes}
    impact = amendment_impact(line, changes)
    return request_approval(
        kind="so_amendment", resource="sales.soline", obj=line, summary=f"Amend {line} ({', '.join(changes)})",
        payload={"line": str(line.pk), "old": old, "new": changes, "impact": impact}, reason=reason,
        required_permission="sales.so.approve_amendment", requester=user,
    )


def amendment_impact(line, changes):
    from apps.planning.models import ProductionRequest

    prs = ProductionRequest.objects.filter(so_line=line, status__in=["draft", "released", "in_progress"])
    return {
        "active_prs": [p.doc_no for p in prs],
        "produced_good": int(quantities(line)["good_produced"]),
        "dispatched": line.dispatched_qty,
        "note": "Quantity/date/artwork amendments recalculate remaining demand; produced and dispatched quantities stay recorded.",
    }


@approval_handler("so_amendment")
def apply_line_amendment(approval, approver):
    from apps.masters.models import Artwork, Item

    line = SOLine.objects.select_for_update().select_related("so").get(pk=approval.payload["line"])
    so = SalesOrder.objects.select_for_update().get(pk=line.so_id)
    new = approval.payload["new"]
    before = audit.snapshot(line)
    for field, value in new.items():
        if field in {"container_artwork", "lid_artwork"}:
            setattr(line, field, Artwork.objects.get(pk=value) if value else None)
        elif field == "masterbatch":
            setattr(line, field, Item.objects.get(pk=value) if value else None)
        else:
            setattr(line, field, value)
    if "qty" in new and int(new["qty"]) < line.dispatched_qty:
        raise BusinessRuleError("New quantity is below already dispatched quantity.", "invalid_qty")
    validate_line(line)
    line.row_version += 1
    line.full_clean()
    line.save()
    so.version += 1
    so.row_version += 1
    so.save()
    regenerate_demand(line)
    SOVersion.objects.create(so=so, version=so.version, snapshot=so_snapshot(so), reason=approval.reason, approval=approval, created_by=approver)
    audit.record("so_amended", "sales.soline", line, changes=audit.diff(before, audit.snapshot(line)), reason=approval.reason, approval_ref=approval.pk)
    notify_perm("planning.planline.update", f"SO amendment approved: {line}", body=str(approval.payload.get("impact")), severity="warning",
                resource="sales.soline", object_id=line.pk, next_action="Review plan, PRs and material reservations",
                dedupe_key=f"so-amend:{approval.pk}")


def cancel_line_balance(line, reason, user):
    """Cancellation preserves history; produced/dispatched quantities stay recorded (p.7)."""
    from apps.inventory.services import release_reservations

    if line.status == "cancelled":
        raise BusinessRuleError("Line already cancelled.", "invalid_state")
    balance = line.qty - line.cancelled_qty - line.dispatched_qty
    q = quantities(line)
    line.cancelled_qty += balance
    line.status = "cancelled" if line.dispatched_qty == 0 else "closed"
    line.row_version += 1
    line.save()
    regenerate_demand(line)
    release_reservations(so_line=line, reason=f"SO line cancelled: {reason}")
    audit.record("so_line_cancelled", "sales.soline", line, changes={"cancelled_balance": balance, "produced_good": str(q["good_produced"])}, reason=reason)
    if q["good_produced"] > 0:
        notify_perm("fg.packingrecord.create", f"Disposition needed: produced stock for cancelled {line}", severity="warning",
                    resource="sales.soline", object_id=line.pk, next_action="Allocate to another order, keep approved stock, rework or scrap",
                    dedupe_key=f"cancel-disposition:{line.pk}")
    return line


def quantities(line):
    """Ordered / allocated / planned / good / held / packed / dispatched / remaining - kept separate (p.7)."""
    from apps.fg.models import PackingRecord
    from apps.inventory.models import Reservation
    from apps.planning.models import PlanLine
    from apps.production.models import ProductionReport

    demands = list(line.demands.filter(active=True))
    per_comp = {}
    for d in demands:
        planned = PlanLine.objects.filter(demand=d).exclude(status="cancelled").aggregate(t=Sum("planned_qty"))["t"] or 0
        reports = ProductionReport.objects.filter(pr__plan_line__demand=d, status__in=["submitted", "corrected"])
        good = reports.aggregate(t=Sum("good_qty"))["t"] or 0
        held = reports.aggregate(t=Sum("held_qty"))["t"] or 0
        allocated = Reservation.objects.filter(so_line=line, item=d.item, status="active").aggregate(t=Sum("qty"))["t"] or 0
        per_comp[d.component_type] = {"required": d.required_qty, "planned": planned, "good_produced": good, "held": held, "allocated": Decimal(allocated)}
    packed = PackingRecord.objects.filter(so_line=line, status="posted").aggregate(t=Sum("sets_packed"))["t"] or 0
    good_min = min((c["good_produced"] for c in per_comp.values()), default=0)
    return {
        "ordered": line.qty, "cancelled": line.cancelled_qty, "components": per_comp,
        "good_produced": good_min, "held": sum((c["held"] for c in per_comp.values()), 0),
        "planned": min((c["planned"] for c in per_comp.values()), default=0),
        "allocated": min((c["allocated"] for c in per_comp.values()), default=0),
        "packed": packed, "dispatched": line.dispatched_qty, "remaining": line.balance_qty,
    }


def readiness(line):
    """Commercial, artwork and material readiness visible before job release (p.7)."""
    issues = []
    so = line.so
    if so.status != "confirmed":
        issues.append("Order not confirmed")
    if so.advance_required_pct and not so.commercial_approved:
        issues.append("Commercial approval pending")
    if line.iml:
        for ctype, art in (("container", line.container_artwork), ("lid", line.lid_artwork)):
            if line.scope in {"set", ctype} and line.product.components.filter(component_type=ctype, iml=True).exists():
                if art is None:
                    issues.append(f"{ctype} artwork not selected")
                elif art.status != "approved":
                    issues.append(f"{ctype} artwork {art} not approved ({art.get_status_display()})")
    return {"ready": not issues, "blockers": issues}


def customer_timeline(request, customer):
    from apps.crm.models import Quotation, Sample
    from apps.fg.models import Dispatch
    from apps.quality.models import Complaint

    show_price = has_perm(request.user, "commercial.price.view")
    events = []
    for q in Quotation.objects.filter(customer=customer).order_by("-created_at")[:20]:
        events.append({"at": q.created_at, "type": "quotation", "doc": q.doc_no, "status": q.status})
    for s in Sample.objects.filter(customer=customer).order_by("-created_at")[:20]:
        events.append({"at": s.created_at, "type": "sample", "doc": s.doc_no, "status": s.status})
    for so in SalesOrder.objects.filter(customer=customer).order_by("-created_at")[:20]:
        e = {"at": so.created_at, "type": "order", "doc": so.doc_no, "status": so.status}
        if show_price:
            e["value"] = str(so.order_value)
        events.append(e)
    for d in Dispatch.objects.filter(customer=customer).order_by("-created_at")[:20]:
        events.append({"at": d.created_at, "type": "dispatch", "doc": d.doc_no, "status": d.status})
    for c in Complaint.objects.filter(customer=customer).order_by("-created_at")[:20]:
        events.append({"at": c.created_at, "type": "complaint", "doc": c.doc_no, "status": c.status})
    events.sort(key=lambda e: e["at"], reverse=True)
    return events


@transaction.atomic
def lock_check(so):
    """Server-side 24h lock (uses server time; applies to every entry route)."""
    return so.is_locked
