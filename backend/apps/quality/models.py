"""Quality: inspections with explicit dispositions, complaints & CAPA (brief p.14)."""
from django.conf import settings
from django.db import models

from apps.core.models import DocumentRecord
from apps.masters.models import Customer, Product


class Inspection(DocumentRecord):
    DOC_PREFIX = "QI"
    KINDS = [("incoming", "Incoming material / label"), ("first_piece", "Start-up / first piece"), ("in_process", "In-process"),
             ("final", "Final release"), ("restart", "Restart after maintenance")]
    STATUS = [("draft", "Draft"), ("decided", "Disposition posted")]
    kind = models.CharField(max_length=12, choices=KINDS)
    lot = models.ForeignKey("inventory.Lot", null=True, blank=True, on_delete=models.PROTECT, related_name="inspections")
    pr = models.ForeignKey("planning.ProductionRequest", null=True, blank=True, on_delete=models.PROTECT, related_name="inspections")
    grn_line = models.ForeignKey("purchase.GRNLine", null=True, blank=True, on_delete=models.PROTECT, related_name="inspections")
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    spec_revision = models.CharField(max_length=40, blank=True)
    sample_size = models.PositiveIntegerField(default=0)
    results = models.JSONField(default=list, blank=True, help_text='[{"parameter","component","spec","actual","unit","pass"}] - container and lid kept separate')
    checklist = models.JSONField(default=dict, blank=True)
    remarks = models.TextField(blank=True)
    qty_release = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    qty_hold = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    qty_reject = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    qty_rework = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)


class Complaint(DocumentRecord):
    DOC_PREFIX = "CC"
    KINDS = [("foreign", "Foreign material / hair"), ("insect", "Insects"), ("fit", "Lid fit / warpage"), ("mixed", "Mixed / unprinted products"),
             ("label_join", "Label join / alignment"), ("carton", "Wet / damaged cartons"), ("dimension", "Dimension / weight"), ("other", "Other")]
    SEVERITY = [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")]
    STATUS = [("reported", "Reported"), ("contained", "Contained"), ("investigation", "Investigation / RCA"), ("action", "Corrective action"),
              ("verification", "Effectiveness verification"), ("closed", "Closed")]
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="complaints")
    dispatch = models.ForeignKey("fg.Dispatch", null=True, blank=True, on_delete=models.PROTECT, related_name="complaints")
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.PROTECT)
    lots = models.ManyToManyField("inventory.Lot", blank=True, related_name="complaints")
    complaint_date = models.DateField()
    kind = models.CharField(max_length=12, choices=KINDS)
    severity = models.CharField(max_length=10, choices=SEVERITY, default="medium")
    qty_affected = models.PositiveIntegerField(default=0)
    description = models.TextField()
    status = models.CharField(max_length=14, choices=STATUS, default="reported", db_index=True)
    containment = models.TextField(blank=True)
    root_cause = models.TextField(blank=True)
    corrective_action = models.TextField(blank=True)
    preventive_action = models.TextField(blank=True)
    effectiveness_review = models.TextField(blank=True)
    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    due_date = models.DateField(null=True, blank=True)
    replacement_required = models.BooleanField(default=False)
    response_reference = models.CharField(max_length=120, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
