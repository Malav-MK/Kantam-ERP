from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.core import audit
from apps.core.api import BusinessRuleError
from apps.core.rbac import has_perm, is_owner, role_levels
from apps.core.registry import Action, Resource, register
from apps.core.services import notify, notify_perm

from . import services
from .models import Asset, Breakdown, Incident, PMPlan, Task

BLOCKERS = [{"value": v, "label": l} for v, l in Task._meta.get_field("blocker_reason").choices]


def _t(action):
    return lambda request, obj, data: services.transition(obj, request.user, action, data)


@register
class TaskResource(Resource):
    key = "tasks.task"
    model = Task
    label = "Tasks & work proof"
    singular = "Task"
    module = "tasks"
    ordering = ["state", "due_at"]
    list_fields = ["doc_no", "title", "kind", "assignee", "reviewer", "priority", "original_due_at", "due_at", "state", "proof_round", "asset"]
    # due_at is set once here; later changes go through "Revise due date" (reason + approval), never a silent edit.
    create_only_fields = ["kind", "source_resource", "source_id", "source_label", "checklist", "checklist_revision", "due_at"]
    form_fields = ["title", "instructions", "department", "assignee", "reviewer", "asset", "priority", "expected_minutes", "evidence_required"]
    readonly_fields = ["original_due_at", "due_revisions", "late_vs_original", "answers", "report_text", "photo_exception_reason", "completion_note", "blocker_reason", "blocker_note", "review_note",
                       "accepted_at", "started_at", "submitted_at", "verified_at", "verified_by", "actual_minutes", "pm_plan"]
    filter_fields = ["state", "assignee", "reviewer", "kind", "priority", "asset", "department"]
    search_fields = ["doc_no", "title", "source_label"]
    help = {
        "steps": "Assigned -> Accepted -> In progress -> Submitted (with proof) -> Verified by a different reviewer. Reopen starts a new proof round.",
        "boundary": "Seen/read is not completion. Submitted is not verified. Old evidence cannot satisfy a reopened round.",
    }

    def scope(self, request, qs):
        user = request.user
        if role_levels(user) & {"owner"}:
            return qs
        cond = Q(assignee=user) | Q(reviewer=user) | Q(created_by=user)
        if role_levels(user) & {"manager"}:
            from apps.core.rbac import scoped_department_ids
            cond |= Q(department_id__in=scoped_department_ids(user)) | Q(assignee__reporting_manager=user)
        return qs.filter(cond)

    def before_create(self, request, data):
        if data["assignee"].pk != request.user.pk and not has_perm(request.user, "tasks.assign"):
            raise BusinessRuleError("Assigning tasks to others needs tasks.assign.", "not_authorised")
        if data.get("reviewer") and data["reviewer"].pk == data["assignee"].pk:
            raise BusinessRuleError("Reviewer must be different from the assignee.", "validation")
        data.setdefault("reviewer", request.user if request.user.pk != data["assignee"].pk else None)

    def after_create(self, request, obj):
        notify([obj.assignee], f"New task: {obj.title}", resource="tasks.task", object_id=obj.pk, next_action="Acknowledge", due_at=obj.due_at,
               dedupe_key=f"task-new:{obj.pk}")

    def edit_block_reason(self, request, obj):
        if obj.state in {"submitted", "verified", "cancelled"}:
            return "Submitted evidence is frozen against silent editing."
        if obj.assignee_id == request.user.pk and obj.created_by_id != request.user.pk:
            return "Use the task actions to report progress."
        return None

    def attachment_policy(self, request, obj, purpose):
        if obj.state in {"submitted", "verified", "cancelled"}:
            return {"blocked": "Proof is frozen. Reopen the task to start a new proof round."}
        return {"proof_round": obj.proof_round, "classification": "internal"}

    def extra_detail(self, request, obj):
        return {"missing_proof": services.proof_satisfied(obj) if obj.state in {"in_progress", "accepted", "reopened"} else [],
                "overdue": obj.due_at < timezone.now() and obj.state not in {"verified", "cancelled", "submitted"},
                "late_vs_original": obj.late_vs_original}

    actions = [
        Action("accept", "Acknowledge / accept", _t("accept"), permission="tasks.task.update", tone="primary",
               available=lambda o, r: o.state in {"assigned", "reopened"} and o.assignee_id == r.user.pk),
        Action("start", "Start work", _t("start"), permission="tasks.task.update", available=lambda o, r: o.state in {"accepted", "reopened"} and o.assignee_id == r.user.pk),
        Action("block", "Record blocker", _t("block"), permission="tasks.task.update",
               fields=[{"name": "blocker_reason", "label": "Blocker", "type": "choice", "choices": BLOCKERS, "required": True},
                       {"name": "blocker_note", "label": "Details", "type": "text"}],
               available=lambda o, r: o.state in {"accepted", "in_progress"} and o.assignee_id == r.user.pk),
        Action("resume", "Resume", _t("resume"), permission="tasks.task.update", available=lambda o, r: o.state == "blocked" and o.assignee_id == r.user.pk),
        Action("submit", "Submit with proof", _t("submit"), permission="tasks.task.update", tone="primary",
               fields=[{"name": "answers", "label": "Checklist answers", "type": "checklist"},
                       {"name": "report_text", "label": "Report / readings", "type": "textarea"},
                       {"name": "photo_exception_reason", "label": "Photo not safe/possible because", "type": "text"},
                       {"name": "completion_note", "label": "Completion note", "type": "textarea"},
                       {"name": "actual_minutes", "label": "Actual minutes", "type": "integer"}],
               available=lambda o, r: o.state in {"in_progress", "accepted"} and o.assignee_id == r.user.pk),
        Action("verify", "Verify", _t("verify"), permission="tasks.verify", tone="primary",
               fields=[{"name": "note", "label": "Review note", "type": "text"}], available=lambda o, r: o.state == "submitted" and o.assignee_id != r.user.pk),
        Action("reopen", "Reopen (new proof round)", _t("reopen"), permission="tasks.verify", reason_required=True, tone="danger",
               available=lambda o, r: o.state in {"submitted", "verified"} and o.assignee_id != r.user.pk),
        Action("revise_due", "Revise due date", _t("revise_due"), permission="tasks.assign", reason_required=True,
               fields=[{"name": "due_at", "label": "New due date / time", "type": "datetime", "required": True}],
               available=lambda o, r: o.state not in {"verified", "cancelled"}),
        Action("cancel", "Cancel task", _t("cancel"), permission="tasks.assign", reason_required=True, tone="danger",
               available=lambda o, r: o.state not in {"verified", "cancelled"}),
    ]


@register
class AssetResource(Resource):
    key = "tasks.asset"
    model = Asset
    label = "Asset register"
    singular = "Asset"
    module = "tasks"
    title_field = "name"
    ordering = ["code"]
    list_fields = ["code", "name", "kind", "make", "model_no", "location", "status", "warranty_until"]
    form_fields = ["code", "name", "kind", "machine", "mould", "make", "model_no", "serial_no", "location", "manual_reference", "service_provider", "warranty_until", "status"]
    filter_fields = ["kind", "status"]
    search_fields = ["code", "name", "serial_no"]
    inlines = [{"resource": "tasks.pmplan", "fk": "asset", "label": "PM plans"}, {"resource": "tasks.breakdown", "fk": "asset", "label": "Breakdowns"}]
    help = {"boundary": "Unknown equipment stays unclassified until the nameplate/manual is recorded. No generic OEM intervals are assumed."}


@register
class PMPlanResource(Resource):
    key = "tasks.pmplan"
    model = PMPlan
    label = "PM plans"
    singular = "PM plan"
    module = "tasks"
    list_fields = ["doc_no", "asset", "title", "interval", "recurrence_basis", "assignee", "next_due", "status"]
    form_fields = ["asset", "title", "interval", "recurrence_basis", "checklist", "checklist_revision", "oem_reference", "competence", "permit_required",
                   "evidence_required", "expected_minutes", "window_hours", "assignee", "reviewer", "next_due"]
    readonly_fields = ["approved_by"]
    filter_fields = ["status", "interval", "asset", "assignee"]
    search_fields = ["doc_no", "title"]
    field_help = {"checklist": '[{"id":1,"text":"Oil level within marks","critical":true}] - template revisions never change past sign-offs.'}
    help = {"boundary": "An unapproved template stays inactive. Calendar buckets are configurable; actual oil/filter/electrical work follows the model-specific OEM manual."}

    def edit_block_reason(self, request, obj):
        if obj.status == "approved" and not has_perm(request.user, "maintenance.pm.approve"):
            return "Only maintenance approval authority can change an active plan."
        return None

    def after_update(self, request, obj, changes):
        if obj.status == "approved" and {"checklist", "interval"} & set(changes):
            PMPlan.objects.filter(pk=obj.pk).update(checklist_revision=str(int(obj.checklist_revision or "1") + 1) if (obj.checklist_revision or "1").isdigit() else obj.checklist_revision)

    def _approve(request, obj, data):
        if not obj.oem_reference:
            raise BusinessRuleError("Record the OEM manual / revision before activation.", "oem_reference_required")
        obj.status = "approved"
        obj.approved_by = request.user
        obj.row_version += 1
        obj.save()
        audit.record("pm_plan_approved", "tasks.pmplan", obj)
        return obj

    def _deactivate(request, obj, data):
        obj.status = "inactive"
        obj.save()
        audit.record("pm_plan_deactivated", "tasks.pmplan", obj, reason=data["reason"])
        return obj

    def _generate(request, obj, data):
        return {"created": services.generate_pm_tasks()}

    actions = [
        Action("approve", "Approve & activate", _approve, permission="maintenance.pm.approve", tone="primary", available=lambda o, r: o.status != "approved"),
        Action("deactivate", "Deactivate", _deactivate, permission="maintenance.pm.approve", reason_required=True, tone="danger", available=lambda o, r: o.status == "approved"),
        Action("generate", "Run PM scheduler now", _generate, permission="maintenance.pm.approve", detail=False),
    ]


@register
class BreakdownResource(Resource):
    key = "tasks.breakdown"
    model = Breakdown
    label = "Breakdown work orders"
    singular = "Breakdown"
    module = "tasks"
    list_fields = ["doc_no", "asset", "machine", "severity", "failure_at", "technician", "status", "restart_at"]
    create_only_fields = ["asset", "machine", "pr", "failure_at"]
    form_fields = ["severity", "symptoms", "alarm_codes", "technician", "suspected_cause", "confirmed_cause", "action_taken", "parts_used",
                   "repair_cost", "permit_ref", "test_result", "first_piece_inspection", "recurrence_of"]
    readonly_fields = ["acknowledged_at", "repair_start_at", "repair_end_at", "restart_approved_by"]
    filter_fields = ["status", "asset", "machine", "severity", "technician"]
    search_fields = ["doc_no", "symptoms"]
    field_permissions = {"repair_cost": "commercial.cost.view"}
    FLOW = ["reported", "acknowledged", "diagnosis", "awaiting_spares", "repair", "testing", "restart_approved", "closed"]
    help = {
        "steps": "Report -> triage -> assign competent technician -> permit -> diagnose -> spares -> repair -> test -> supervisor & QC restart -> verify effectiveness.",
        "boundary": "App checkpoints only - hands-on work follows the OEM/site isolation procedure. A reset alarm alone does not close the issue.",
    }

    def before_create(self, request, data):
        data.setdefault("failure_at", timezone.now())

    def after_create(self, request, obj):
        from apps.production.models import Downtime
        from apps.core.shifts import shift_for
        if obj.machine_id:
            type(obj.machine).objects.filter(pk=obj.machine_id).update(status="breakdown")
            slot = shift_for(obj.failure_at)
            Downtime.objects.create(machine=obj.machine, pr=obj.pr, kind="machine", start_at=obj.failure_at, shift_date=slot.shift_date,
                                    shift=slot.shift, breakdown=obj, notes=f"Breakdown {obj.doc_no}", created_by=request.user)
            from apps.planning.services import reforecast_machine
            reforecast_machine(obj.machine)
        notify_perm("maintenance.pm.approve", f"Breakdown {obj.doc_no}: {obj.asset}", body=obj.symptoms, severity="critical",
                    resource="tasks.breakdown", object_id=obj.pk, next_action="Acknowledge and assign technician", dedupe_key=f"bd:{obj.pk}")

    def edit_block_reason(self, request, obj):
        return "Closed." if obj.status == "closed" else None

    def _advance(request, obj, data):
        flow = BreakdownResource.FLOW
        nxt = data.get("to") or flow[flow.index(obj.status) + 1]
        if nxt not in flow or flow.index(nxt) <= flow.index(obj.status):
            raise BusinessRuleError("Invalid next state.", "invalid_state")
        now = timezone.now()
        if nxt == "acknowledged":
            obj.acknowledged_at = now
        if nxt in {"diagnosis", "repair"} and not obj.technician_id:
            raise BusinessRuleError("Assign a competent technician first.", "validation")
        if nxt == "repair":
            if obj.severity in {"major", "safety"} and not obj.permit_ref:
                raise BusinessRuleError("Record the isolation / permit reference before repair.", "permit_required")
            obj.repair_start_at = obj.repair_start_at or now
        if nxt == "testing":
            obj.repair_end_at = now
            if not obj.action_taken:
                raise BusinessRuleError("Record the action taken.", "validation")
        if nxt == "restart_approved":
            if not obj.test_result:
                raise BusinessRuleError("Record the test / measurement result.", "validation")
            fpi = obj.first_piece_inspection
            if fpi is None or fpi.status != "decided" or fpi.kind not in {"restart", "first_piece"}:
                raise BusinessRuleError("A decided QC restart / first-piece inspection is required before restart.", "qc_restart_required")
            if obj.technician_id == request.user.pk and not is_owner(request.user):  # owner = super admin: no maker/checker
                raise BusinessRuleError("Restart approval must be by a supervisor other than the technician.", "self_approval")
            obj.restart_approved_by = request.user
            obj.restart_at = now
            if obj.machine_id:
                type(obj.machine).objects.filter(pk=obj.machine_id).update(status="available")
                obj.downtimes.filter(end_at__isnull=True).update(end_at=now)
        if nxt == "closed" and not obj.confirmed_cause:
            raise BusinessRuleError("Record the confirmed cause (separate from suspected cause) before closing.", "validation")
        before = obj.status
        obj.status = nxt
        obj.row_version += 1
        obj.save()
        audit.record("breakdown_advanced", "tasks.breakdown", obj, changes={"status": [before, nxt]}, reason=data.get("reason", ""))
        return obj

    def _technician_task(request, obj, data):
        if not obj.technician_id:
            raise BusinessRuleError("Assign a technician first.", "validation")
        task = services.create_task(title=f"Repair {obj.asset} ({obj.doc_no})", assignee=obj.technician, due_at=timezone.now() + timedelta(hours=4),
                                    user=request.user, kind="breakdown", asset=obj.asset, reviewer=request.user, evidence_required="photo",
                                    source_resource="tasks.breakdown", source_id=str(obj.pk), source_label=obj.doc_no, priority="critical")
        return task

    actions = [
        Action("advance", "Advance work order", _advance, permission="tasks.breakdown.update", tone="primary",
               fields=[{"name": "to", "label": "Move to (optional)", "type": "choice", "choices": [{"value": v, "label": l} for v, l in Breakdown.STATUS]}],
               available=lambda o, r: o.status != "closed"),
        Action("technician_task", "Create technician task (before/after photos)", _technician_task, permission="tasks.assign"),
    ]


@register
class IncidentResource(Resource):
    key = "tasks.incident"
    model = Incident
    label = "Incident register"
    singular = "Incident"
    module = "tasks"
    list_fields = ["doc_no", "kind", "severity", "title", "occurred_at", "accountable", "due_date", "status"]
    form_fields = ["kind", "severity", "title", "description", "occurred_at", "asset", "immediate_response", "accountable", "due_date", "root_cause",
                   "action", "verification", "recurrence_of", "sensitive"]
    filter_fields = ["status", "kind", "severity", "accountable"]
    search_fields = ["doc_no", "title"]
    FLOW = ["reported", "contained", "assigned", "investigation", "verification", "closed"]

    def scope(self, request, qs):
        if has_perm(request.user, "hr.personal.view") or role_levels(request.user) & {"owner"}:
            return qs
        return qs.filter(Q(sensitive=False) | Q(accountable=request.user) | Q(created_by=request.user))

    def _advance(request, obj, data):
        flow = IncidentResource.FLOW
        nxt = flow[flow.index(obj.status) + 1]
        req = {"contained": "immediate_response", "assigned": "accountable_id", "investigation": "root_cause", "verification": "action", "closed": "verification"}[nxt]
        if not getattr(obj, req):
            raise BusinessRuleError(f"Record {req.replace('_id', '').replace('_', ' ')} first.", "validation")
        if nxt == "closed" and obj.accountable_id == request.user.pk and not is_owner(request.user):
            raise BusinessRuleError("Closure must be verified by someone other than the accountable person.", "self_approval")
        before = obj.status
        obj.status = nxt
        obj.save()
        audit.record("incident_advanced", "tasks.incident", obj, changes={"status": [before, nxt]})
        return obj

    actions = [Action("advance", "Advance", _advance, permission="tasks.incident.update", tone="primary", available=lambda o, r: o.status != "closed")]
