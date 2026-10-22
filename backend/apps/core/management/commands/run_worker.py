"""
Durable background worker (brief p.63: the factory scheduler never depends on a
browser tab). Every job is idempotent, so running two workers or retrying is safe.
"""
import datetime
import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from apps.core.models import AuthSession, ShiftLock
from apps.core.services import notify, notify_perm
from apps.core.shifts import ShiftSlot, shift_for

log = logging.getLogger("kantam.worker")


def auto_close_shifts(now=None):
    """Record the automatic close of shifts past end + grace (records are already immutable by time)."""
    from django.conf import settings
    now = now or timezone.now()
    slot = shift_for(now)
    closed = 0
    for _ in range(4):
        slot = ShiftSlot(slot.shift_date - datetime.timedelta(days=1), "night") if slot.shift == "day" else ShiftSlot(slot.shift_date, "day")
        if now >= slot.end + datetime.timedelta(minutes=settings.KANTAM["SHIFT_CLOSE_GRACE_MINUTES"]):
            _, created = ShiftLock.objects.get_or_create(site=None, shift_date=slot.shift_date, shift=slot.shift, defaults={"automatic": True})
            closed += int(created)
    return closed


def purchase_followups(now=None):
    from apps.purchase.services import overdue_po_lines
    today = timezone.localdate()
    n = 0
    for line in overdue_po_lines(today):
        owner = line.follow_up_owner
        title = f"Overdue supply: {line} promised {line.promised_date}"
        kwargs = dict(severity="warning", resource="purchase.poline", object_id=line.pk, next_action="Follow up supplier; record new promise",
                      dedupe_key=f"po-overdue:{line.pk}:{today}")
        n += notify([owner], title, **kwargs) if owner else notify_perm("purchase.poline.followup", title, **kwargs)
    return n


def gate_reminders(now=None):
    from apps.gate.models import Courier, GatePass, Visit
    now = now or timezone.now()
    n = 0
    for v in Visit.objects.filter(status="inside"):
        if v.entry_at and now > v.entry_at + datetime.timedelta(minutes=v.expected_minutes + 30):
            n += notify_perm("dashboard.gate.view", f"Overdue visitor: {v.visitor_name} (host {v.host.full_name})", severity="warning",
                             resource="gate.visit", object_id=v.pk, dedupe_key=f"visit-overdue:{v.pk}")
    for p in GatePass.objects.filter(returnable=True, status__in=["out", "partial"], due_back__lt=timezone.localdate()):
        n += notify([p.asset_owner], f"Returnable pass overdue: {p.asset_description}", severity="warning", resource="gate.gatepass",
                    object_id=p.pk, next_action="Chase return or post authorised disposition", dedupe_key=f"pass-overdue:{p.pk}:{timezone.localdate()}")
    for c in Courier.objects.filter(direction="in", status="received", created_at__lt=now - datetime.timedelta(hours=4)):
        n += notify([c.receiver], f"Parcel still awaiting your acknowledgement: {c.awb}", resource="gate.mycourier", object_id=c.pk,
                    dedupe_key=f"courier-remind:{c.pk}:{timezone.localdate()}")
    return n


def crm_reminders():
    from apps.crm.models import Activity, Quotation
    today = timezone.localdate()
    n = 0
    for a in Activity.objects.filter(done=False, next_follow_up__lt=today).select_related("responsible"):
        n += notify([a.responsible], f"Overdue follow-up: {a.subject}", severity="warning", resource="crm.activity", object_id=a.pk,
                    dedupe_key=f"followup:{a.pk}:{a.next_follow_up}")
    for q in Quotation.objects.filter(status="sent", valid_until__lt=today):
        q.status = "expired"
        q.save(update_fields=["status", "updated_at"])
        if q.created_by:
            n += notify([q.created_by], f"Quotation expired: {q.doc_no}", resource="crm.quotation", object_id=q.pk, dedupe_key=f"qexp:{q.pk}")
    return n


def session_cleanup():
    cutoff = timezone.now() - datetime.timedelta(days=30)
    return AuthSession.objects.filter(revoked_at__lt=cutoff).delete()[0]


def run_once():
    from apps.tasks.services import generate_pm_tasks, overdue_reminders
    return {
        "shifts_closed": auto_close_shifts(),
        "pm_tasks": generate_pm_tasks(),
        "overdue_tasks": overdue_reminders(),
        "po_followups": purchase_followups(),
        "gate": gate_reminders(),
        "crm": crm_reminders(),
        "sessions_purged": session_cleanup(),
        "approvals_expired": __import__("apps.core.maker_checker", fromlist=["expire_pending"]).expire_pending(),
    }


class Command(BaseCommand):
    help = "Run the background scheduler (PM generation, reminders, auto shift close)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args, **o):
        while True:
            close_old_connections()
            try:
                result = run_once()
                log.info("worker cycle %s", result)
                if o["once"]:
                    self.stdout.write(str(result))
                    return
            except Exception:  # keep the loop alive; errors are logged for monitoring
                log.exception("worker cycle failed")
                if o["once"]:
                    raise
            time.sleep(o["interval"])
