"""SFG/FG packing and dispatch (brief p.15-16)."""
from django.conf import settings
from django.db import models

from apps.core.models import BaseRecord, DocumentRecord
from apps.masters.models import Customer, Location, Product


class PackingRecord(DocumentRecord):
    DOC_PREFIX = "PK"
    STATUS = [("posted", "Posted"), ("reversed", "Reversed (unpacked)")]
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    so_line = models.ForeignKey("sales.SOLine", null=True, blank=True, on_delete=models.PROTECT, related_name="packings")
    flavour = models.CharField(max_length=80, blank=True)
    sets_packed = models.PositiveIntegerField()
    packed_lot = models.ForeignKey("inventory.Lot", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    pcs_per_carton = models.PositiveIntegerField(default=0)
    cartons = models.PositiveIntegerField(default=0)
    partial_carton_pcs = models.PositiveIntegerField(default=0)
    bags = models.PositiveIntegerField(default=0)
    packer_name = models.CharField(max_length=120, blank=True)
    consumption = models.JSONField(default=list, help_text="Component lots consumed [{lot, qty, component}]")
    status = models.CharField(max_length=10, choices=STATUS, default="posted")


class Dispatch(DocumentRecord):
    DOC_PREFIX = "DS"
    STATUS = [("planned", "Planned"), ("picked", "Picked"), ("verified", "Packing / QC verified"), ("approved", "Loading approved"),
              ("posted", "Dispatched (posted)"), ("delivered", "Delivered"), ("cancelled", "Cancelled")]
    so = models.ForeignKey("sales.SalesOrder", on_delete=models.PROTECT, related_name="dispatches")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT)
    destination = models.TextField(blank=True)
    planned_date = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS, default="planned", db_index=True)
    transporter = models.CharField(max_length=120, blank=True)
    vehicle_no = models.CharField(max_length=20, blank=True)
    driver_name = models.CharField(max_length=120, blank=True)
    driver_phone = models.CharField(max_length=20, blank=True)
    freight_basis = models.CharField(max_length=60, blank=True)
    invoice_ref = models.CharField(max_length=60, blank=True)
    eway_bill = models.CharField(max_length=40, blank=True)
    loading_checks = models.JSONField(default=dict, blank=True)
    monsoon_tarpaulin_ok = models.BooleanField(default=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    posted_at = models.DateTimeField(null=True, blank=True)
    departed_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_note = models.TextField(blank=True)
    total_weight_kg = models.DecimalField(max_digits=12, decimal_places=2, default=0)


class DispatchLine(BaseRecord):
    dispatch = models.ForeignKey(Dispatch, on_delete=models.PROTECT, related_name="lines")
    so_line = models.ForeignKey("sales.SOLine", on_delete=models.PROTECT, related_name="dispatch_lines")
    lot = models.ForeignKey("inventory.Lot", on_delete=models.PROTECT, related_name="dispatch_lines")
    qty = models.PositiveIntegerField()
    cartons = models.PositiveIntegerField(default=0)


class DeliveryException(BaseRecord):
    KINDS = [("shortage", "Shortage"), ("damage", "Damage"), ("return", "Return"), ("wrong_item", "Wrong item")]
    ACTIONS = [("replacement", "Replacement"), ("credit_note", "Credit note"), ("return", "Return to plant"), ("none", "No action")]
    dispatch = models.ForeignKey(Dispatch, on_delete=models.PROTECT, related_name="exceptions")
    kind = models.CharField(max_length=12, choices=KINDS)
    qty = models.PositiveIntegerField()
    description = models.TextField()
    action = models.CharField(max_length=12, choices=ACTIONS, default="none")
    status = models.CharField(max_length=10, choices=[("open", "Open"), ("resolved", "Resolved")], default="open")
    complaint = models.ForeignKey("quality.Complaint", null=True, blank=True, on_delete=models.PROTECT)
