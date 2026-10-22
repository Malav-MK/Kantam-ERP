"""
Planning services (brief p.9-11, p.18, p.30).

* Forecasts derive from the central calc dictionary.
* Every revision keeps the baseline and records reason, planner and impact.
* PR release snapshots recipe/artwork, reserves materials and cannot bypass
  readiness controls.
"""
import datetime
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from apps.core import audit, calc
from apps.core.api import BusinessRuleError
from apps.core.services import notify, notify_perm
from apps.core.shifts import ShiftSlot, is_shift_closed
from apps.masters.models import Holiday, Recipe

from .models import MonthlyPlan, PlanLine, PlanRevision, PRMaterial, ProductionRequest

ACTIVE_LINE_STATES = ["planned", "released", "running", "stopped"]


# ---------------------------------------------------------------------------
# Metrics & forecast
# ---------------------------------------------------------------------------
def good_to_date(line):
    from apps.production.models import ProductionReport

    return ProductionReport.objects.filter(pr__plan_line=line, status__in=["submitted", "corrected"]).aggregate(t=Sum("good_qty"))["t"] or 0


def line_metrics(line):
    good = good_to_date(line)
    remaining_good = max(line.planned_qty - good, 0)
    gross_remaining = calc.gross_target(remaining_good, line.expected_yield_pct)
    rate = calc.theoretical_rate(line.active_cavities, line.cycle_s)
    hours = calc.run_hours(gross_remaining, line.active_cavities, line.cycle_s)
    baseline = None
    if line.baseline_cycle_s:
        baseline = {
            "qty": line.baseline_qty, "cycle_s": str(line.baseline_cycle_s), "finish": line.baseline_finish,
            "rate_pcs_hr": str(calc.theoretical_rate(line.active_cavities, line.baseline_cycle_s).quantize(Decimal("0.1"))),
        }
    return {
        "good_to_date": good, "remaining_good": remaining_good, "gross_remaining": gross_remaining,
        "rate_pcs_per_hour": str(rate.quantize(Decimal("0.1"))), "remaining_run_hours": str(hours),
        "variance_vs_target": good - line.planned_qty,
        "basis": {
            "formula": "run hours = gross pcs x cycle / active cavities / 3600; gross = ceil(good / yield)",
            "active_cavities": line.active_cavities, "cycle_s": str(line.cycle_s), "yield_pct": str(line.expected_yield_pct),
        },
        "baseline": baseline,
        "forecast_start": line.forecast_start, "forecast_finish": line.forecast_finish,
    }


def _skip_holidays(start, hours):
    """Add run hours, extending over plant holidays (24h each)."""
    end = start + datetime.timedelta(hours=float(hours))
    holidays = set(Holiday.objects.filter(date__gte=start.date(), date__lte=(end + datetime.timedelta(days=30)).date()).values_list("date", flat=True))
    day = start.date()
    while day <= end.date():
        if day in holidays:
            end += datetime.timedelta(hours=24)
        day += datetime.timedelta(days=1)
    return end


def reforecast_machine(machine, notify_changes=True):
    """Sequence active lines on a machine and recompute start/finish and delivery risk."""
    now = timezone.now()
    cursor = now
    changed = []
    for line in PlanLine.objects.filter(machine=machine, status__in=ACTIVE_LINE_STATES).select_related("demand__so_line__so").order_by("sequence", "created_at"):
        start = max(cursor, line.earliest_start) if line.earliest_start else cursor
        if line.status == "planned":
            start += datetime.timedelta(minutes=line.setup_minutes)
        m = line_metrics(line)
        finish = _skip_holidays(start, m["remaining_run_hours"])
        old_finish = line.forecast_finish
        at_risk = False
        so_line = line.demand.so_line if line.demand_id else None
        if so_line and so_line.committed_date and timezone.localtime(finish).date() > so_line.committed_date:
            at_risk = True
        PlanLine.objects.filter(pk=line.pk).update(forecast_start=start, forecast_finish=finish, at_risk=at_risk)
        if so_line is not None:
            note = f"Forecast finish {timezone.localtime(finish):%d-%b %H:%M} vs committed {so_line.committed_date}" if at_risk else ""
            type(so_line).objects.filter(pk=so_line.pk).update(at_risk=at_risk, risk_note=note)
            if at_risk and notify_changes:
                # Do not silently move a customer commitment - tell its owner (p.9).
                notify([so_line.so.salesperson], f"Delivery risk: {so_line}", body=note, severity="warning", resource="sales.soline",
                       object_id=so_line.pk, next_action="Reconfirm date with customer or expedite",
                       dedupe_key=f"risk:{so_line.pk}:{timezone.localtime(finish):%Y%m%d}")
        if old_finish != finish:
            changed.append({"line": str(line.pk), "old_finish": old_finish, "new_finish": finish})
        cursor = finish
    return changed


def next_free_time(machine):
    last = PlanLine.objects.filter(machine=machine, status__in=ACTIVE_LINE_STATES).exclude(forecast_finish__isnull=True).order_by("-forecast_finish").first()
    return max(last.forecast_finish, timezone.now()) if last else timezone.now()


# ---------------------------------------------------------------------------
# Compatibility & readiness (p.9, p.29-30)
# ---------------------------------------------------------------------------
def compatibility_issues(machine, mould, component):
    issues = []
    if mould.product_component_id != component.pk:
        issues.append(f"Mould {mould.code} does not produce {component}.")
    if mould.compatible_machines.exists() and not mould.compatible_machines.filter(pk=machine.pk).exists():
        issues.append(f"Mould {mould.code} is not approved for machine {machine.code}.")
    if mould.min_tonnage and machine.tonnage and machine.tonnage < mould.min_tonnage:
        issues.append(f"Machine tonnage {machine.tonnage} below mould requirement {mould.min_tonnage}.")
    if mould.requires_robot and component.iml and not machine.has_iml_robot:
        issues.append(f"Machine {machine.code} has no IML robot.")
    if mould.status in {"in_repair", "out", "inactive"}:
        issues.append(f"Mould {mould.code} is {mould.get_status_display()}.")
    if machine.status in {"breakdown", "inactive", "maintenance"} or not machine.active:
        issues.append(f"Machine {machine.code} is {machine.get_status_display()}.")
    if mould.active_cavities < 1:
        issues.append("No active cavities.")
    return issues


def active_recipe(component, on_date=None):
    on_date = on_date or timezone.localdate()
    return Recipe.objects.filter(product_component=component, approved=True, effective_from__lte=on_date).order_by("-effective_from", "-revision").first()


def line_readiness(line):
    blockers = compatibility_issues(line.machine, line.mould, line.component)
    if line.component.iml:
        if not line.artwork_id:
            blockers.append("IML artwork not selected.")
        elif line.artwork.status != "approved":
            blockers.append(f"Artwork {line.artwork} not approved.")
    if not active_recipe(line.component):
        blockers.append("No approved recipe for component.")
    if line.demand_id:
        so = line.demand.so_line.so
        if so.status != "confirmed":
            blockers.append("Sales order not confirmed.")
    elif not (line.stock_production and line.stock_approval_ref):
        blockers.append("Stock production needs an approval reference.")
    return {"ready": not blockers, "blockers": blockers}


# ---------------------------------------------------------------------------
# Plan approval and revisions
# ---------------------------------------------------------------------------
def approve_plan(plan, user):
    reforecast_all(plan)
    lines = list(plan.lines.exclude(status="cancelled"))
    snap = []
    for line in lines:
        line.refresh_from_db()
        if line.baseline_qty is None:
            line.baseline_qty = line.planned_qty
            line.baseline_cycle_s = line.cycle_s
            line.baseline_finish = line.forecast_finish
            line.save(update_fields=["baseline_qty", "baseline_cycle_s", "baseline_finish", "updated_at"])
        snap.append({"line": str(line.pk), "machine": line.machine.code, "component": str(line.component), "qty": line.baseline_qty,
                     "cycle_s": str(line.baseline_cycle_s), "finish": audit._jsonable(line.baseline_finish)})
    if plan.status == "draft":
        plan.baseline = {"approved_at": timezone.now().isoformat(), "lines": snap}
        plan.baseline_at = timezone.now()
    plan.status = "approved"
    plan.approved_by = user
    plan.row_version += 1
    plan.save()
    audit.record("plan_approved", "planning.monthlyplan", plan, changes={"lines": len(snap)})
    return plan


def reforecast_all(plan):
    for machine_id in plan.lines.values_list("machine", flat=True).distinct():
        from apps.masters.models import Machine
        reforecast_machine(Machine.objects.get(pk=machine_id))


def revise_line(line, changes, reason, user, kind="other"):
    """Versioned revision: baseline & actuals preserved, impact shown, owners notified (p.9, p.18)."""
    if not reason or len(reason.strip()) < 5:
        raise BusinessRuleError("A change reason is mandatory for plan revisions.", "reason_required")
    allowed = {"planned_qty", "cycle_s", "active_cavities", "expected_yield_pct", "machine", "mould", "sequence", "status", "earliest_start", "setup_minutes"}
    bad = set(changes) - allowed
    if bad:
        raise BusinessRuleError(f"Not revisable here: {', '.join(bad)}", "validation")
    before_forecast = {str(l.pk): l.forecast_finish for l in PlanLine.objects.filter(machine=line.machine, status__in=ACTIVE_LINE_STATES)}
    before = {k: audit._jsonable(getattr(line, k + "_id" if k in {"machine", "mould"} else k)) for k in changes}
    for k, v in changes.items():
        setattr(line, k, v)
    if "active_cavities" in changes and line.active_cavities > line.mould.cavities:
        raise BusinessRuleError("Active cavities exceed mould cavities.", "invalid_cavities")
    if {"machine", "mould"} & set(changes):
        issues = compatibility_issues(line.machine, line.mould, line.component)
        if issues:
            raise BusinessRuleError("; ".join(issues), "incompatible_resource")
    line.revision += 1
    line.row_version += 1
    line.save()
    affected_machines = {line.machine}
    changed = []
    for m in affected_machines:
        changed += reforecast_machine(m)
    after_forecast = {str(l.pk): l.forecast_finish for l in PlanLine.objects.filter(machine=line.machine, status__in=ACTIVE_LINE_STATES)}
    impact = {
        "affected_lines": [k for k in after_forecast if before_forecast.get(k) != after_forecast.get(k)],
        "metrics_after": line_metrics(line),
    }
    rev = PlanRevision.objects.create(plan_line=line, revision=line.revision, kind=kind, changes={"before": before, "after": audit._jsonable(changes)},
                                      reason=reason, impact=audit._jsonable(impact), created_by=user)
    audit.record("plan_revised", "planning.planline", line, changes={k: [before[k], audit._jsonable(v)] for k, v in changes.items()}, reason=reason)
    for perm in ("inventory.issue.create", "purchase.requisition.create", "sales.soline.view"):
        notify_perm(perm, f"Plan revised: {line}", body=f"{kind}: {reason}", severity="info", resource="planning.planline",
                    object_id=line.pk, next_action="Review material balance / commitments", dedupe_key=f"plan-rev:{line.pk}:{line.revision}:{perm}")
    return rev


# ---------------------------------------------------------------------------
# Shift PR (p.10)
# ---------------------------------------------------------------------------
def pr_materials(*, component, recipe, gross_pcs, artwork, so_line, packing_rule=None):
    """Material requirement for one PR with the calculation basis kept for display."""
    rows = []
    mass = calc.material_mass_kg(gross_pcs, component.weight_g, recipe.loss_allowance_pct if recipe else 0)
    if recipe:
        lines = list(recipe.lines.select_related("item"))
        split = calc.recipe_split(mass, [(l.item, l.percent) for l in lines], recipe.basis)
        for (item, kg), rl in zip(split, lines):
            # Masterbatch colour follows the SO; recipe % is kept, item is the SO's colour.
            if rl.is_masterbatch and so_line is not None and so_line.masterbatch_id:
                item = so_line.masterbatch
            rows.append({"item": item, "kind": item.kind, "qty": kg, "uom": item.stock_uom, "basis": {
                "gross_pcs": gross_pcs, "g_per_pc": str(component.weight_g), "allowance_pct": str(recipe.loss_allowance_pct),
                "blend_kg": str(mass), "percent": str(rl.percent), "recipe_basis": recipe.basis, "recipe": f"R{recipe.revision}"}})
    if component.iml and artwork is not None and artwork.label_item_id:
        lab = calc.label_requirement(gross_pcs, True, component.labels_per_piece, recipe.label_allowance_pct if recipe else 0)
        rows.append({"item": artwork.label_item, "kind": "label", "qty": Decimal(lab["total"]), "uom": artwork.label_item.stock_uom,
                     "artwork": artwork, "basis": {"gross_pcs": gross_pcs, "labels_per_piece": component.labels_per_piece, **lab}})
    if packing_rule:
        pack = calc.packing_requirement(gross_pcs, packing_rule.pcs_per_bag, packing_rule.pcs_per_carton)
        for item, qty, label in ((packing_rule.bag_item, pack["bags"], "bags"), (packing_rule.carton_item, pack["cartons"], "cartons")):
            if item:
                rows.append({"item": item, "kind": item.kind, "qty": Decimal(qty), "uom": item.stock_uom, "basis": {"pcs": gross_pcs, **pack}})
        if packing_rule.tape_item and packing_rule.cartons_per_tape_roll:
            rolls = calc.tape_rolls(pack["cartons"], packing_rule.cartons_per_tape_roll)
            rows.append({"item": packing_rule.tape_item, "kind": "tape", "qty": rolls, "uom": packing_rule.tape_item.stock_uom,
                         "basis": {"cartons": pack["cartons"], "cartons_per_roll": packing_rule.cartons_per_tape_roll}})
    return rows


def create_pr(*, line, shift_date, shift, user, start=None, end=None, planned_pcs=None, parent_pr=None):
    slot = ShiftSlot(shift_date, shift)
    start = start or slot.start
    end = end or slot.end
    if start < slot.start or end > slot.end or end <= start:
        raise BusinessRuleError(f"Segment must lie within the {slot.label} shift ({slot.start:%H:%M}-{slot.end:%H:%M}).", "invalid_segment")
    if is_shift_closed(shift_date, shift):
        raise BusinessRuleError("That shift is already closed.", "shift_closed")
    if line.status in {"done", "cancelled"}:
        raise BusinessRuleError("Plan line is not active.", "invalid_state")
    overlap = ProductionRequest.objects.filter(machine=line.machine, shift_date=shift_date, shift=shift).exclude(status="cancelled").filter(
        Q(segment_start__lt=end) & Q(segment_end__gt=start))
    if overlap.exists():
        raise BusinessRuleError(f"Overlaps {overlap.first().doc_no} on {line.machine.code}. Job segments cannot overlap.", "segment_overlap")
    hours = Decimal(str((end - start).total_seconds() / 3600))
    capacity = calc.planned_moulded_pieces(hours, line.active_cavities, line.cycle_s)
    m = line_metrics(line)
    planned_pcs = int(planned_pcs) if planned_pcs else min(capacity, m["gross_remaining"]) or capacity
    if planned_pcs > capacity:
        raise BusinessRuleError(f"Planned {planned_pcs} pcs exceeds segment capacity {capacity} pcs.", "over_capacity")
    recipe = active_recipe(line.component, shift_date)
    so_line = line.demand.so_line if line.demand_id else None
    pr = ProductionRequest.objects.create(
        plan_line=line, so_line=so_line, machine=line.machine, mould=line.mould, component=line.component, item=line.component.item,
        artwork=line.artwork, recipe=recipe, shift_date=shift_date, shift=shift, segment_start=start, segment_end=end,
        planned_pcs=planned_pcs, active_cavities=line.active_cavities, cycle_s=line.cycle_s, parent_pr=parent_pr,
        created_by=user, owner=user,
    )
    packing_rule = so_line.packing_rule if so_line and so_line.packing_rule_id else None
    for row in pr_materials(component=line.component, recipe=recipe, gross_pcs=planned_pcs, artwork=line.artwork, so_line=so_line, packing_rule=packing_rule):
        existing = PRMaterial.objects.filter(pr=pr, item=row["item"]).first()
        if existing:
            existing.required_qty += row["qty"]
            existing.save()
            continue
        PRMaterial.objects.create(pr=pr, item=row["item"], kind=row["kind"], artwork=row.get("artwork"), required_qty=row["qty"], uom=row["uom"],
                                  basis=audit._jsonable(row["basis"]), created_by=user)
    pr.readiness = line_readiness(line)
    pr.save(update_fields=["readiness"])
    audit.record("pr_created", "planning.productionrequest", pr, changes={"planned_pcs": planned_pcs, "capacity": capacity})
    return pr


def release_pr(pr, user):
    """Snapshot recipe/artwork, reserve materials and create shift issue requirements (p.18)."""
    from apps.inventory.services import balance_summary, reserve

    if pr.status != "draft":
        raise BusinessRuleError("Only draft PRs can be released.", "invalid_state")
    ready = line_readiness(pr.plan_line)
    shortages = []
    for mat in pr.materials.select_related("item"):
        bal = balance_summary(mat.item)
        if bal["available"] < mat.required_qty:
            shortages.append({"item": mat.item.code, "required": str(mat.required_qty), "available": str(bal["available"]), "incoming": str(bal["incoming"])})
    if shortages:
        ready["blockers"].append("Material shortage")
    if ready["blockers"]:
        pr.readiness = {**ready, "shortages": shortages}
        pr.save(update_fields=["readiness"])
        if shortages:
            notify_perm("purchase.requisition.create", f"Shortage blocks {pr.doc_no}", body=str(shortages), severity="critical",
                        resource="planning.productionrequest", object_id=pr.pk, next_action="Raise requisition / expedite supplier",
                        dedupe_key=f"pr-shortage:{pr.pk}")
        raise BusinessRuleError("PR cannot be released: " + "; ".join(ready["blockers"]), "release_blocked", {"blockers": ready["blockers"], "shortages": shortages})
    for mat in pr.materials.select_related("item"):
        reserve(item=mat.item, qty=mat.required_qty, user=user, pr=pr, note=f"Shift requirement {pr.doc_no}")
    recipe = pr.recipe
    pr.snapshot = {
        "recipe": {"id": str(recipe.pk), "revision": recipe.revision, "basis": recipe.basis,
                   "lines": [{"item": l.item.code, "percent": str(l.percent), "mb": l.is_masterbatch} for l in recipe.lines.all()]} if recipe else None,
        "artwork": {"id": str(pr.artwork.pk), "code": pr.artwork.code, "revision": pr.artwork.revision, "flavour": pr.artwork.flavour} if pr.artwork else None,
        "so_line": str(pr.so_line) if pr.so_line else None,
        "component_weight_g": str(pr.component.weight_g),
        "cavities": pr.active_cavities, "cycle_s": str(pr.cycle_s),
    }
    pr.status = "released"
    pr.released_by = user
    pr.released_at = timezone.now()
    pr.readiness = ready
    pr.row_version += 1
    pr.save()
    line = pr.plan_line
    if line.status == "planned":
        line.status = "released"
        line.save(update_fields=["status", "updated_at"])
    audit.record("pr_released", "planning.productionrequest", pr)
    notify_perm("inventory.issue.create", f"PR released {pr.doc_no} - {pr.machine.code} {pr.shift_date} {pr.shift}", resource="planning.productionrequest",
                object_id=pr.pk, next_action="Issue shift materials", dedupe_key=f"pr-released:{pr.pk}")
    return pr


def cancel_pr(pr, reason, user, reissue=False):
    """
    Cancelled/replaced PR: unused material returns against the SAME old PR; a new
    linked PR is generated for reissue. Actual production/consumption is retained.
    """
    from apps.inventory.services import release_reservations

    if pr.status in {"cancelled", "closed"}:
        raise BusinessRuleError(f"PR is already {pr.status}.", "invalid_state")
    release_reservations(pr=pr, reason=f"PR cancelled: {reason}")
    pr.status = "cancelled"
    pr.cancel_reason = reason
    pr.row_version += 1
    pr.save()
    machine_side = [{"item": m.item.code, "balance": str(m.machine_side_balance)} for m in pr.materials.all() if m.machine_side_balance > 0]
    audit.record("pr_cancelled", "planning.productionrequest", pr, changes={"machine_side_balance": machine_side}, reason=reason)
    if machine_side:
        notify_perm("inventory.return.create", f"Return material against cancelled {pr.doc_no}", body=str(machine_side), severity="warning",
                    resource="planning.productionrequest", object_id=pr.pk, next_action="Post return against the old PR",
                    dedupe_key=f"pr-cancel-return:{pr.pk}")
    new_pr = None
    if reissue:
        from apps.core.shifts import shift_for
        slot = shift_for()
        if is_shift_closed(slot.shift_date, slot.shift):
            slot = slot.next()
        from apps.production.models import ProductionReport
        produced = ProductionReport.objects.filter(pr=pr, status__in=["submitted", "corrected"]).aggregate(t=Sum("total_moulded"))["t"] or 0
        remaining = max(pr.planned_pcs - produced, 0)
        if remaining > 0:
            new_pr = create_pr(line=pr.plan_line, shift_date=slot.shift_date, shift=slot.shift, user=user, planned_pcs=None, parent_pr=pr)
            audit.record("pr_reissued", "planning.productionrequest", new_pr, changes={"parent": pr.doc_no, "remaining": remaining})
    return new_pr
