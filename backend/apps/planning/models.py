"""
Monthly planning, plan lines with baseline/revision/actual, and shift PRs.

Brief p.9: monthly plan stays editable; original approved baseline is kept and
revisions/actuals are shown separately. Brief p.10: a PR is the released
instruction for one machine / 12-hour shift with recipe & artwork snapshots.
"""
from django.conf import settings
from django.db import models

from apps.core.models import BaseRecord, DocumentRecord, ShiftLock
from apps.masters.models import Artwork, Item, Machine, Mould, ProductComponent, Recipe


class MonthlyPlan(DocumentRecord):
    DOC_PREFIX = "MP"
    STATUS = [("draft", "Draft"), ("approved", "Approved (baseline kept)")]
    month = models.CharField(max_length=7, help_text="YYYY-MM")
    status = models.CharField(max_length=10, choices=STATUS, default="draft")
    baseline = models.JSONField(default=dict, blank=True)
    baseline_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    revision = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.doc_no} {self.month}"


class PlanLine(BaseRecord):
    STATUS = [("planned", "Planned"), ("released", "Released"), ("running", "Running"), ("stopped", "Stopped"),
              ("done", "Completed"), ("cancelled", "Cancelled")]
    plan = models.ForeignKey(MonthlyPlan, on_delete=models.PROTECT, related_name="lines")
    demand = models.ForeignKey("sales.ComponentDemand", null=True, blank=True, on_delete=models.PROTECT, related_name="plan_lines")
    stock_production = models.BooleanField(default=False, help_text="Separately approved stock-production job (no SO line)")
    stock_approval_ref = models.CharField(max_length=60, blank=True)
    component = models.ForeignKey(ProductComponent, on_delete=models.PROTECT)
    artwork = models.ForeignKey(Artwork, null=True, blank=True, on_delete=models.PROTECT)
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="plan_lines")
    mould = models.ForeignKey(Mould, on_delete=models.PROTECT, related_name="plan_lines")
    sequence = models.PositiveIntegerField(default=10, help_text="Order on the machine; drives forecast start/finish")
    planned_qty = models.PositiveIntegerField("Good qty target")
    expected_yield_pct = models.DecimalField(max_digits=5, decimal_places=2, default=100)
    cycle_s = models.DecimalField("Planning cycle (s)", max_digits=6, decimal_places=2)
    active_cavities = models.PositiveSmallIntegerField()
    setup_minutes = models.PositiveIntegerField(default=0)
    earliest_start = models.DateTimeField(null=True, blank=True)
    forecast_start = models.DateTimeField(null=True, blank=True)
    forecast_finish = models.DateTimeField(null=True, blank=True)
    # Baseline frozen at plan approval
    baseline_qty = models.PositiveIntegerField(null=True, blank=True)
    baseline_cycle_s = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    baseline_finish = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="planned", db_index=True)
    revision = models.PositiveIntegerField(default=0)
    readiness = models.JSONField(default=dict, blank=True)
    at_risk = models.BooleanField(default=False)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["machine", "sequence"]

    def __str__(self):
        return f"{self.machine.code} #{self.sequence} {self.component}"


class PlanRevision(BaseRecord):
    KINDS = [("cycle", "Cycle change"), ("quantity", "Quantity change"), ("stop", "Stop"), ("continue", "Continue"),
             ("urgent", "Urgent insertion"), ("cancel", "Cancellation"), ("resequence", "Resequence"), ("machine", "Machine change"), ("other", "Other")]
    plan_line = models.ForeignKey(PlanLine, on_delete=models.PROTECT, related_name="revisions")
    revision = models.PositiveIntegerField()
    kind = models.CharField(max_length=12, choices=KINDS)
    changes = models.JSONField()
    reason = models.TextField()
    impact = models.JSONField(default=dict)


class ProductionRequest(DocumentRecord):
    DOC_PREFIX = "PR"
    STATUS = [("draft", "Draft (blocked allowed)"), ("released", "Released"), ("in_progress", "In progress"), ("closed", "Closed"),
              ("cancelled", "Cancelled")]
    SHIFTS = ShiftLock.SHIFTS
    plan_line = models.ForeignKey(PlanLine, on_delete=models.PROTECT, related_name="prs")
    so_line = models.ForeignKey("sales.SOLine", null=True, blank=True, on_delete=models.PROTECT, related_name="prs")
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="prs")
    mould = models.ForeignKey(Mould, on_delete=models.PROTECT, related_name="prs")
    component = models.ForeignKey(ProductComponent, on_delete=models.PROTECT)
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="+")
    artwork = models.ForeignKey(Artwork, null=True, blank=True, on_delete=models.PROTECT, related_name="prs")
    recipe = models.ForeignKey(Recipe, null=True, blank=True, on_delete=models.PROTECT)
    shift_date = models.DateField()
    shift = models.CharField(max_length=5, choices=SHIFTS)
    segment_start = models.DateTimeField()
    segment_end = models.DateTimeField()
    planned_pcs = models.PositiveIntegerField("Planned moulded pcs")
    active_cavities = models.PositiveSmallIntegerField()
    cycle_s = models.DecimalField(max_digits=6, decimal_places=2)
    snapshot = models.JSONField(default=dict, help_text="Recipe/artwork/packing snapshot frozen at release")
    readiness = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="draft", db_index=True)
    parent_pr = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reissues")
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    released_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True)
    handover_note = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["machine", "shift_date", "shift"])]

    def __str__(self):
        return f"{self.doc_no} {self.machine.code} {self.shift_date} {self.shift}"


class PRMaterial(BaseRecord):
    pr = models.ForeignKey(ProductionRequest, on_delete=models.PROTECT, related_name="materials")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20)
    artwork = models.ForeignKey(Artwork, null=True, blank=True, on_delete=models.PROTECT)
    required_qty = models.DecimalField(max_digits=14, decimal_places=3)
    uom = models.CharField(max_length=5)
    issued_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    returned_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    consumed_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    basis = models.JSONField(default=dict, help_text="Calculation inputs shown to the user")

    class Meta:
        unique_together = [("pr", "item")]

    @property
    def net_issued(self):
        return self.issued_qty - self.returned_qty

    @property
    def pending_issue(self):
        return max(self.required_qty - self.net_issued, 0)

    @property
    def machine_side_balance(self):
        return self.net_issued - self.consumed_qty
