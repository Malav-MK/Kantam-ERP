import datetime

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.core import audit
from apps.core.api import BusinessRuleError
from apps.core.registry import Action, Resource, register
from apps.core.shifts import next_month_prep_open
from apps.masters.models import Machine, Mould

from . import services
from .models import MonthlyPlan, PlanLine, PlanRevision, PRMaterial, ProductionRequest


@register
class MonthlyPlanResource(Resource):
    key = "planning.monthlyplan"
    model = MonthlyPlan
    label = "Monthly plans"
    singular = "Monthly plan"
    module = "planning"
    list_fields = ["doc_no", "month", "status", "approved_by", "baseline_at", "revision"]
    create_only_fields = ["month"]
    form_fields = ["notes"]
    readonly_fields = ["baseline"]
    filter_fields = ["status", "month"]
    inlines = [{"resource": "planning.planline", "fk": "plan", "label": "Plan lines"}]
    help = {
        "why": "Earlier Sheet 03: pending SO lines, components, machine load, original approved plan, current revision and actuals.",
        "effect": "Approval freezes the baseline; later revisions never overwrite it or historical actuals.",
        "boundary": "Prepare next month around day 20 without closing the current month.",
    }

    def before_create(self, request, data):
        month = data["month"]
        try:
            y, m = map(int, month.split("-"))
            first = datetime.date(y, m, 1)
        except ValueError:
            raise BusinessRuleError("Month must be YYYY-MM.", "validation")
        today = timezone.localdate()
        current = today.replace(day=1)
        nxt = (current + datetime.timedelta(days=32)).replace(day=1)
        if first > nxt or (first == nxt and not next_month_prep_open(today)):
            raise BusinessRuleError("Next month's plan opens from day 20 of the current month.", "too_early")
        if MonthlyPlan.objects.filter(month=month).exists():
            raise BusinessRuleError("A plan for this month already exists.", "duplicate")

    def extra_detail(self, request, obj):
        machines = {}
        for line in obj.lines.exclude(status="cancelled").select_related("machine"):
            m = machines.setdefault(line.machine.code, {"machine": line.machine.code, "lines": 0, "remaining_hours": 0.0})
            m["lines"] += 1
            m["remaining_hours"] += float(services.line_metrics(line)["remaining_run_hours"])
        return {"machine_load": list(machines.values())}

    def _approve(request, obj, data):
        return services.approve_plan(obj, request.user)

    def _reforecast(request, obj, data):
        services.reforecast_all(obj)
        return obj

    actions = [
        Action("approve", "Approve plan (freeze baseline)", _approve, tone="primary"),
        Action("reforecast", "Recalculate forecast", _reforecast, permission="planning.planline.update"),
    ]


@register
class PlanLineResource(Resource):
    key = "planning.planline"
    server_defaults = ["active_cavities", "cycle_s", "setup_minutes"]
    model = PlanLine
    label = "Plan lines"
    singular = "Plan line"
    module = "planning"
    title_field = "component"
    ordering = ["machine", "sequence"]
    list_fields = ["plan", "machine", "sequence", "component", "artwork", "planned_qty", "cycle_s", "active_cavities", "forecast_finish",
                   "baseline_finish", "status", "at_risk", "revision"]
    create_only_fields = ["plan", "demand", "stock_production", "stock_approval_ref", "component"]
    form_fields = ["artwork", "machine", "mould", "sequence", "planned_qty", "expected_yield_pct", "cycle_s", "active_cavities", "setup_minutes",
                   "earliest_start", "notes"]
    readonly_fields = ["forecast_start", "baseline_qty", "baseline_cycle_s", "readiness"]
    filter_fields = ["plan", "machine", "status", "at_risk", "component"]
    depends = {
        "mould": {"product_component": "component"},
        "artwork": {"status": "=approved"},
        "demand": {"active": "=true"},
    }
    inlines = [{"resource": "planning.planrevision", "fk": "plan_line", "label": "Revision history"},
               {"resource": "planning.productionrequest", "fk": "plan_line", "label": "Shift PRs"}]
    field_help = {
        "cycle_s": "Changing the cycle reforecasts remaining run hours, material need and affected delivery commitments.",
        "planned_qty": "Good-output target; gross moulded = ceil(good / yield) - loss applied once.",
    }

    def before_create(self, request, data):
        comp = data["component"]
        dem = data.get("demand")
        if dem and dem.component_id != comp.pk:
            raise BusinessRuleError("Demand is for a different component.", "incompatible_demand")
        if not dem and not data.get("stock_production"):
            raise BusinessRuleError("Link an SO component demand or mark as approved stock production.", "validation")
        mould = data.get("mould")
        if mould is None:
            raise BusinessRuleError("Select a mould.", "validation")
        data.setdefault("active_cavities", mould.active_cavities)
        data.setdefault("cycle_s", mould.standard_cycle_s)
        data.setdefault("setup_minutes", mould.setup_minutes)
        if dem and not data.get("artwork"):
            data["artwork"] = dem.artwork
        if data.get("artwork") and data["artwork"].product_id != comp.product_id:
            raise BusinessRuleError("Artwork does not belong to this product.", "incompatible_artwork")
        issues = services.compatibility_issues(data["machine"], mould, comp)
        if issues:
            raise BusinessRuleError("; ".join(issues), "incompatible_resource")
        if data["active_cavities"] > mould.cavities:
            raise BusinessRuleError("Active cavities exceed mould cavities.", "invalid_cavities")

    def after_create(self, request, obj):
        services.reforecast_machine(obj.machine)
        obj.readiness = services.line_readiness(obj)
        obj.save(update_fields=["readiness"])

    def edit_block_reason(self, request, obj):
        if obj.status in {"done", "cancelled"}:
            return f"Line is {obj.status}."
        if obj.plan.status == "approved":
            return "Plan approved: use 'Revise' so the baseline and history are kept."
        return None

    def after_update(self, request, obj, changes):
        services.reforecast_machine(obj.machine)

    def extra_detail(self, request, obj):
        return {"metrics": services.line_metrics(obj), "readiness": services.line_readiness(obj),
                "machine_next_free": services.next_free_time(obj.machine)}

    def _revise(request, obj, data):
        changes = {}
        for f in ("planned_qty", "active_cavities", "sequence", "setup_minutes"):
            if data.get(f) not in (None, ""):
                changes[f] = int(data[f])
        for f in ("cycle_s", "expected_yield_pct"):
            if data.get(f) not in (None, ""):
                changes[f] = data[f]
        if data.get("machine"):
            changes["machine"] = Machine.objects.get(pk=data["machine"])
        if data.get("mould"):
            changes["mould"] = Mould.objects.get(pk=data["mould"])
        if not changes:
            raise BusinessRuleError("Enter at least one revised value.", "validation")
        kind = data.get("kind") or ("cycle" if "cycle_s" in changes else "quantity" if "planned_qty" in changes else "other")
        services.revise_line(obj, changes, data["reason"], request.user, kind)
        obj.refresh_from_db()
        return obj

    def _set_status(status, kind):
        def handler(request, obj, data):
            services.revise_line(obj, {"status": status}, data["reason"], request.user, kind)
            obj.refresh_from_db()
            return obj
        return handler

    def _generate_pr(request, obj, data):
        shift_date = parse_date(str(data.get("shift_date", "")))
        shift = data.get("shift")
        if not shift_date or shift not in {"day", "night"}:
            raise BusinessRuleError("Select date and shift.", "validation")
        start = parse_datetime(data["start"]) if data.get("start") else None
        end = parse_datetime(data["end"]) if data.get("end") else None
        return services.create_pr(line=obj, shift_date=shift_date, shift=shift, user=request.user, start=start, end=end,
                                  planned_pcs=data.get("planned_pcs") or None)

    actions = [
        Action("revise", "Revise (versioned)", _revise, permission="planning.planline.update", reason_required=True, tone="primary",
               fields=[{"name": "kind", "label": "Change type", "type": "choice", "choices": [{"value": v, "label": l} for v, l in PlanRevision.KINDS]},
                       {"name": "cycle_s", "label": "Cycle (s)", "type": "decimal"},
                       {"name": "planned_qty", "label": "Good qty target", "type": "integer"},
                       {"name": "active_cavities", "label": "Active cavities", "type": "integer"},
                       {"name": "expected_yield_pct", "label": "Expected yield %", "type": "decimal"},
                       {"name": "sequence", "label": "Sequence on machine", "type": "integer"},
                       {"name": "machine", "label": "Machine", "type": "fk", "target": "masters.machine"},
                       {"name": "mould", "label": "Mould", "type": "fk", "target": "masters.mould"}],
               available=lambda o, r: o.status not in {"done", "cancelled"}),
        Action("stop", "Stop", _set_status("stopped", "stop"), permission="planning.planline.update", reason_required=True,
               available=lambda o, r: o.status in {"planned", "released", "running"}),
        Action("resume", "Continue", _set_status("released", "continue"), permission="planning.planline.update", reason_required=True,
               available=lambda o, r: o.status == "stopped"),
        Action("cancel", "Cancel line", _set_status("cancelled", "cancel"), permission="planning.planline.update", reason_required=True, tone="danger",
               available=lambda o, r: o.status in {"planned", "stopped"}),
        Action("generate_pr", "Create shift PR", _generate_pr, permission="planning.productionrequest.create", tone="primary",
               fields=[{"name": "shift_date", "label": "Date", "type": "date", "required": True},
                       {"name": "shift", "label": "Shift", "type": "choice", "choices": [{"value": "day", "label": "Day 09-21"}, {"value": "night", "label": "Night 21-09"}], "required": True},
                       {"name": "start", "label": "Segment start (optional)", "type": "datetime"},
                       {"name": "end", "label": "Segment end (optional)", "type": "datetime"},
                       {"name": "planned_pcs", "label": "Planned moulded pcs (optional)", "type": "integer"}],
               available=lambda o, r: o.status in {"planned", "released", "running"}),
    ]


@register
class PlanRevisionResource(Resource):
    key = "planning.planrevision"
    in_nav = False
    model = PlanRevision
    label = "Plan revisions"
    singular = "Plan revision"
    module = "planning"
    title_field = "revision"
    allow_create = False
    allow_update = False
    list_fields = ["plan_line", "revision", "kind", "reason", "created_by", "created_at"]
    readonly_fields = ["changes", "impact"]
    filter_fields = ["plan_line", "kind"]


@register
class ProductionRequestResource(Resource):
    key = "planning.productionrequest"
    model = ProductionRequest
    label = "Shift PRs"
    singular = "Shift PR"
    module = "planning"
    list_fields = ["doc_no", "shift_date", "shift", "machine", "component", "artwork", "planned_pcs", "status", "so_line", "parent_pr"]
    readonly_fields = ["mould", "item", "recipe", "segment_start", "segment_end", "active_cavities", "cycle_s", "snapshot", "readiness",
                       "released_by", "released_at", "cancel_reason", "handover_note"]
    form_fields = ["handover_note"]
    filter_fields = ["status", "machine", "shift_date", "shift", "so_line", "plan_line"]
    search_fields = ["doc_no", "machine__code"]
    allow_create = False
    inlines = [{"resource": "planning.prmaterial", "fk": "pr", "label": "Shift material requirement"},
               {"resource": "production.productionreport", "fk": "pr", "label": "Production reports"}]
    help = {
        "why": "Released instruction for one machine and one 12-hour shift; stores issue only against it.",
        "effect": "Release snapshots recipe/artwork and reserves the shift's materials.",
        "boundary": "Cancelled PR: return unused material against this PR; reissue creates a new linked PR.",
    }

    def edit_block_reason(self, request, obj):
        if obj.status in {"closed", "cancelled"}:
            return f"PR is {obj.status}."
        return None

    def extra_detail(self, request, obj):
        from apps.production.models import ProductionReport
        reports = ProductionReport.objects.filter(pr=obj, status__in=["submitted", "corrected"])
        return {
            "materials": [
                {"item": m.item.code, "kind": m.kind, "required": str(m.required_qty), "issued": str(m.issued_qty), "returned": str(m.returned_qty),
                 "consumed": str(m.consumed_qty), "pending_issue": str(m.pending_issue), "machine_side": str(m.machine_side_balance), "uom": m.uom, "basis": m.basis}
                for m in obj.materials.select_related("item")
            ],
            "output": {k: sum(getattr(r, k) for r in reports) for k in ("total_moulded", "good_qty", "held_qty", "rejected_qty")},
            "reissues": [p.doc_no for p in obj.reissues.all()],
        }

    def _release(request, obj, data):
        return services.release_pr(obj, request.user)

    def _cancel(request, obj, data):
        new = services.cancel_pr(obj, data["reason"], request.user, reissue=bool(data.get("reissue")))
        return new or obj

    def _close(request, obj, data):
        if obj.status not in {"released", "in_progress"}:
            raise BusinessRuleError("Only running PRs can be closed.", "invalid_state")
        from apps.inventory.services import release_reservations
        release_reservations(pr=obj, reason="PR closed")
        obj.status = "closed"
        obj.handover_note = data.get("handover_note", obj.handover_note)
        obj.row_version += 1
        obj.save()
        audit.record("pr_closed", "planning.productionrequest", obj, changes={"handover": obj.handover_note})
        return obj

    actions = [
        Action("release", "Release PR", _release, tone="primary", available=lambda o, r: o.status == "draft"),
        Action("cancel", "Cancel / replace", _cancel, reason_required=True, tone="danger",
               fields=[{"name": "reissue", "label": "Create linked reissue PR for remaining quantity", "type": "boolean"}],
               available=lambda o, r: o.status in {"draft", "released", "in_progress"}),
        Action("close", "Close PR (handover)", _close, permission="production.shift.close",
               fields=[{"name": "handover_note", "label": "Handover note (unfinished work / balances)", "type": "textarea"}],
               available=lambda o, r: o.status in {"released", "in_progress"}),
    ]


@register
class PRMaterialResource(Resource):
    key = "planning.prmaterial"
    in_nav = False
    model = PRMaterial
    label = "PR materials"
    singular = "PR material"
    module = "planning"
    title_field = "item"
    allow_create = False
    allow_update = False
    ordering = ["pr", "kind"]
    list_fields = ["pr", "item", "kind", "artwork", "required_qty", "issued_qty", "returned_qty", "consumed_qty", "uom", "pending_issue", "machine_side_balance"]
    readonly_fields = ["basis"]
    filter_fields = ["pr", "item", "kind"]
