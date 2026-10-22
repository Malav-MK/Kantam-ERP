"""
Material ledger (brief p.11-12).

* Balances derive from dated, immutable movements. Posted movements are never
  edited or deleted; corrections are linked reversals/adjustments.
* Quality state lives on the lot: only `released` lots are usable stock;
  pending/held/rejected stock is excluded from availability.
* Available-to-allocate = usable on-hand - active reservations.
"""
from django.conf import settings
from django.db import models

from apps.core.models import BaseRecord, DocumentRecord
from apps.masters.models import COMPONENT_TYPES, Artwork, Item, Location, Product

QUALITY_STATES = [
    ("pending", "Awaiting QC"), ("released", "Released / usable"), ("held", "QC hold"),
    ("rework", "Rework"), ("rejected", "Rejected"), ("blocked", "Blocked / obsolete"),
]


class Lot(DocumentRecord):
    DOC_PREFIX = "LOT"
    SOURCES = [("grn", "Goods receipt"), ("production", "Production"), ("packing", "Packing"), ("opening", "Opening balance"),
               ("split", "Split"), ("adjustment", "Adjustment"), ("return", "Customer return")]
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="lots")
    artwork = models.ForeignKey(Artwork, null=True, blank=True, on_delete=models.PROTECT, related_name="lots")
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.PROTECT)
    component_type = models.CharField(max_length=10, choices=COMPONENT_TYPES, blank=True)
    flavour = models.CharField(max_length=80, blank=True)
    supplier_lot = models.CharField(max_length=80, blank=True)
    source = models.CharField(max_length=12, choices=SOURCES)
    quality_state = models.CharField(max_length=10, choices=QUALITY_STATES, default="pending", db_index=True)
    pr = models.ForeignKey("planning.ProductionRequest", null=True, blank=True, on_delete=models.PROTECT, related_name="output_lots")
    grn_line = models.ForeignKey("purchase.GRNLine", null=True, blank=True, on_delete=models.PROTECT, related_name="lots")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="splits")
    produced_at = models.DateTimeField(null=True, blank=True)
    shift_date = models.DateField(null=True, blank=True)
    shift = models.CharField(max_length=5, blank=True)
    excess = models.BooleanField(default=False, help_text="Approved excess production awaiting disposition")
    notes = models.CharField(max_length=300, blank=True)

    def __str__(self):
        return f"{self.doc_no} {self.item.code}"


class LotGenealogy(models.Model):
    """Component/material lots that went into a child lot (packing, production)."""

    parent = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="consumed_into")
    child = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="made_from")
    qty = models.DecimalField(max_digits=16, decimal_places=3)
    relation = models.CharField(max_length=20, default="consumed")


class StockBalance(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="balances")
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="balances")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="balances")
    qty = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("item", "lot", "location")]
        indexes = [models.Index(fields=["item", "location"])]
        constraints = [models.CheckConstraint(condition=models.Q(qty__gte=0), name="stock_balance_non_negative")]


class StockMove(DocumentRecord):
    DOC_PREFIX = "SM"
    TYPES = [
        ("receipt", "Receipt (GRN)"), ("opening", "Opening balance"), ("issue", "Issue to machine"), ("return", "Return from machine"),
        ("transfer", "Location transfer"), ("consumption", "Production consumption"), ("production", "Production output"),
        ("scrap", "Scrap"), ("supplier_return", "Supplier return"), ("adjustment", "Authorised adjustment"),
        ("pack_consume", "Packing - component consumed"), ("pack_output", "Packing - set produced"),
        ("dispatch", "Dispatch to customer"), ("customer_return", "Customer return"), ("reversal", "Reversal"),
        ("quality", "Quality state split"),
    ]
    move_type = models.CharField(max_length=16, choices=TYPES, db_index=True)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT, related_name="moves")
    from_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    to_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    qty = models.DecimalField(max_digits=16, decimal_places=3)
    uom = models.CharField(max_length=5)
    pr = models.ForeignKey("planning.ProductionRequest", null=True, blank=True, on_delete=models.PROTECT, related_name="stock_moves")
    so_line = models.ForeignKey("sales.SOLine", null=True, blank=True, on_delete=models.PROTECT, related_name="stock_moves")
    ref_resource = models.CharField(max_length=60, blank=True)
    ref_id = models.CharField(max_length=64, blank=True)
    ref_doc = models.CharField(max_length=40, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    reversal_of = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by")
    event_time = models.DateTimeField()
    shift_date = models.DateField(null=True, blank=True)
    shift = models.CharField(max_length=5, blank=True)
    issuer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    receiver_name = models.CharField(max_length=120, blank=True)

    class Meta:
        indexes = [models.Index(fields=["item", "event_time"]), models.Index(fields=["ref_resource", "ref_id"])]


class Reservation(BaseRecord):
    STATUS = [("active", "Active"), ("consumed", "Consumed"), ("released", "Released")]
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="reservations")
    lot = models.ForeignKey(Lot, null=True, blank=True, on_delete=models.PROTECT, related_name="reservations")
    qty = models.DecimalField(max_digits=16, decimal_places=3)
    consumed_qty = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    so_line = models.ForeignKey("sales.SOLine", null=True, blank=True, on_delete=models.PROTECT, related_name="reservations")
    pr = models.ForeignKey("planning.ProductionRequest", null=True, blank=True, on_delete=models.PROTECT, related_name="reservations")
    status = models.CharField(max_length=10, choices=STATUS, default="active", db_index=True)
    note = models.CharField(max_length=200, blank=True)

    @property
    def open_qty(self):
        return self.qty - self.consumed_qty


class StockCount(DocumentRecord):
    DOC_PREFIX = "SC"
    STATUS = [("draft", "Draft"), ("submitted", "Submitted for approval"), ("approved", "Approved & posted"), ("rejected", "Rejected")]
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    lot = models.ForeignKey(Lot, on_delete=models.PROTECT)
    system_qty = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    counted_qty = models.DecimalField(max_digits=16, decimal_places=3)
    variance = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    status = models.CharField(max_length=10, choices=STATUS, default="draft")
    reason = models.CharField(max_length=300, blank=True)
    adjustment_move = models.ForeignKey(StockMove, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
