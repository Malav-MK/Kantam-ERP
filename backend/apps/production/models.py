"""Shift production execution (brief p.10), downtime, labour allocation, handover."""
from django.conf import settings
from django.db import models

from apps.core.models import BaseRecord, DocumentRecord, ShiftLock
from apps.masters.models import Machine, Reason


class ProductionReport(DocumentRecord):
    DOC_PREFIX = "PRD"
    STATUS = [("draft", "Draft"), ("submitted", "Submitted / posted"), ("corrected", "Corrected by admin")]
    EXTRA = [("none", "None"), ("allocate", "Allocate to order"), ("stock", "Keep approved stock"), ("rework", "Rework"), ("scrap", "Scrap"), ("pending", "Pending owner disposition")]
    pr = models.ForeignKey("planning.ProductionRequest", on_delete=models.PROTECT, related_name="reports")
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="+")
    shift_date = models.DateField()
    shift = models.CharField(max_length=5, choices=ShiftLock.SHIFTS)
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    operator_name = models.CharField(max_length=120, blank=True)
    supervisor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    actual_start = models.DateTimeField()
    actual_end = models.DateTimeField()
    active_cavities = models.PositiveSmallIntegerField()
    actual_cycle_s = models.DecimalField("Actual cycle (s)", max_digits=6, decimal_places=2)
    total_moulded = models.PositiveIntegerField()
    good_qty = models.PositiveIntegerField("Accepted / good")
    held_qty = models.PositiveIntegerField("QC held", default=0)
    rejected_qty = models.PositiveIntegerField(default=0)
    startup_loss_pcs = models.PositiveIntegerField("Start-up / purge loss (pcs)", default=0)
    defect_reasons = models.CharField(max_length=300, blank=True)
    setup_minutes = models.PositiveIntegerField(default=0)
    label_loss = models.PositiveIntegerField(default=0)
    extra_classification = models.CharField(max_length=10, choices=EXTRA, default="none")
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    good_lot = models.ForeignKey("inventory.Lot", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    held_lot = models.ForeignKey("inventory.Lot", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    rejected_lot = models.ForeignKey("inventory.Lot", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    handover_note = models.TextField(blank=True)
    notes = models.TextField(blank=True)


class Downtime(BaseRecord):
    KINDS = [("machine", "Moulding machine"), ("robot", "IML robot"), ("mould", "Mould"), ("material", "Material shortage"),
             ("quality", "Quality hold"), ("setup", "Setup / changeover"), ("planned", "Planned idle"), ("power", "Power / utility"), ("other", "Other")]
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="downtimes")
    pr = models.ForeignKey("planning.ProductionRequest", null=True, blank=True, on_delete=models.PROTECT, related_name="downtimes")
    kind = models.CharField(max_length=10, choices=KINDS)
    reason = models.ForeignKey(Reason, null=True, blank=True, on_delete=models.PROTECT, limit_choices_to={"kind": "downtime"})
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(null=True, blank=True)
    shift_date = models.DateField()
    shift = models.CharField(max_length=5, choices=ShiftLock.SHIFTS)
    concurrent_with = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, help_text="Overlapping event (e.g. robot during machine stop) - minutes not double-counted")
    breakdown = models.ForeignKey("tasks.Breakdown", null=True, blank=True, on_delete=models.PROTECT, related_name="downtimes")
    notes = models.CharField(max_length=300, blank=True)

    @property
    def minutes(self):
        if not self.end_at:
            return None
        return round((self.end_at - self.start_at).total_seconds() / 60)


class LabourAllocation(BaseRecord):
    ROLES = [("operator", "Operator"), ("helper", "Helper"), ("packer", "Packer"), ("setter", "Setter"), ("supervisor", "Supervisor")]
    person = models.ForeignKey("gate.Person", on_delete=models.PROTECT, related_name="allocations")
    machine = models.ForeignKey(Machine, null=True, blank=True, on_delete=models.PROTECT)
    pr = models.ForeignKey("planning.ProductionRequest", null=True, blank=True, on_delete=models.PROTECT)
    shift_date = models.DateField()
    shift = models.CharField(max_length=5, choices=ShiftLock.SHIFTS)
    role = models.CharField(max_length=10, choices=ROLES)
    hours = models.DecimalField(max_digits=5, decimal_places=2)
    shared = models.BooleanField(default=False, help_text="Shared helper - hours split across machines, never double counted")
    approved = models.BooleanField(default=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")


class ShiftHandover(BaseRecord):
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT)
    shift_date = models.DateField()
    shift = models.CharField(max_length=5, choices=ShiftLock.SHIFTS)
    unfinished = models.TextField(blank=True)
    material_balance = models.TextField(blank=True)
    blockers = models.TextField(blank=True)
    handed_over_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("machine", "shift_date", "shift")]
