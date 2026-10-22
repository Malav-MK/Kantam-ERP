"""
Tasks with proof, PM plans, breakdown work orders and incidents (brief p.47-58).

Submitted != Verified: the worker claims completion with evidence; a
*different* authorised reviewer verifies. Reopening starts a new proof round so
old attachments cannot satisfy it.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseRecord, Department, DocumentRecord
from apps.masters.models import Machine, Mould


class Asset(BaseRecord):
    KINDS = [("machine", "Injection machine"), ("mould", "Mould"), ("robot", "IML robot / conveyor"), ("compressor", "Compressor / air"),
             ("granulator", "Grinder / granulator"), ("chiller", "Chiller / cooling"), ("dryer", "Dryer / loader"), ("mtc", "Mould temp. unit"),
             ("electrical", "Electrical / UPS"), ("facility", "Facility / building"), ("safety", "Fire / safety"), ("other", "Unclassified - identify")]
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, choices=KINDS)
    machine = models.ForeignKey(Machine, null=True, blank=True, on_delete=models.PROTECT)
    mould = models.ForeignKey(Mould, null=True, blank=True, on_delete=models.PROTECT)
    make = models.CharField(max_length=60, blank=True)
    model_no = models.CharField("Model", max_length=60, blank=True)
    serial_no = models.CharField(max_length=60, blank=True)
    location = models.CharField(max_length=80, blank=True)
    manual_reference = models.CharField("OEM manual / revision", max_length=200, blank=True)
    service_provider = models.CharField(max_length=120, blank=True)
    warranty_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=[("running", "Running"), ("down", "Down"), ("service", "Under service"), ("inactive", "Inactive")], default="running")

    def __str__(self):
        return f"{self.code} {self.name}"


class Task(DocumentRecord):
    DOC_PREFIX = "TSK"
    STATES = [("assigned", "Assigned"), ("accepted", "Accepted"), ("in_progress", "In progress"), ("blocked", "Blocked"),
              ("submitted", "Submitted - awaiting review"), ("verified", "Verified"), ("reopened", "Reopened"), ("cancelled", "Cancelled")]
    KINDS = [("general", "General"), ("pm", "Preventive maintenance"), ("breakdown", "Breakdown repair"), ("inspection", "Operator inspection"),
             ("calibration", "Calibration"), ("purchase_followup", "Purchase follow-up"), ("qc_containment", "QC containment"),
             ("loading", "Loading checklist"), ("callback", "Call-back / escalation"), ("facility", "Plant / facility")]
    EVIDENCE = [("none", "None"), ("photo", "Photo"), ("report", "Report / reading"), ("photo_or_report", "Photo or report")]
    PRIORITY = [("low", "Low"), ("normal", "Normal"), ("high", "High"), ("critical", "Critical")]
    title = models.CharField(max_length=200)
    instructions = models.TextField(blank=True)
    kind = models.CharField(max_length=18, choices=KINDS, default="general")
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasks")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="tasks_to_review")
    source_resource = models.CharField(max_length=60, blank=True)
    source_id = models.CharField(max_length=64, blank=True)
    source_label = models.CharField(max_length=120, blank=True)
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.PROTECT, related_name="tasks")
    priority = models.CharField(max_length=10, choices=PRIORITY, default="normal")
    due_at = models.DateTimeField("Current due")
    original_due_at = models.DateTimeField("Original due", null=True, blank=True, help_text="Set once at creation; revisions never overwrite it")
    due_revisions = models.JSONField(default=list, blank=True, help_text='[{"from","to","reason","by","at"}]')
    expected_minutes = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=12, choices=STATES, default="assigned", db_index=True)
    evidence_required = models.CharField(max_length=16, choices=EVIDENCE, default="none")
    checklist = models.JSONField(default=list, blank=True, help_text='[{"id","text","critical","kind":"yes_no|reading","limit"}] - frozen template revision')
    checklist_revision = models.CharField(max_length=20, blank=True)
    answers = models.JSONField(default=dict, blank=True)
    proof_round = models.PositiveSmallIntegerField(default=1)
    blocker_reason = models.CharField(max_length=40, blank=True, choices=[
        ("access", "Machine access"), ("permit", "Isolation permit"), ("spare", "Spare part"), ("vendor", "Vendor visit"),
        ("slot", "Production slot"), ("instruction", "Unclear instruction"), ("failed_test", "Failed test"), ("other", "Other")])
    blocker_note = models.CharField(max_length=300, blank=True)
    report_text = models.TextField(blank=True, help_text="Report / readings when a safe photo is impractical")
    photo_exception_reason = models.CharField(max_length=300, blank=True)
    completion_note = models.TextField(blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    review_note = models.TextField(blank=True)
    actual_minutes = models.PositiveIntegerField(default=0)
    pm_plan = models.ForeignKey("PMPlan", null=True, blank=True, on_delete=models.PROTECT, related_name="occurrences")
    occurrence_key = models.CharField(max_length=120, null=True, blank=True, unique=True, help_text="Stable key; prevents duplicate generation")
    overdue_notified = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if self.original_due_at is None:
            self.original_due_at = self.due_at
        super().save(*args, **kwargs)

    @property
    def late_vs_original(self):
        """Lateness is measured against the original commitment, never erased by a revision."""
        if not self.original_due_at:
            return False
        done = self.submitted_at or self.verified_at
        return (done or timezone.now()) > self.original_due_at and self.state != "cancelled"

    def __str__(self):
        return f"{self.doc_no} {self.title}"


class PMPlan(DocumentRecord):
    DOC_PREFIX = "PM"
    INTERVALS = [("12h", "Every 12 hours"), ("24h", "Every 24 hours"), ("weekly", "Weekly"), ("15d", "Every 15 days"), ("30d", "Every 30 days"),
                 ("quarterly", "Quarterly"), ("half_yearly", "Half yearly"), ("yearly", "Yearly")]
    STATUS = [("draft", "Draft - inactive"), ("approved", "Approved - active"), ("inactive", "Inactive")]
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="pm_plans")
    title = models.CharField(max_length=200)
    interval = models.CharField(max_length=12, choices=INTERVALS)
    recurrence_basis = models.CharField(max_length=12, choices=[("fixed", "Fixed calendar"), ("after_done", "Next due after verified service")], default="fixed")
    checklist = models.JSONField(default=list)
    checklist_revision = models.CharField(max_length=20, default="1")
    oem_reference = models.CharField("OEM manual / revision / page", max_length=200, blank=True)
    competence = models.CharField(max_length=120, blank=True)
    permit_required = models.BooleanField(default=False)
    evidence_required = models.CharField(max_length=16, choices=Task.EVIDENCE, default="photo_or_report")
    expected_minutes = models.PositiveIntegerField(default=30)
    window_hours = models.PositiveIntegerField(default=12, help_text="Planning window before due")
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    next_due = models.DateTimeField()
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")


class Breakdown(DocumentRecord):
    DOC_PREFIX = "BD"
    STATUS = [("reported", "Reported"), ("acknowledged", "Acknowledged / triage"), ("diagnosis", "Diagnosis"), ("awaiting_spares", "Awaiting spares / service"),
              ("repair", "Repair"), ("testing", "Test / measure"), ("restart_approved", "Restart approved"), ("closed", "Closed - effectiveness verified")]
    SEVERITY = [("minor", "Minor"), ("major", "Major - production stopped"), ("safety", "Safety critical")]
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="breakdowns")
    machine = models.ForeignKey(Machine, null=True, blank=True, on_delete=models.PROTECT)
    pr = models.ForeignKey("planning.ProductionRequest", null=True, blank=True, on_delete=models.PROTECT, related_name="breakdowns")
    severity = models.CharField(max_length=8, choices=SEVERITY, default="major")
    symptoms = models.TextField()
    alarm_codes = models.CharField(max_length=120, blank=True)
    failure_at = models.DateTimeField()
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    repair_start_at = models.DateTimeField(null=True, blank=True)
    repair_end_at = models.DateTimeField(null=True, blank=True)
    restart_at = models.DateTimeField(null=True, blank=True)
    technician = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    suspected_cause = models.TextField(blank=True)
    confirmed_cause = models.TextField(blank=True)
    action_taken = models.TextField(blank=True)
    parts_used = models.TextField(blank=True)
    repair_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    permit_ref = models.CharField(max_length=60, blank=True)
    test_result = models.TextField(blank=True)
    first_piece_inspection = models.ForeignKey("quality.Inspection", null=True, blank=True, on_delete=models.PROTECT)
    restart_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=16, choices=STATUS, default="reported", db_index=True)
    recurrence_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)


class Incident(DocumentRecord):
    DOC_PREFIX = "INC"
    STATUS = [("reported", "Reported"), ("contained", "Contained"), ("assigned", "Assigned"), ("investigation", "Investigation / action"),
              ("verification", "Verification"), ("closed", "Closed")]
    TYPES = [("safety", "Safety"), ("operational", "Operational"), ("security", "Security"), ("environment", "Environment"), ("quality", "Quality")]
    kind = models.CharField(max_length=12, choices=TYPES)
    severity = models.CharField(max_length=10, choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")])
    title = models.CharField(max_length=200)
    description = models.TextField()
    occurred_at = models.DateTimeField()
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.PROTECT)
    immediate_response = models.TextField(blank=True)
    accountable = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    due_date = models.DateField(null=True, blank=True)
    root_cause = models.TextField(blank=True)
    action = models.TextField(blank=True)
    verification = models.TextField(blank=True)
    recurrence_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    sensitive = models.BooleanField(default=False, help_text="Personnel details - restricted visibility")
    status = models.CharField(max_length=14, choices=STATUS, default="reported", db_index=True)
