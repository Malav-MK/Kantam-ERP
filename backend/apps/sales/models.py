"""Sales orders and demand (brief p.7). Linked at SO-line level, never only at header."""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseRecord, DocumentRecord
from apps.masters.models import COMPONENT_TYPES, Artwork, Customer, Item, PackingRule, Product, ProductComponent


class SalesOrder(DocumentRecord):
    DOC_PREFIX = "SO"
    STATUS = [("draft", "Draft"), ("confirmed", "Confirmed"), ("closed", "Closed"), ("cancelled", "Cancelled")]
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders")
    customer_po_no = models.CharField("Customer PO no.", max_length=60, blank=True)
    customer_po_date = models.DateField("Customer PO date", null=True, blank=True)
    order_date = models.DateField(default=timezone.localdate)
    salesperson = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    billing_address = models.TextField(blank=True)
    delivery_address = models.TextField(blank=True)
    payment_terms = models.CharField(max_length=120, blank=True)
    freight_basis = models.CharField(max_length=60, blank=True)
    advance_required_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    advance_received = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    commercial_approved = models.BooleanField(default=False)
    credit_released = models.BooleanField(default=False, help_text="Accounts release for dispatch against credit")
    quotation = models.ForeignKey("crm.Quotation", null=True, blank=True, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    version = models.PositiveIntegerField(default=1)
    locked_at = models.DateTimeField(null=True, blank=True, help_text="Server time 24h after creation")
    cancel_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        if not self.locked_at:
            self.locked_at = timezone.now() + timedelta(hours=settings.KANTAM["SO_LOCK_HOURS"])
        super().save(*args, **kwargs)

    @property
    def is_locked(self):
        return self.locked_at is not None and timezone.now() >= self.locked_at

    @property
    def order_value(self):
        return sum((line.qty * line.unit_price for line in self.lines.exclude(status="cancelled")), 0)


class SOLine(BaseRecord):
    SCOPES = [("set", "Complete set"), ("container", "Container only"), ("lid", "Lid only"), ("handle", "Handle only")]
    STATUS = [("open", "Open"), ("closed", "Closed"), ("cancelled", "Cancelled")]
    so = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="lines")
    line_no = models.PositiveSmallIntegerField()
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    scope = models.CharField(max_length=10, choices=SCOPES, default="set")
    flavour = models.CharField(max_length=80, blank=True)
    container_artwork = models.ForeignKey(Artwork, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    lid_artwork = models.ForeignKey(Artwork, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    container_colour = models.CharField(max_length=60, blank=True)
    lid_colour = models.CharField(max_length=60, blank=True)
    masterbatch = models.ForeignKey(Item, null=True, blank=True, on_delete=models.PROTECT, related_name="+", help_text="Masterbatch colour follows the SO")
    impact_modifier = models.BooleanField(default=False)
    iml = models.BooleanField("IML", default=True)
    qty = models.PositiveIntegerField(help_text="Sets for complete-set lines, pieces for component-only lines")
    unit_price = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    requested_date = models.DateField(null=True, blank=True)
    committed_date = models.DateField(null=True, blank=True)
    packing_rule = models.ForeignKey(PackingRule, null=True, blank=True, on_delete=models.PROTECT)
    priority = models.PositiveSmallIntegerField(default=3)
    special_instructions = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="open", db_index=True)
    cancelled_qty = models.PositiveIntegerField(default=0)
    dispatched_qty = models.PositiveIntegerField(default=0)
    at_risk = models.BooleanField(default=False)
    risk_note = models.CharField(max_length=300, blank=True)

    class Meta:
        unique_together = [("so", "line_no")]
        ordering = ["so", "line_no"]

    def __str__(self):
        flavour = f" {self.flavour}" if self.flavour else ""
        return f"{self.so.doc_no}/{self.line_no} {self.product.code}{flavour}"

    @property
    def balance_qty(self):
        return max(self.qty - self.cancelled_qty - self.dispatched_qty, 0)


class ComponentDemand(BaseRecord):
    """Component requirement generated from an SO line through the product BOM."""

    so_line = models.ForeignKey(SOLine, on_delete=models.PROTECT, related_name="demands")
    component = models.ForeignKey(ProductComponent, on_delete=models.PROTECT)
    component_type = models.CharField(max_length=10, choices=COMPONENT_TYPES)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    artwork = models.ForeignKey(Artwork, null=True, blank=True, on_delete=models.PROTECT)
    required_qty = models.PositiveIntegerField()
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("so_line", "component")]

    def __str__(self):
        return f"{self.so_line} {self.component_type}"


class SOVersion(BaseRecord):
    so = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    reason = models.TextField(blank=True)
    approval = models.ForeignKey("core.ApprovalRequest", null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        unique_together = [("so", "version")]
