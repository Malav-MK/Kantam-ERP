import datetime

from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.core import audit, calc
from apps.core.api import BusinessRuleError
from apps.core.registry import Action, Resource, register
from apps.core.shifts import is_shift_closed, shift_for

from . import services
from .models import Downtime, LabourAllocation, ProductionReport, ShiftHandover


class ShiftBound(Resource):
    module = "production"

    def edit_block_reason(self, request, obj):
        from apps.core.rbac import has_perm
        if is_shift_closed(obj.shift_date, obj.shift) and not has_perm(request.user, "production.correct_closed"):
            return "Shift closed: record is immutable. Ask an admin for a controlled correction."
        return None


@register
class ProductionReportResource(ShiftBound):
    key = "production.productionreport"
    server_defaults = ["active_cavities", "actual_start", "actual_end"]
    model = ProductionReport
    label = "Production reports"
    singular = "Production report"
    list_fields = ["doc_no", "shift_date", "shift", "machine", "pr", "total_moulded", "good_qty", "held_qty", "rejected_qty", "actual_cycle_s", "status"]
    create_only_fields = ["pr"]
    form_fields = ["operator", "operator_name", "supervisor", "actual_start", "actual_end", "active_cavities", "actual_cycle_s", "total_moulded",
                   "good_qty", "held_qty", "rejected_qty", "startup_loss_pcs", "defect_reasons", "setup_minutes", "label_loss",
                   "extra_classification", "handover_note", "notes"]
    readonly_fields = ["good_lot", "held_lot", "rejected_lot"]
    filter_fields = ["status", "machine", "shift_date", "shift", "pr"]
    search_fields = ["doc_no", "pr__doc_no"]
    depends = {"pr": {"status": "=released,in_progress"}}
    field_help = {
        "total_moulded": "Exclusive buckets: total moulded = good + held + rejected (rework tracked without double counting).",
        "actual_cycle_s": "A different actual cycle reforecasts remaining work; the baseline is kept.",
        "held_qty": "Held pieces go to QC hold; only QC can release them.",
    }
    help = {
        "why": "Report physical outcomes, not only targets; QC stock and the reforecast depend on these entries.",
        "boundary": "Night shift spans midnight but keeps its shift date. After shift close, records are immutable to normal users.",
    }

    def before_create(self, request, data):
        pr = data["pr"]
        data["machine"] = pr.machine
        data["shift_date"] = pr.shift_date
        data["shift"] = pr.shift
        data.setdefault("active_cavities", pr.active_cavities)
        data.setdefault("actual_start", pr.segment_start)
        data.setdefault("actual_end", pr.segment_end)
        data.setdefault("operator", request.user)
        services.assert_shift_open(pr.shift_date, pr.shift, request.user)
        if pr.status not in {"released", "in_progress"}:
            raise BusinessRuleError("PR is not released.", "pr_not_released")

    def edit_block_reason(self, request, obj):
        if obj.status != "draft":
            return "Submitted reports are posted; use admin correction."
        return super().edit_block_reason(request, obj)

    def extra_detail(self, request, obj):
        return {"reject_pct": str(calc.reject_pct(obj.rejected_qty, obj.total_moulded)),
                "reconciled": calc.reconcile_output(obj.total_moulded, obj.good_qty, obj.held_qty, obj.rejected_qty)}

    def _submit(request, obj, data):
        return services.submit_report(obj, request.user, consumption=data.get("consumption") or [])

    def _correct(request, obj, data):
        changes = {k: v for k, v in data.items() if k in {"good_qty", "held_qty", "rejected_qty", "total_moulded", "actual_cycle_s", "defect_reasons", "notes"} and v not in (None, "")}
        return services.admin_correct(obj, changes, data["reason"], request.user)

    actions = [
        Action("submit", "Submit & post output", _submit, permission="production.productionreport.create", tone="primary",
               available=lambda o, r: o.status == "draft"),
        Action("correct", "Admin correction", _correct, permission="production.correct_closed", reason_required=True, tone="danger",
               fields=[{"name": "total_moulded", "label": "Total moulded", "type": "integer"},
                       {"name": "good_qty", "label": "Good", "type": "integer"},
                       {"name": "held_qty", "label": "Held", "type": "integer"},
                       {"name": "rejected_qty", "label": "Rejected", "type": "integer"},
                       {"name": "defect_reasons", "label": "Defect reasons", "type": "text"}],
               available=lambda o, r: o.status in {"submitted", "corrected"}),
    ]


@register
class DowntimeResource(ShiftBound):
    key = "production.downtime"
    model = Downtime
    label = "Downtime events"
    singular = "Downtime"
    title_field = "kind"
    ordering = ["-start_at"]
    list_fields = ["machine", "kind", "reason", "start_at", "end_at", "minutes", "pr", "shift_date", "shift"]
    form_fields = ["machine", "pr", "kind", "reason", "start_at", "end_at", "concurrent_with", "breakdown", "notes"]
    filter_fields = ["machine", "kind", "shift_date", "shift"]
    depends = {"reason": {"kind": "=downtime"}, "pr": {"machine": "machine"}}

    def _validate(self, data, obj=None):
        start, end = data.get("start_at") or obj.start_at, data.get("end_at", obj.end_at if obj else None)
        if end and end <= start:
            raise BusinessRuleError("End must be after start.", "invalid_time")
        machine = data.get("machine") or obj.machine
        qs = Downtime.objects.filter(machine=machine, concurrent_with__isnull=True)
        if obj:
            qs = qs.exclude(pk=obj.pk)
        if not data.get("concurrent_with", obj.concurrent_with if obj else None):
            far = end or timezone.now() + datetime.timedelta(days=365)
            clash = [d for d in qs if d.start_at < far and (d.end_at is None or d.end_at > start)]
            if clash:
                raise BusinessRuleError("Overlaps another downtime on this machine. Link it as concurrent to avoid double counting lost minutes.", "time_overlap")

    def before_create(self, request, data):
        slot = shift_for(data["start_at"])
        data["shift_date"], data["shift"] = slot.shift_date, slot.shift
        services.assert_shift_open(slot.shift_date, slot.shift, request.user)
        self._validate(data)

    def before_update(self, request, obj, data):
        self._validate(data, obj)


@register
class LabourAllocationResource(ShiftBound):
    key = "production.labourallocation"
    model = LabourAllocation
    label = "Labour allocation"
    singular = "Allocation"
    title_field = "role"
    ordering = ["-shift_date"]
    list_fields = ["person", "shift_date", "shift", "machine", "pr", "role", "hours", "shared", "approved"]
    form_fields = ["person", "shift_date", "shift", "machine", "pr", "role", "hours", "shared"]
    filter_fields = ["shift_date", "shift", "machine", "approved"]
    help = {"boundary": "Helper hours split across machines are never counted twice; full payroll/HR is out of scope."}

    def before_create(self, request, data):
        from django.db.models import Sum
        used = LabourAllocation.objects.filter(person=data["person"], shift_date=data["shift_date"], shift=data["shift"]).aggregate(t=Sum("hours"))["t"] or 0
        if used + data["hours"] > 12:
            raise BusinessRuleError(f"{data['person']} already has {used} h in this 12-hour shift.", "hours_exceed")

    def _approve(request, obj, data):
        obj.approved = True
        obj.approved_by = request.user
        obj.save()
        audit.record("labour_approved", "production.labourallocation", obj)
        return obj

    actions = [Action("approve", "Approve hours", _approve, permission="production.labourallocation.update", available=lambda o, r: not o.approved)]


@register
class ShiftHandoverResource(ShiftBound):
    key = "production.shifthandover"
    model = ShiftHandover
    label = "Shift handover"
    singular = "Handover"
    title_field = "machine"
    ordering = ["-shift_date"]
    list_fields = ["machine", "shift_date", "shift", "handed_over_by", "received_by", "acknowledged_at"]
    form_fields = ["machine", "shift_date", "shift", "unfinished", "material_balance", "blockers", "received_by"]
    filter_fields = ["machine", "shift_date", "shift"]

    def before_create(self, request, data):
        data["handed_over_by"] = request.user

    def _ack(request, obj, data):
        if obj.received_by_id and obj.received_by_id != request.user.pk:
            raise BusinessRuleError("Only the receiving supervisor can acknowledge.", "not_authorised")
        obj.received_by = request.user
        obj.acknowledged_at = timezone.now()
        obj.save()
        audit.record("handover_acknowledged", "production.shifthandover", obj)
        return obj

    actions = [Action("acknowledge", "Acknowledge handover", _ack, permission="production.shifthandover.view", available=lambda o, r: o.acknowledged_at is None)]


def close_shift_action(request, obj, data):
    d = parse_date(str(data.get("shift_date", "")))
    if not d or data.get("shift") not in {"day", "night"}:
        raise BusinessRuleError("Select date and shift.", "validation")
    return services.close_shift(d, data["shift"], request.user)


ProductionReportResource.actions.append(
    Action("close_shift", "Close a shift", close_shift_action, permission="production.shift.close", detail=False, tone="danger",
           confirm="Closed shift records become immutable to normal users.",
           fields=[{"name": "shift_date", "label": "Shift date", "type": "date", "required": True},
                   {"name": "shift", "label": "Shift", "type": "choice", "choices": [{"value": "day", "label": "Day"}, {"value": "night", "label": "Night"}], "required": True}])
)
