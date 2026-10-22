"""Sales & marketing CRM (brief p.6)."""
from django.conf import settings
from django.db import models

from apps.core.models import BaseRecord, DocumentRecord
from apps.masters.models import Artwork, Customer, Product

STAGES = [
    ("new", "New lead"), ("contacted", "Contacted"), ("requirement", "Requirement understood"), ("sample", "Sample sent"),
    ("quotation", "Quotation sent"), ("negotiation", "Negotiation"), ("won", "Won"), ("lost", "Lost"), ("on_hold", "On hold"),
]


class Lead(DocumentRecord):
    DOC_PREFIX = "LD"
    company = models.CharField(max_length=200)
    industry = models.CharField(max_length=80, blank=True)
    location = models.CharField(max_length=120, blank=True)
    POWER_SOURCES = [("paid", "P - Paid"), ("organic", "O - Organic"), ("word_of_mouth", "W - Word of mouth / referral"),
                     ("external_partner", "E - External partner"), ("reach_out", "R - Reach out")]
    power_source = models.CharField("Original source (POWER)", max_length=16, choices=POWER_SOURCES, blank=True, db_index=True,
                                    help_text="How the company first reached Kantam. Kept unchanged; later touchpoints go in activities.")
    source = models.CharField("Source detail", max_length=60, blank=True, help_text="Exhibition name, referrer, campaign, partner ...")
    contact_name = models.CharField(max_length=120, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)
    potential_products = models.CharField(max_length=300, blank=True)
    potential_qty_per_month = models.PositiveIntegerField(default=0)
    buying_frequency = models.CharField(max_length=40, blank=True)
    expected_buy_date = models.DateField(null=True, blank=True)
    salesperson = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    stage = models.CharField(max_length=12, choices=STAGES, default="new", db_index=True)
    priority_score = models.PositiveSmallIntegerField(default=0, help_text="Transparent configurable score (quantity, sector, stage, buying date)")
    next_action = models.CharField(max_length=200, blank=True)
    next_action_date = models.DateField(null=True, blank=True)
    lost_reason = models.CharField(max_length=200, blank=True)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, help_text="Linked once the lead becomes a customer")

    def __str__(self):
        return f"{self.doc_no} {self.company}"


class Activity(BaseRecord):
    KINDS = [("call", "Call"), ("meeting", "Meeting"), ("visit", "Customer visit"), ("email", "Email"), ("note", "Note")]
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.PROTECT, related_name="activities")
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="activities")
    kind = models.CharField(max_length=10, choices=KINDS)
    subject = models.CharField(max_length=200)
    at = models.DateTimeField()
    outcome = models.TextField(blank=True)
    next_follow_up = models.DateField(null=True, blank=True)
    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    done = models.BooleanField(default=False)

    def __str__(self):
        return self.subject


class Sample(DocumentRecord):
    DOC_PREFIX = "SMP"
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.PROTECT, related_name="samples")
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="samples")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    artwork = models.ForeignKey(Artwork, null=True, blank=True, on_delete=models.PROTECT)
    qty = models.PositiveIntegerField()
    sent_date = models.DateField(null=True, blank=True)
    shipment_ref = models.CharField(max_length=80, blank=True)
    received_by_customer = models.DateField(null=True, blank=True)
    feedback = models.TextField(blank=True)
    next_action = models.CharField(max_length=200, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=12, choices=[("planned", "Planned"), ("sent", "Sent"), ("received", "Received"), ("feedback", "Feedback received"), ("closed", "Closed")], default="planned")


class Quotation(DocumentRecord):
    DOC_PREFIX = "QT"
    STATUS = [("draft", "Draft"), ("pending_approval", "Pending approval"), ("approved", "Approved"), ("sent", "Sent"),
              ("accepted", "Accepted"), ("rejected", "Rejected"), ("expired", "Expired"), ("superseded", "Superseded"), ("converted", "Converted to SO")]
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.PROTECT, related_name="quotations")
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="quotations")
    revision = models.PositiveSmallIntegerField(default=1)
    previous = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="revisions")
    status = models.CharField(max_length=18, choices=STATUS, default="draft", db_index=True)
    valid_until = models.DateField()
    payment_terms = models.CharField(max_length=120, default="50% advance, balance before dispatch")
    freight_basis = models.CharField(max_length=60, default="Ex-Works Bareja")
    PRICE_BASIS = [("excl_gst", "Prices exclude GST"), ("incl_gst", "Prices include GST")]
    price_basis = models.CharField("Price basis", max_length=8, choices=PRICE_BASIS, default="excl_gst")
    gst_rate_pct = models.DecimalField("GST rate %", max_digits=5, decimal_places=2, default=18)
    block_charges = models.DecimalField("Design / block charges", max_digits=12, decimal_places=2, default=0)
    delivery_lead_days = models.PositiveIntegerField(default=21)
    min_margin_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    sent_at = models.DateTimeField(null=True, blank=True)
    snapshot = models.JSONField(default=dict, blank=True, help_text="Exact content as sent")
    notes = models.TextField(blank=True)


class QuotationLine(BaseRecord):
    SCOPES = [("set", "Complete set"), ("container", "Container only"), ("lid", "Lid only"), ("handle", "Handle only")]
    quotation = models.ForeignKey(Quotation, on_delete=models.PROTECT, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    scope = models.CharField(max_length=10, choices=SCOPES, default="set")
    flavour = models.CharField(max_length=80, blank=True)
    specification = models.CharField(max_length=300, blank=True)
    qty_from = models.PositiveIntegerField("Quantity slab from", default=0)
    qty = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=4)
    moq = models.PositiveIntegerField("MOQ", default=0)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)
