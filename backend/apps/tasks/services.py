"""Task, PM scheduling and reminder services (brief p.48-57)."""
import calendar
import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core import audit
from apps.core.api import BusinessRuleError
from apps.core.attachments import attachments_for
from apps.core.services import notify

from .models import PMPlan, Task

INTERVAL_DELTA = {
    "12h": datetime.timedelta(hours=12), "24h": datetime.timedelta(hours=24), "weekly": datetime.timedelta(days=7),
    "15d": datetime.timedelta(days=15), "30d": datetime.timedelta(days=30),
}
INTERVAL_MONTHS = {"quarterly": 3, "half_yearly": 6, "yearly": 12}


def add_months(dt, months):
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def next_due(plan_interval, current):
    if plan_interval in INTERVAL_DELTA:
        return current + INTERVAL_DELTA[plan_interval]
    return add_months(current, INTERVAL_MONTHS[plan_interval])


def proof_satisfied(task):
    """Required checklist answers and evidence for the CURRENT proof round (p.48)."""
    missing = []
    for item in task.checklist or []:
        ans = (task.answers or {}).get(str(item.get("id")))
        if ans in (None, ""):
            missing.append(f"Checklist: {item.get('text')}")
    photos = attachments_for(task, proof_round=task.proof_round).exists()
    has_report = bool(task.report_text.strip())
    req = task.evidence_required
    if req == "photo" and not photos:
        if task.photo_exception_reason and has_report:
            pass  # documented unsafe-photo alternative; reviewer must accept
        else:
            missing.append("Completion photo for this proof round (or documented exception + report)")
    if req == "report" and not has_report:
        missing.append("Report / readings")
    if req == "photo_or_report" and not (photos or has_report):
        missing.append("Photo or report")
    return missing


def critical_failures(task):
    fails = []
    for item in task.checklist or []:
        if item.get("critical") and str((task.answers or {}).get(str(item.get("id")), "")).lower() in {"no", "fail", "false"}:
            fails.append(item.get("text"))
    return fails


def transition(task, user, action, data):
    now = timezone.now()
    s = task.state
    if action in {"accept", "start", "block", "resume", "submit"} and task.assignee_id != user.pk:
        raise BusinessRuleError("Only the assignee can update work progress.", "not_assignee")
    if action == "accept":
        if s not in {"assigned", "reopened"}:
            raise BusinessRuleError("Task is not awaiting acceptance.", "invalid_state")
        task.state, task.accepted_at = "accepted", now
    elif action == "start":
        if s not in {"accepted", "reopened"}:
            raise BusinessRuleError("Accept the task first.", "invalid_state")
        task.state, task.started_at = "in_progress", now
    elif action == "block":
        if s not in {"accepted", "in_progress"}:
            raise BusinessRuleError("Only active tasks can be blocked.", "invalid_state")
        if not data.get("blocker_reason"):
            raise BusinessRuleError("Name the blocker.", "validation")
        task.state, task.blocker_reason, task.blocker_note = "blocked", data["blocker_reason"], data.get("blocker_note", "")
        resolver = task.reviewer or task.assignee.reporting_manager
        if resolver:
            notify([resolver], f"Blocked: {task.title}", body=f"{task.get_blocker_reason_display()}: {task.blocker_note}", severity="warning",
                   resource="tasks.task", object_id=task.pk, next_action="Resolve the blocker", dedupe_key=f"blocked:{task.pk}:{task.blocker_reason}:{now:%Y%m%d%H}")
    elif action == "resume":
        if s != "blocked":
            raise BusinessRuleError("Task is not blocked.", "invalid_state")
        task.state = "in_progress"
    elif action == "submit":
        if s not in {"in_progress", "accepted"}:
            raise BusinessRuleError("Start the task before submitting.", "invalid_state")
        for k in ("answers", "report_text", "photo_exception_reason", "completion_note"):
            if k in data and data[k] not in (None,):
                setattr(task, k, data[k])
        if data.get("actual_minutes"):
            task.actual_minutes = int(data["actual_minutes"])
        missing = proof_satisfied(task)
        if missing:
            raise BusinessRuleError("Submission blocked - proof missing: " + "; ".join(missing), "proof_required", {"missing": missing})
        task.state, task.submitted_at = "submitted", now
        attachments_for(task, proof_round=task.proof_round).update(frozen=True)
        if task.reviewer:
            notify([task.reviewer], f"Review needed: {task.title}", resource="tasks.task", object_id=task.pk, next_action="Verify or reopen",
                   dedupe_key=f"review:{task.pk}:{task.proof_round}")
    elif action == "verify":
        if s != "submitted":
            raise BusinessRuleError("Only submitted work can be verified.", "invalid_state")
        if user.pk == task.assignee_id:
            raise BusinessRuleError("Verification must be by a different authorised reviewer.", "self_verification")
        fails = critical_failures(task)
        if fails:
            raise BusinessRuleError("Critical checkpoint failed: " + "; ".join(fails) + ". Reopen or escalate.", "critical_failure")
        task.state, task.verified_at, task.verified_by, task.review_note = "verified", now, user, data.get("note", "")
        _after_verified(task, user)
    elif action == "reopen":
        if s not in {"submitted", "verified"}:
            raise BusinessRuleError("Only submitted/verified work can be reopened.", "invalid_state")
        if user.pk == task.assignee_id:
            raise BusinessRuleError("Assignee cannot reopen own work.", "self_verification")
        task.state, task.review_note = "reopened", data.get("reason", "")
        task.proof_round += 1  # old evidence stays in history but cannot satisfy the new round
        task.submitted_at = None
        notify([task.assignee], f"Reopened: {task.title}", body=task.review_note, severity="warning", resource="tasks.task", object_id=task.pk,
               next_action="Correct and resubmit with new proof", dedupe_key=f"reopen:{task.pk}:{task.proof_round}")
    elif action == "revise_due":
        if s in {"verified", "cancelled"}:
            raise BusinessRuleError("Closed tasks cannot be re-dated.", "invalid_state")
        new_due = data.get("due_at")
        if isinstance(new_due, str):
            new_due = parse_datetime(new_due)
        if new_due is None:
            raise BusinessRuleError("Give the new due date and time.", "validation")
        if timezone.is_naive(new_due):
            new_due = timezone.make_aware(new_due)
        if new_due == task.due_at:
            raise BusinessRuleError("The new due date is the same as the current one.", "validation")
        if len((data.get("reason") or "").strip()) < 5:
            raise BusinessRuleError("A reason is required to change a due date.", "reason_required")
        task.due_revisions = [*task.due_revisions, {"from": task.due_at.isoformat(), "to": new_due.isoformat(), "reason": data["reason"].strip(),
                                                    "by": user.username, "at": now.isoformat()}]
        task.due_at, task.overdue_notified = new_due, False
        notify([task.assignee], f"Due date changed: {task.title}", body=f"New due {timezone.localtime(new_due):%d-%b %H:%M}. Original due is kept for reporting.",
               resource="tasks.task", object_id=task.pk, next_action="Note the new due date", dedupe_key=f"redue:{task.pk}:{len(task.due_revisions)}")
    elif action == "cancel":
        if s in {"verified", "cancelled"}:
            raise BusinessRuleError("Task already closed.", "invalid_state")
        task.state, task.review_note = "cancelled", data.get("reason", "")
    else:
        raise BusinessRuleError("Unknown action.", "validation")
    task.row_version += 1
    task.save()
    audit.record(f"task_{action}", "tasks.task", task, changes={"state": [s, task.state], "proof_round": task.proof_round}, reason=data.get("reason", ""))
    return task


def _after_verified(task, user):
    """PM: next due is set from verified service for 'after_done' plans; a photo alone never advances it (p.57)."""
    if task.pm_plan_id and task.pm_plan.recurrence_basis == "after_done":
        plan = task.pm_plan
        plan.next_due = next_due(plan.interval, timezone.now())
        plan.save(update_fields=["next_due", "updated_at"])


def generate_pm_tasks(now=None):
    """
    Generate at most one open task per approved plan per due slot. Occurrence keys
    (plan + due slot) make concurrent schedulers/retries idempotent. Missed periods
    are not flooded: one overdue occurrence is kept and the plan advances past now.
    """
    now = now or timezone.now()
    created = 0
    for plan in PMPlan.objects.filter(status="approved", next_due__lte=now + datetime.timedelta(hours=1)).select_related("asset"):
        if Task.objects.filter(pm_plan=plan).exclude(state__in=["verified", "cancelled"]).exists():
            continue
        due = plan.next_due
        key = f"pm:{plan.pk}:{due:%Y%m%d%H%M}"
        try:
            with transaction.atomic():
                Task.objects.create(
                    title=f"PM: {plan.title} - {plan.asset}", instructions=f"OEM ref: {plan.oem_reference}\nPermit required: {'yes' if plan.permit_required else 'no'}",
                    kind="pm", assignee=plan.assignee, reviewer=plan.reviewer, asset=plan.asset, due_at=due, expected_minutes=plan.expected_minutes,
                    evidence_required=plan.evidence_required, checklist=plan.checklist, checklist_revision=plan.checklist_revision, pm_plan=plan,
                    occurrence_key=key, source_resource="tasks.pmplan", source_id=str(plan.pk), source_label=plan.doc_no,
                )
                created += 1
        except IntegrityError:
            continue
        if plan.recurrence_basis == "fixed":
            nxt = next_due(plan.interval, due)
            skipped = 0
            while nxt <= now:
                nxt = next_due(plan.interval, nxt)
                skipped += 1
            plan.next_due = nxt
            plan.save(update_fields=["next_due", "updated_at"])
            if skipped:
                audit.record("pm_missed_periods", "tasks.pmplan", plan, changes={"skipped_slots": skipped}, actor=None)
    return created


def overdue_reminders(now=None):
    """One overdue reminder per task/due date; escalate to manager (p.57)."""
    now = now or timezone.now()
    n = 0
    for task in Task.objects.filter(due_at__lt=now, overdue_notified=False).exclude(state__in=["submitted", "verified", "cancelled"]).select_related("assignee"):
        recipients = [task.assignee] + ([task.assignee.reporting_manager] if task.assignee.reporting_manager_id else []) + ([task.reviewer] if task.reviewer_id else [])
        notify(list({u.pk: u for u in recipients}.values()), f"Overdue: {task.title}", severity="warning" if task.priority != "critical" else "critical",
               resource="tasks.task", object_id=task.pk, next_action="Complete, or record the blocker", due_at=task.due_at,
               dedupe_key=f"overdue:{task.pk}:{task.due_at:%Y%m%d%H%M}")
        Task.objects.filter(pk=task.pk).update(overdue_notified=True)
        n += 1
    return n


def create_task(*, title, assignee, due_at, user=None, **fields):
    task = Task.objects.create(title=title, assignee=assignee, due_at=due_at, created_by=user, owner=user, **fields)
    notify([assignee], f"New task: {title}", resource="tasks.task", object_id=task.pk, next_action="Acknowledge", due_at=due_at, dedupe_key=f"task-new:{task.pk}")
    return task
