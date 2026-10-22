"""Production execution services (brief p.10, p.18)."""
import datetime

from django.db.models import Q, Sum
from django.utils import timezone

from apps.core import audit, calc
from apps.core.api import BusinessRuleError
from apps.core.models import ShiftLock
from apps.core.rbac import has_perm
from apps.core.services import notify_perm
from apps.core.shifts import ShiftSlot, is_shift_closed
from apps.inventory import services as inv
from apps.masters.models import Location

from .models import Downtime, ProductionReport

SLACK = datetime.timedelta(minutes=30)


def _location(kind):
    loc = Location.objects.filter(kind=kind, active=True).order_by("code").first()
    if loc is None:
        loc = Location.objects.create(code=kind.upper(), name=kind.replace("_", " ").title(), kind=kind)
    return loc


def assert_shift_open(shift_date, shift, user):
    if is_shift_closed(shift_date, shift) and not has_perm(user, "production.correct_closed"):
        raise BusinessRuleError("Shift is closed. Records are immutable except by controlled admin correction.", "shift_closed")


def validate_report(report):
    slot = ShiftSlot(report.shift_date, report.shift)
    if report.actual_end <= report.actual_start:
        raise BusinessRuleError("End time must be after start time.", "invalid_time")
    if report.actual_end > timezone.now() + datetime.timedelta(minutes=15):
        raise BusinessRuleError("Production cannot be reported for a time in the future.", "future_time")
    if report.actual_start < slot.start - SLACK or report.actual_end > slot.end + SLACK:
        raise BusinessRuleError(f"Times must fall within the {slot.label} shift window.", "outside_shift")
    if not calc.reconcile_output(report.total_moulded, report.good_qty, report.held_qty, report.rejected_qty):
        raise BusinessRuleError(
            f"Total moulded {report.total_moulded} must equal good {report.good_qty} + held {report.held_qty} + rejected {report.rejected_qty}.",
            "output_not_reconciled",
        )
    if report.active_cavities > report.pr.mould.cavities:
        raise BusinessRuleError("Active cavities exceed mould cavities.", "invalid_cavities")
    overlap = ProductionReport.objects.filter(machine=report.machine, status__in=["submitted", "corrected"]).exclude(pk=report.pk).filter(
        Q(actual_start__lt=report.actual_end) & Q(actual_end__gt=report.actual_start))
    if overlap.exists():
        raise BusinessRuleError(f"Time overlaps report {overlap.first().doc_no} on this machine.", "time_overlap")


def submit_report(report, user, consumption=None):
    pr = report.pr
    if pr.status not in {"released", "in_progress"}:
        raise BusinessRuleError("Report production only against a released PR.", "pr_not_released")
    if report.status != "draft":
        raise BusinessRuleError("Report already submitted.", "invalid_state")
    assert_shift_open(report.shift_date, report.shift, user)
    validate_report(report)
    so_line = pr.so_line
    lot_fields = dict(artwork=pr.artwork, product=pr.component.product, component_type=pr.component.component_type,
                      flavour=(so_line.flavour if so_line else (pr.artwork.flavour if pr.artwork else "")), pr=pr,
                      produced_at=report.actual_end, shift_date=report.shift_date, shift=report.shift)
    ref = {"resource": "production.productionreport", "id": report.pk, "doc": report.doc_no}
    produced_before = ProductionReport.objects.filter(pr__plan_line=pr.plan_line, status__in=["submitted", "corrected"]).aggregate(t=Sum("good_qty"))["t"] or 0
    excess = max(produced_before + report.good_qty - pr.plan_line.planned_qty, 0)
    if report.good_qty:
        report.good_lot = inv.receipt_into_stock(item=pr.item, qty=report.good_qty, location=_location("fg"), user=user, source="production",
                                                 quality_state="pending", ref=ref, excess=excess > 0, **lot_fields)
    if report.held_qty:
        report.held_lot = inv.receipt_into_stock(item=pr.item, qty=report.held_qty, location=_location("qc_hold"), user=user, source="production",
                                                 quality_state="held", ref=ref, **lot_fields)
    if report.rejected_qty:
        report.rejected_lot = inv.receipt_into_stock(item=pr.item, qty=report.rejected_qty, location=_location("scrap"), user=user, source="production",
                                                     quality_state="rejected", ref=ref, **lot_fields)
    for row in consumption or []:
        from apps.masters.models import Item
        item = Item.objects.get(pk=row["item"])
        inv.consume_for_pr(pr=pr, item=item, qty=row["qty"], user=user, reason=f"Consumption {report.doc_no}")
    report.status = "submitted"
    report.row_version += 1
    report.save()
    if pr.status == "released":
        pr.status = "in_progress"
        pr.save(update_fields=["status", "updated_at"])
    line = pr.plan_line
    if line.status in {"planned", "released"}:
        line.status = "running"
        line.save(update_fields=["status", "updated_at"])
    mould = pr.mould
    type(mould).objects.filter(pk=mould.pk).update(shot_count=mould.shot_count + int(report.total_moulded / max(report.active_cavities, 1)))
    audit.record("production_reported", "production.productionreport", report, changes={
        "good": report.good_qty, "held": report.held_qty, "rejected": report.rejected_qty, "actual_cycle_s": str(report.actual_cycle_s)})
    _actual_cycle_reforecast(report, user)
    if excess:
        notify_perm("dashboard.owner.view", f"Excess production {excess} pcs on {pr.doc_no}", severity="warning",
                    resource="production.productionreport", object_id=report.pk,
                    next_action="Classify: allocate to order, keep approved stock, rework or scrap", dedupe_key=f"excess:{report.pk}")
    if report.held_qty:
        notify_perm("quality.release", f"{report.held_qty} pcs held on {pr.doc_no}", severity="warning", resource="production.productionreport",
                    object_id=report.pk, next_action="Inspect and disposition held lot", dedupe_key=f"held:{report.pk}")
    if report.good_qty:
        notify_perm("quality.release", f"{report.good_qty} pcs awaiting QC release ({pr.doc_no})", resource="inventory.lot",
                    object_id=report.good_lot.pk, next_action="Final inspection and release", dedupe_key=f"pending-qc:{report.good_lot.pk}")
    if produced_before + report.good_qty >= line.planned_qty and line.status == "running":
        line.status = "done"
        line.save(update_fields=["status", "updated_at"])
    return report


def _actual_cycle_reforecast(report, user):
    """Actual cycle change updates the remaining forecast; baseline and actuals are preserved (p.3, p.9)."""
    from apps.planning.services import reforecast_machine, revise_line

    line = report.pr.plan_line
    if report.actual_cycle_s and report.actual_cycle_s != line.cycle_s and line.status not in {"done", "cancelled"}:
        revise_line(line, {"cycle_s": report.actual_cycle_s}, f"Actual cycle {report.actual_cycle_s}s reported on {report.doc_no}", user, kind="cycle")
    else:
        reforecast_machine(line.machine)


def close_shift(shift_date, shift, user, site=None):
    lock, created = ShiftLock.objects.get_or_create(site=site, shift_date=shift_date, shift=shift, defaults={"closed_by": user})
    if not created:
        raise BusinessRuleError("Shift already closed.", "invalid_state")
    open_drafts = ProductionReport.objects.filter(shift_date=shift_date, shift=shift, status="draft").count()
    open_downtime = Downtime.objects.filter(shift_date=shift_date, shift=shift, end_at__isnull=True).count()
    audit.record("shift_closed", "production.shift", object_id=f"{shift_date}:{shift}", changes={"draft_reports": open_drafts, "open_downtime": open_downtime})
    return {"closed": True, "shift_date": str(shift_date), "shift": shift, "warnings": {"draft_reports": open_drafts, "open_downtime": open_downtime}}


def admin_correct(report, changes, reason, user):
    """Closed-record correction: before/after kept, stock corrected by controlled postings (p.10)."""
    if not has_perm(user, "production.correct_closed"):
        raise BusinessRuleError("Admin correction authority required.", "not_authorised")
    allowed = {"good_qty", "held_qty", "rejected_qty", "total_moulded", "actual_cycle_s", "defect_reasons", "notes", "operator_name"}
    if set(changes) - allowed:
        raise BusinessRuleError("Field not correctable.", "validation")
    before = audit.snapshot(report)
    deltas = {}
    for f in ("good_qty", "held_qty", "rejected_qty"):
        if f in changes:
            deltas[f] = int(changes[f]) - getattr(report, f)
    for k, v in changes.items():
        setattr(report, k, v)
    if not calc.reconcile_output(report.total_moulded, report.good_qty, report.held_qty, report.rejected_qty):
        raise BusinessRuleError("Corrected quantities do not reconcile.", "output_not_reconciled")
    adj = inv.virtual_location("adjustment")
    for field, lot_attr, loc_kind in (("good_qty", "good_lot", "fg"), ("held_qty", "held_lot", "qc_hold"), ("rejected_qty", "rejected_lot", "scrap")):
        d = deltas.get(field, 0)
        if not d:
            continue
        lot = getattr(report, lot_attr)
        loc = _location(loc_kind)
        if lot is None:
            raise BusinessRuleError(f"No {field} lot to correct; post a new report instead.", "invalid_state")
        if d > 0:
            inv.post_move("adjustment", item=lot.item, lot=lot, qty=d, from_location=adj, to_location=loc, user=user, reason=f"Correction {report.doc_no}: {reason}")
        else:
            inv.post_move("adjustment", item=lot.item, lot=lot, qty=-d, from_location=loc, to_location=adj, user=user, reason=f"Correction {report.doc_no}: {reason}")
    report.status = "corrected"
    report.row_version += 1
    report.save()
    audit.record("production_corrected", "production.productionreport", report, changes=audit.diff(before, audit.snapshot(report)), reason=reason)
    return report
