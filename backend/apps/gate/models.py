"""
Entire-plant gate & security (brief p.35-45).

Physical events (who/what entered/left) are recorded here. They are linked to,
but never substitute for, business postings: a gate arrival is not a GRN and a
vehicle exit is not a stock relief.
"""
from django.conf import settings
from django.db import models

from apps.core.models import BaseRecord, DocumentRecord, Department
from apps.masters.models import Customer, Supplier


class Person(BaseRecord):
    KINDS = [("employee", "Employee"), ("contract", "Contract worker"), ("contractor", "Contractor / service"), ("driver", "Driver"), ("visitor", "Visitor")]
    person_code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, choices=KINDS)
    employer = models.CharField("Employer / agency / organisation", max_length=150, blank=True)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT)
    supervisor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    phone = models.CharField(max_length=20, blank=True)
    badge_no = models.CharField(max_length=30, blank=True)
    authorised_shift = models.CharField(max_length=10, blank=True)
    skills = models.CharField(max_length=200, blank=True)
    id_verification_note = models.CharField(max_length=120, blank=True, help_text="Only the minimum verification detail - not full ID copies")
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.person_code} {self.name}"


class Visit(DocumentRecord):
    DOC_PREFIX = "VIS"
    TYPES = [("business", "Business meeting"), ("supplier", "Supplier"), ("customer", "Customer / audit"), ("service", "Service / contractor"),
             ("interview", "Interview"), ("delivery", "Delivery"), ("other", "Other")]
    STATUS = [("draft", "Draft"), ("awaiting_approval", "Awaiting host approval"), ("approved", "Approved"), ("rejected", "Rejected"),
              ("inside", "Inside"), ("exited", "Exited"), ("cancelled", "Cancelled"), ("no_show", "No show")]
    visitor_name = models.CharField(max_length=150)
    organisation = models.CharField(max_length=150, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.PROTECT, related_name="visits")
    visit_type = models.CharField(max_length=10, choices=TYPES, default="business")
    host = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="hosted_visits")
    purpose = models.CharField(max_length=300)
    pre_registered = models.BooleanField(default=False)
    expected_at = models.DateTimeField(null=True, blank=True)
    expected_minutes = models.PositiveIntegerField(default=60)
    gate = models.CharField(max_length=40, default="Main gate")
    permitted_area = models.CharField(max_length=120, blank=True)
    escort_required = models.BooleanField(default=True)
    badge_no = models.CharField(max_length=30, blank=True)
    badge_returned = models.BooleanField(default=False)
    safety_briefing_ack = models.BooleanField(default=False)
    belongings_declared = models.CharField(max_length=300, blank=True)
    verification_outcome = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=18, choices=STATUS, default="draft", db_index=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    decision_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True)
    entry_at = models.DateTimeField(null=True, blank=True)
    exit_at = models.DateTimeField(null=True, blank=True)
    entry_guard = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    exit_guard = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    late_stay_reason = models.CharField(max_length=200, blank=True)


class GateEvent(BaseRecord):
    """Staff / labour / contractor movement (p.38). Feeds attendance review; is not attendance."""

    KINDS = [("entry", "Entry"), ("exit", "Exit"), ("break_out", "Break - out"), ("break_in", "Break - return"), ("duty_out", "Outside duty - out"), ("duty_in", "Outside duty - return")]
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="gate_events")
    kind = models.CharField(max_length=10, choices=KINDS)
    at = models.DateTimeField()
    gate = models.CharField(max_length=40, default="Main gate")
    purpose = models.CharField(max_length=200, blank=True)
    guard = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    shift_date = models.DateField()
    shift = models.CharField(max_length=5)
    duty_pass = models.CharField(max_length=60, blank=True)


class AttendanceException(BaseRecord):
    KINDS = [("missing_exit", "Missing exit"), ("missing_entry", "Missing entry"), ("repeat_tap", "Repeated tap"), ("inactive_id", "Inactive ID used"), ("late_exit", "Late exit")]
    person = models.ForeignKey(Person, on_delete=models.PROTECT, related_name="attendance_exceptions")
    shift_date = models.DateField()
    kind = models.CharField(max_length=14, choices=KINDS)
    detail = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=[("open", "Open"), ("resolved", "Resolved")], default="open")
    resolution = models.CharField(max_length=300, blank=True)

    class Meta:
        unique_together = [("person", "shift_date", "kind")]


class VehicleTrip(DocumentRecord):
    DOC_PREFIX = "VT"
    STATUS = [("inside", "Inside"), ("exited", "Exited")]
    vehicle_no = models.CharField(max_length=20)
    driver_name = models.CharField(max_length=120)
    driver_phone = models.CharField(max_length=20, blank=True)
    transporter = models.CharField(max_length=120, blank=True)
    purpose = models.CharField(max_length=20, choices=[("inward", "Material inward"), ("outward", "Dispatch / outward"), ("both", "Both"), ("other", "Other")])
    seal_no = models.CharField(max_length=40, blank=True)
    in_at = models.DateTimeField()
    out_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="inside", db_index=True)
    guard = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")


class GateMaterial(DocumentRecord):
    """Gate inward/outward entry: header + lines + document links (p.39-40)."""

    DOC_PREFIX = "GE"
    DIRECTIONS = [("in", "Inward"), ("out", "Outward")]
    INWARD_TYPES = [("purchase", "Purchase"), ("transfer", "Transfer"), ("customer_return", "Customer return"), ("job_work", "Job work"), ("returnable", "Returnable"), ("other", "Other")]
    STATUS = [
        ("arrived", "Arrived at gate"), ("receiver_notified", "Receiver notified"), ("unloading", "Unloading permitted"),
        ("handed_over", "Handed over to stores"), ("authorised", "Outward load authorised"), ("verified", "Guard verified load"),
        ("departed", "Departed"), ("discrepancy", "Discrepancy open"), ("closed", "Closed"), ("cancelled", "Cancelled"),
    ]
    direction = models.CharField(max_length=3, choices=DIRECTIONS)
    inward_type = models.CharField(max_length=16, choices=INWARD_TYPES, blank=True)
    trip = models.ForeignKey(VehicleTrip, null=True, blank=True, on_delete=models.PROTECT, related_name="materials")
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.PROTECT)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT)
    party_name = models.CharField(max_length=150, blank=True)
    document_no = models.CharField("Invoice / challan no.", max_length=60)
    document_date = models.DateField(null=True, blank=True)
    po_reference = models.CharField(max_length=60, blank=True)
    dispatch = models.ForeignKey("fg.Dispatch", null=True, blank=True, on_delete=models.PROTECT, related_name="gate_outs")
    receiving_department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.PROTECT)
    packages = models.PositiveIntegerField(default=0)
    seal_no = models.CharField(max_length=40, blank=True)
    weighbridge_ref = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=18, choices=STATUS, default="arrived", db_index=True)
    guard = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    authorised_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    event_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True)


class GateMaterialLine(BaseRecord):
    entry = models.ForeignKey(GateMaterial, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=200)
    item = models.ForeignKey("masters.Item", null=True, blank=True, on_delete=models.PROTECT)
    supplier_lot = models.CharField(max_length=80, blank=True)
    declared_qty = models.DecimalField("Declared qty", max_digits=14, decimal_places=3)
    observed_qty = models.DecimalField("Gate-observed qty", max_digits=14, decimal_places=3, null=True, blank=True)
    uom = models.CharField(max_length=10)
    packages = models.PositiveIntegerField(default=0)
    visible_damage = models.CharField(max_length=200, blank=True)


class GatePass(DocumentRecord):
    DOC_PREFIX = "GP"
    STATUS = [("draft", "Draft"), ("approved", "Approved"), ("out", "Out"), ("partial", "Partially returned"), ("returned", "Returned / closed"),
              ("disposed", "Closed by authorised disposition"), ("cancelled", "Cancelled")]
    returnable = models.BooleanField(default=True)
    asset_description = models.CharField(max_length=200)
    asset_ref = models.CharField("Serial / asset ID", max_length=80, blank=True)
    mould = models.ForeignKey("masters.Mould", null=True, blank=True, on_delete=models.PROTECT)
    qty = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    returned_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    recipient = models.CharField(max_length=150)
    purpose = models.CharField(max_length=200)
    due_back = models.DateField(null=True, blank=True)
    asset_owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    out_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="draft", db_index=True)
    return_log = models.JSONField(default=list, blank=True)
    disposition = models.CharField(max_length=300, blank=True)


class Courier(DocumentRecord):
    DOC_PREFIX = "CR"
    STATUS = [("received", "Received at gate"), ("acknowledged", "Receiver acknowledged"), ("awaiting_pickup", "Awaiting courier pickup"), ("collected", "Collected by courier")]
    direction = models.CharField(max_length=3, choices=[("in", "Inward"), ("out", "Outward")])
    courier_company = models.CharField(max_length=80)
    awb = models.CharField("AWB / tracking", max_length=60)
    sender = models.CharField(max_length=150)
    receiver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="couriers", help_text="Named internal receiver / sender")
    packages = models.PositiveIntegerField(default=1)
    declared_contents = models.CharField(max_length=200, blank=True)
    condition = models.CharField(max_length=120, default="Intact")
    status = models.CharField(max_length=16, choices=STATUS, db_index=True)
    guard = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=20, blank=True, help_text="Receiver decides: sample / document / asset / stock receipt")
