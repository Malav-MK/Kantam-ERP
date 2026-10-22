"""
Dashboard computations. Every figure derives from posted source records and
carries the records/basis behind it so a click can reveal them (p.18, p.20).
"""
import datetime
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.core import calc
from apps.core.rbac import has_perm
from apps.core.shifts import ShiftSlot, shift_for


def period_bounds(period):
    now = timezone.localtime()
    slot = shift_for()
    if period == "shift":
        return slot.start, slot.end, slot.label
    if period == "week":
        start = (now - datetime.timedelta(days=now.weekday())).replace(hour=9, minute=0, second=0, microsecond=0)
        return start, now, "This week"
    if period == "month":
        start = now.replace(day=1, hour=9, minute=0, second=0, microsecond=0)
        return start, now, now.strftime("%B %Y")
    start = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if now.hour < 9:
        start -= datetime.timedelta(days=1)
    return start, now, "Today (from 09:00)"


def machine_board(include_commercial=False):
    from apps.masters.models import Machine
    from apps.planning.models import ProductionRequest
    from apps.planning.services import next_free_time
    from apps.production.models import Downtime, ProductionReport
    from apps.tasks.models import Breakdown

    slot = shift_for()
    now = timezone.now()
    out = []
    for m in Machine.objects.filter(active=True):
        pr = ProductionRequest.objects.filter(machine=m, shift_date=slot.shift_date, shift=slot.shift, status__in=["released", "in_progress"]).select_related(
            "component__product", "artwork", "so_line__so__customer").order_by("segment_start").first()
        open_dt = Downtime.objects.filter(machine=m, end_at__isnull=True).order_by("-start_at").first()
        open_bd = Breakdown.objects.filter(machine=m).exclude(status__in=["restart_approved", "closed"]).first()
        reports = ProductionReport.objects.filter(machine=m, shift_date=slot.shift_date, shift=slot.shift, status__in=["submitted", "corrected"])
        good = reports.aggregate(t=Sum("good_qty"))["t"] or 0
        total = reports.aggregate(t=Sum("total_moulded"))["t"] or 0
        rejected = reports.aggregate(t=Sum("rejected_qty"))["t"] or 0
        last_activity = reports.order_by("-actual_end").values_list("actual_end", flat=True).first()
        if open_bd or m.status == "breakdown":
            status, reason = "breakdown", (open_bd.symptoms[:80] if open_bd else "Machine marked breakdown")
        elif m.status == "maintenance":
            status, reason = "maintenance", "Maintenance block"
        elif open_dt and open_dt.kind == "quality":
            status, reason = "quality_hold", open_dt.notes or "Quality hold"
        elif open_dt and open_dt.kind == "setup":
            status, reason = "setup", open_dt.notes or "Setup / changeover"
        elif open_dt:
            status, reason = "stopped", f"{open_dt.get_kind_display()}: {open_dt.reason or open_dt.notes}"
        elif pr is None:
            status, reason = "planned_idle", "No released PR this shift"
        elif not last_activity and now - slot.start > datetime.timedelta(hours=2):
            # Do not infer Running from an old record (p.26).
            status, reason = "no_report", "Released PR but no production reported in this shift yet"
        else:
            status, reason = "running", ""
        row = {
            "machine": m.code, "machine_id": str(m.pk), "name": m.name, "status": status, "reason": reason,
            "pr": pr.doc_no if pr else None, "pr_id": str(pr.pk) if pr else None,
            "product": pr.component.product.name if pr else None, "component": pr.component.get_component_type_display() if pr else None,
            "planned_pcs": pr.planned_pcs if pr else 0, "good": good, "total_moulded": total,
            "reject_pct": str(calc.reject_pct(rejected, total)), "cycle_s": str(pr.cycle_s) if pr else None,
            "expected_finish": pr.segment_end if pr else None, "next_free": next_free_time(m),
            "last_report": last_activity,
        }
        if include_commercial and pr and pr.so_line_id:
            row["customer"] = pr.so_line.so.customer.name
            row["flavour"] = pr.so_line.flavour
        out.append(row)
    return {"shift": slot.label, "generated_at": now, "machines": out}


def tv_projection():
    """Sanitised: no customer names, contacts, prices, people, payroll or evidence (p.26)."""
    board = machine_board(include_commercial=False)
    keep = {"machine", "status", "product", "component", "planned_pcs", "good", "reject_pct", "expected_finish"}
    return {"shift": board["shift"], "generated_at": board["generated_at"],
            "machines": [{k: v for k, v in m.items() if k in keep} for m in board["machines"]]}


def owner_overview(request, period="today"):
    from apps.fg.models import Dispatch
    from apps.inventory.models import Lot, StockBalance
    from apps.planning.models import PlanLine
    from apps.production.models import Downtime, ProductionReport
    from apps.purchase.services import overdue_po_lines, shortage_report
    from apps.sales.models import SalesOrder, SOLine
    from apps.tasks.models import Task

    start, end, label = period_bounds(period)
    now = timezone.now()
    reports = ProductionReport.objects.filter(status__in=["submitted", "corrected"], actual_end__gte=start, actual_end__lte=end)
    agg = reports.aggregate(good=Sum("good_qty"), total=Sum("total_moulded"), rejected=Sum("rejected_qty"), held=Sum("held_qty"))
    good, total, rejected = agg["good"] or 0, agg["total"] or 0, agg["rejected"] or 0
    planned = sum(r.pr.planned_pcs for r in reports.select_related("pr")) if reports.exists() else 0
    at_risk = SOLine.objects.filter(status="open", at_risk=True).select_related("so__customer", "product")
    show_customer = has_perm(request.user, "masters.customer.view")
    board = machine_board()
    running = [m for m in board["machines"] if m["status"] == "running"]
    stopped = [m for m in board["machines"] if m["status"] in {"breakdown", "stopped", "quality_hold", "no_report"}]
    shortages = shortage_report()
    held_lots = Lot.objects.filter(quality_state__in=["held", "pending"]).select_related("item")
    held_rows = []
    for lot in held_lots[:200]:
        q = StockBalance.objects.filter(lot=lot, qty__gt=0).aggregate(t=Sum("qty"))["t"]
        if q:
            held_rows.append({"lot": lot.doc_no, "id": str(lot.pk), "item": lot.item.code, "state": lot.quality_state, "qty": str(q)})
    ready = Dispatch.objects.filter(status__in=["verified", "approved"]).select_related("so", "customer")
    overdue_tasks = Task.objects.filter(due_at__lt=now).exclude(state__in=["submitted", "verified", "cancelled"]).select_related("assignee")
    downtime = (Downtime.objects.filter(start_at__gte=start, start_at__lte=end, concurrent_with__isnull=True)
                .values("kind").annotate(n=Count("id")))
    downtime_minutes = {}
    for d in Downtime.objects.filter(start_at__gte=start, start_at__lte=end, concurrent_with__isnull=True):
        mins = d.minutes if d.minutes is not None else round((now - d.start_at).total_seconds() / 60)
        downtime_minutes[d.get_kind_display()] = downtime_minutes.get(d.get_kind_display(), 0) + mins

    cards = [
        {"key": "orders_at_risk", "label": "Orders at risk", "value": at_risk.count(), "tone": "danger" if at_risk.exists() else "ok",
         "basis": "Open SO lines whose forecast finish is after the committed date.",
         "records": [{"resource": "sales.soline", "id": str(l.pk), "label": str(l), "detail": l.risk_note,
                      "customer": l.so.customer.name if show_customer else None} for l in at_risk[:50]]},
        {"key": "machines", "label": "Machines running / stopped", "value": f"{len(running)} / {len(stopped)}",
         "tone": "warn" if stopped else "ok", "basis": "Derived from current-shift PRs, open downtime and breakdowns; never inferred from old records.",
         "records": [{"resource": "masters.machine", "id": m["machine_id"], "label": m["machine"], "detail": f"{m['status']}: {m['reason']}"} for m in stopped]},
        {"key": "output", "label": "Planned vs good output", "value": f"{good:,} / {planned:,}",
         "tone": "ok" if not planned or good >= planned * 0.9 else "warn",
         "basis": "Good (accepted) pieces vs planned moulded pieces of PRs reported in the period - like with like.",
         "records": [{"resource": "production.productionreport", "id": str(r.pk), "label": r.doc_no, "detail": f"{r.machine.code}: good {r.good_qty} of {r.pr.planned_pcs}"} for r in reports.select_related("machine", "pr")[:50]]},
        {"key": "rejection", "label": "Rejection %", "value": f"{calc.reject_pct(rejected, total)}%", "tone": "warn" if total and rejected / total > 0.03 else "ok",
         "basis": "Rejected / total moulded x 100 for the period.", "records": [{"label": k, "detail": f"{v} min lost"} for k, v in downtime_minutes.items()]},
        {"key": "shortages", "label": "Material shortages", "value": len(shortages), "tone": "danger" if shortages else "ok",
         "basis": "Time-phased: unreserved demand + safety - free stock - PO arrivals by need date.",
         "records": [{"resource": "masters.item", "id": s["item"], "label": s["item_code"], "detail": f"short {s['shortage']} {s['uom']} by {s['need_date']}"} for s in shortages[:50]]},
        {"key": "quality_holds", "label": "Quality holds / awaiting QC", "value": len(held_rows), "tone": "warn" if held_rows else "ok",
         "basis": "Lots in held or pending state with stock on hand; excluded from usable stock.",
         "records": [{"resource": "inventory.lot", "id": h["id"], "label": h["lot"], "detail": f"{h['item']} {h['state']} {h['qty']}"} for h in held_rows[:50]]},
        {"key": "ready_dispatch", "label": "Ready dispatches", "value": ready.count(), "tone": "ok",
         "basis": "Dispatches verified or loading-approved, awaiting posting.",
         "records": [{"resource": "fg.dispatch", "id": str(d.pk), "label": d.doc_no, "detail": f"{d.so.doc_no} {d.vehicle_no}"} for d in ready[:50]]},
        {"key": "overdue_actions", "label": "Overdue actions", "value": overdue_tasks.count() + overdue_po_lines().count(),
         "tone": "danger" if overdue_tasks.exists() else "ok", "basis": "Tasks past due (not submitted/verified) + PO lines past promised date.",
         "records": [{"resource": "tasks.task", "id": str(t.pk), "label": t.doc_no, "detail": f"{t.title} - {t.assignee.full_name}"} for t in overdue_tasks[:30]]
         + [{"resource": "purchase.poline", "id": str(l.pk), "label": str(l), "detail": f"promised {l.promised_date}"} for l in overdue_po_lines()[:20]]},
    ]
    financial = None
    if has_perm(request.user, "finance.dashboard.view") or has_perm(request.user, "commercial.price.view") and has_perm(request.user, "dashboard.owner.view"):
        open_lines = SOLine.objects.filter(status="open")
        open_value = sum((l.balance_qty * l.unit_price for l in open_lines), Decimal("0"))
        from apps.purchase.models import POLine
        commitments = sum((l.balance_qty * l.rate for l in POLine.objects.filter(status__in=["open", "partial"], po__status__in=["approved", "sent", "partial"])), Decimal("0"))
        financial = [
            {"key": "open_order_value", "label": "Open order value", "value": f"₹{open_value:,.0f}", "basis": "Open SO line balance x unit price."},
            {"key": "procurement_commitments", "label": "Procurement commitments", "value": f"₹{commitments:,.0f}", "basis": "Approved PO balance x rate (committed, not invoiced)."},
            {"key": "receivables", "label": "Receivables", "value": "Not connected", "basis": "Accounting system of record not yet integrated (brief p.64)."},
            {"key": "margin", "label": "Margin", "value": "Pending valuation policy", "basis": "Valuation, regrind and overhead rules must be agreed before actual profit is shown."},
        ]
    return {"period": label, "generated_at": now, "source": "manual postings (no machine telemetry connected)",
            "cards": cards, "financial": financial,
            "pending_approvals": _pending_approvals(request)}


def _pending_approvals(request):
    from apps.core.models import ApprovalRequest
    from apps.core.rbac import effective_permissions
    perms = effective_permissions(request.user)
    return ApprovalRequest.objects.filter(status="pending", required_permission__in=list(perms)).exclude(created_by=request.user).count()


def gate_overview(request):
    from apps.gate.models import Courier, GateEvent, GateMaterial, GatePass, Person, VehicleTrip, Visit

    now = timezone.now()
    show_personal = has_perm(request.user, "hr.personal.view")
    inside_people = []
    for p in Person.objects.filter(active=True).exclude(kind="visitor"):
        last = GateEvent.objects.filter(person=p).order_by("-at").first()
        if last and last.kind in {"entry", "break_in", "duty_in"}:
            inside_people.append({"person": p.name, "code": p.person_code, "kind": p.get_kind_display(), "employer": p.employer,
                                  "since": last.at, "phone": p.phone if show_personal else None})
    visitors_inside = Visit.objects.filter(status="inside")
    overdue_visitors = [v for v in visitors_inside if v.entry_at and now > v.entry_at + datetime.timedelta(minutes=v.expected_minutes + 30)]
    return {
        "generated_at": now,
        "freshness_note": "Last confirmed gate events - supports emergency procedures, not a guaranteed physical headcount.",
        "counts": {
            "people_inside": len(inside_people),
            "visitors_inside": visitors_inside.count(),
            "vehicles_inside": VehicleTrip.objects.filter(status="inside").count(),
            "awaiting_host": Visit.objects.filter(status="awaiting_approval").count(),
            "arrivals_pending_receipt": GateMaterial.objects.filter(direction="in").exclude(status__in=["closed", "cancelled"]).count(),
            "gate_outs_pending": GateMaterial.objects.filter(direction="out", status__in=["arrived", "authorised", "discrepancy"]).count(),
            "parcels_awaiting_receiver": Courier.objects.filter(direction="in", status="received").count(),
            "overdue_visitors": len(overdue_visitors),
            "unreturned_badges": Visit.objects.filter(status="exited", badge_returned=False).exclude(badge_no="").count(),
            "returnable_overdue": GatePass.objects.filter(returnable=True, status__in=["out", "partial"], due_back__lt=timezone.localdate()).count(),
        },
        "muster": {
            "people": inside_people,
            "visitors": [{"visitor": v.visitor_name, "organisation": v.organisation, "host": v.host.full_name, "since": v.entry_at, "badge": v.badge_no} for v in visitors_inside],
            "vehicles": list(VehicleTrip.objects.filter(status="inside").values("vehicle_no", "driver_name", "in_at")),
        },
        "overdue_visitors": [{"id": str(v.pk), "visitor": v.visitor_name, "host": v.host.full_name, "entry_at": v.entry_at} for v in overdue_visitors],
    }


def stores_daily(shift_date=None, shift=None):
    from apps.planning.models import ProductionRequest

    slot = shift_for()
    shift_date = shift_date or slot.shift_date
    shift = shift or slot.shift
    rows = []
    for pr in ProductionRequest.objects.filter(shift_date=shift_date, shift=shift).exclude(status="draft").select_related(
            "machine", "component__product", "so_line__so__customer", "artwork").order_by("machine__sequence"):
        mats = []
        pending_action = []
        for m in pr.materials.select_related("item"):
            mats.append({"item": m.item.code, "kind": m.kind, "required": str(m.required_qty), "issued": str(m.issued_qty), "returned": str(m.returned_qty),
                         "pending": str(m.pending_issue), "machine_side": str(m.machine_side_balance), "uom": m.uom})
            if m.pending_issue > 0 and pr.status in {"released", "in_progress"}:
                pending_action.append(f"Issue {m.item.code}")
            if pr.status in {"cancelled", "closed"} and m.machine_side_balance > 0:
                pending_action.append(f"Return {m.item.code}")
        rows.append({
            "pr": pr.doc_no, "pr_id": str(pr.pk), "status": pr.status, "machine": pr.machine.code, "shift_date": pr.shift_date, "shift": pr.shift,
            "customer": pr.so_line.so.customer.name if pr.so_line_id else "Stock job", "product": pr.component.product.name,
            "component": pr.component.get_component_type_display(), "flavour": pr.so_line.flavour if pr.so_line_id else "",
            "artwork": str(pr.artwork) if pr.artwork_id else "", "materials": mats, "pending_action": pending_action, "handover": pr.handover_note,
        })
    return {"shift": ShiftSlot(shift_date, shift).label, "rows": rows}


def planning_board(date_from=None, days=3):
    from apps.masters.models import Machine
    from apps.planning.models import PlanLine, ProductionRequest
    from apps.planning.services import next_free_time

    date_from = date_from or shift_for().shift_date
    date_to = date_from + datetime.timedelta(days=days)
    out = []
    for m in Machine.objects.filter(active=True):
        prs = ProductionRequest.objects.filter(machine=m, shift_date__gte=date_from, shift_date__lt=date_to).exclude(status="cancelled").select_related("component__product")
        lines = PlanLine.objects.filter(machine=m, status__in=["planned", "released", "running", "stopped"]).select_related("component__product")
        out.append({
            "machine": m.code, "machine_id": str(m.pk), "status": m.status, "next_free": next_free_time(m),
            "segments": [{"pr": p.doc_no, "id": str(p.pk), "date": p.shift_date, "shift": p.shift, "start": p.segment_start, "end": p.segment_end,
                          "product": p.component.product.code, "component": p.component.component_type, "planned_pcs": p.planned_pcs, "status": p.status} for p in prs],
            "eligible_plans": [{"id": str(l.pk), "label": f"#{l.sequence} {l.component.product.code} {l.component.component_type}", "planned_qty": l.planned_qty,
                                "cycle_s": str(l.cycle_s), "cavities": l.active_cavities, "forecast_finish": l.forecast_finish, "at_risk": l.at_risk,
                                "status": l.status} for l in lines],
        })
    return {"from": date_from, "to": date_to, "machines": out}


def task_analytics(request):
    from apps.tasks.models import Task
    from apps.tasks.resources import TaskResource

    qs = TaskResource().get_queryset(request)
    now = timezone.now()
    by_state = dict(qs.values_list("state").annotate(n=Count("id")))
    overdue = qs.filter(due_at__lt=now).exclude(state__in=["submitted", "verified", "cancelled"]).count()
    blocked = list(qs.filter(state="blocked").values("doc_no", "title", "blocker_reason", "assignee__full_name"))
    verified = qs.filter(state="verified")
    on_time = verified.filter(verified_at__lte=models_F("due_at")).count() if verified.exists() else 0
    return {"by_state": by_state, "overdue": overdue, "blocked": blocked,
            "verified_on_time_pct": round(on_time / verified.count() * 100, 1) if verified.exists() else None,
            "submitted_unverified": by_state.get("submitted", 0),
            "note": "On-time is measured on verified completion against the due date, not on submission."}


def models_F(name):
    from django.db.models import F
    return F(name)
