"""Purchase (brief p.13): requisition -> PO with staged lots -> GRN -> QC acceptance."""
from django.conf import settings
from django.db import models

from apps.core.models import BaseRecord, DocumentRecord
from apps.masters.models import Artwork, Item, Location, Supplier


class Requisition(DocumentRecord):
    DOC_PREFIX = "MR"
    STATUS = [("draft", "Draft"), ("submitted", "Submitted"), ("approved", "Approved"), ("ordered", "Ordered"), ("rejected", "Rejected"), ("cancelled", "Cancelled")]
    URGENCY = [("normal", "Normal"), ("urgent", "Urgent"), ("critical", "Critical - production stop risk")]
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    qty = models.DecimalField(max_digits=14, decimal_places=3)
    needed_date = models.DateField()
    source_demand = models.CharField(max_length=200, blank=True, help_text="PR / plan / maintenance / shortage reference")
    stock_basis = models.JSONField(default=dict, blank=True, help_text="Usable / reserved / incoming at time of request")
    urgency = models.CharField(max_length=10, choices=URGENCY, default="normal")
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    notes = models.TextField(blank=True)


class PurchaseOrder(DocumentRecord):
    DOC_PREFIX = "PO"
    STATUS = [("draft", "Draft"), ("pending_approval", "Pending approval"), ("approved", "Approved"), ("sent", "Sent to supplier"),
              ("partial", "Partially received"), ("closed", "Closed"), ("cancelled", "Cancelled")]
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="orders")
    order_date = models.DateField()
    status = models.CharField(max_length=18, choices=STATUS, default="draft", db_index=True)
    terms = models.CharField(max_length=200, blank=True)
    freight_charges = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    supplier_confirmed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    @property
    def total_value(self):
        return sum((line.qty * line.rate for line in self.lines.all()), 0)


class POLine(BaseRecord):
    STATUS = [("open", "Open"), ("partial", "Partial"), ("received", "Fully received"), ("closed", "Short-closed"), ("cancelled", "Cancelled")]
    po = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="lines")
    line_no = models.PositiveSmallIntegerField()
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    artwork = models.ForeignKey(Artwork, null=True, blank=True, on_delete=models.PROTECT, help_text="Required for label purchases")
    requisition = models.ForeignKey(Requisition, null=True, blank=True, on_delete=models.PROTECT, related_name="po_lines")
    qty = models.DecimalField(max_digits=14, decimal_places=3)
    rate = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    promised_date = models.DateField("Current supplier promise")
    original_promised_date = models.DateField("Original supplier promise", null=True, blank=True,
                                              help_text="Frozen when the PO leaves draft; later promises never overwrite it")
    promise_revisions = models.JSONField(default=list, blank=True, help_text='[{"from","to","reason","by","at"}]')
    received_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    accepted_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    rejected_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    status = models.CharField(max_length=10, choices=STATUS, default="open", db_index=True)
    follow_up_date = models.DateField(null=True, blank=True)
    follow_up_owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    overdue_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = [("po", "line_no")]
        ordering = ["po", "line_no"]

    def save(self, *args, **kwargs):
        if self.original_promised_date is None or self.po.status == "draft":
            self.original_promised_date = self.promised_date
        super().save(*args, **kwargs)

    @property
    def balance_qty(self):
        return max(self.qty - self.accepted_qty, 0)

    @property
    def slip_days(self):
        """Days the current promise has slipped past the original commitment."""
        if not self.original_promised_date:
            return 0
        return max((self.promised_date - self.original_promised_date).days, 0)

    def __str__(self):
        return f"{self.po.doc_no}/{self.line_no} {self.item.code}"


class GRN(DocumentRecord):
    DOC_PREFIX = "GRN"
    STATUS = [("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")]
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT)
    gate_entry = models.ForeignKey("gate.GateMaterial", null=True, blank=True, on_delete=models.PROTECT, related_name="grns")
    invoice_no = models.CharField(max_length=60)
    invoice_date = models.DateField(null=True, blank=True)
    duplicate_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                     help_text="Another GRN already uses this supplier invoice number")
    duplicate_note = models.CharField("Reason invoice is reused", max_length=300, blank=True,
                                      help_text="Only when one supplier invoice genuinely covers several receipts (e.g. split trucks)")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, help_text="Receipt location")
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    @staticmethod
    def normalise_invoice(no):
        """'INV/0042 ', 'inv-42' and 'INV 0042' are the same invoice for duplicate checks."""
        import re
        cleaned = re.sub(r"[^0-9A-Z]", "", (no or "").upper())
        return re.sub(r"(?<![0-9])0+(?=[0-9])", "", cleaned)


class GRNLine(BaseRecord):
    QC = [("pending", "Awaiting QC"), ("decided", "QC decided")]
    grn = models.ForeignKey(GRN, on_delete=models.PROTECT, related_name="lines")
    po_line = models.ForeignKey(POLine, null=True, blank=True, on_delete=models.PROTECT, related_name="grn_lines")
    gate_line = models.ForeignKey("gate.GateMaterialLine", null=True, blank=True, on_delete=models.PROTECT, related_name="grn_lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    artwork = models.ForeignKey(Artwork, null=True, blank=True, on_delete=models.PROTECT)
    supplier_lot = models.CharField(max_length=80, blank=True)
    declared_qty = models.DecimalField("Supplier declared qty", max_digits=14, decimal_places=3)
    received_qty = models.DecimalField("Stores received (counted/weighed)", max_digits=14, decimal_places=3)
    accepted_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    held_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    rejected_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    qc_status = models.CharField(max_length=10, choices=QC, default="pending")
    lot = models.ForeignKey("inventory.Lot", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    discrepancy = models.CharField(max_length=300, blank=True)
