from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.core import audit
from apps.core.api import BusinessRuleError
from apps.core.rbac import has_perm, is_owner, role_levels
from apps.core.registry import Action, Resource, register
from apps.core.services import request_approval
from apps.masters.models import Customer
from apps.sales.models import SalesOrder, SOLine

from .models import STAGES, Activity, Lead, Quotation, QuotationLine, Sample

PRICE = "commercial.price.view"
COST = "commercial.cost.view"


def own_or_team(request, qs, field):
    if role_levels(request.user) & {"owner", "manager"}:
        return qs
    return qs.filter(**{field: request.user})


def priority_score(lead):
    """Transparent, configurable rule (p.6): quantity + stage + buying date."""
    score = 0
    q = lead.potential_qty_per_month or 0
    score += 40 if q >= 100000 else 25 if q >= 25000 else 10 if q > 0 else 0
    score += {"new": 0, "contacted": 5, "requirement": 10, "sample": 15, "quotation": 20, "negotiation": 30}.get(lead.stage, 0)
    if lead.expected_buy_date:
        days = (lead.expected_buy_date - timezone.localdate()).days
        score += 30 if days <= 30 else 15 if days <= 90 else 5
    return min(score, 100)


@register
class LeadResource(Resource):
    key = "crm.lead"
    server_defaults = ["salesperson"]
    model = Lead
    label = "Leads & pipeline"
    singular = "Lead"
    module = "crm"
    title_field = "company"
    list_fields = ["doc_no", "company", "industry", "power_source", "salesperson", "stage", "priority_score", "next_action", "next_action_date"]
    form_fields = ["company", "industry", "location", "source", "contact_name", "contact_phone", "potential_products",
                   "potential_qty_per_month", "buying_frequency", "expected_buy_date", "salesperson", "next_action", "next_action_date"]
    # The original acquisition source is captured once and never edited, so attribution cannot be rewritten later.
    create_only_fields = ["power_source"]
    required_fields = ["power_source"]
    readonly_fields = ["lost_reason", "customer", "created_at"]
    search_fields = ["doc_no", "company", "contact_name", "industry"]
    filter_fields = ["stage", "salesperson", "industry", "power_source"]
    inlines = [
        {"resource": "crm.activity", "fk": "lead", "label": "Activities"},
        {"resource": "crm.sample", "fk": "lead", "label": "Samples"},
        {"resource": "crm.quotation", "fk": "lead", "label": "Quotations"},
    ]
    help = {
        "steps": "New lead -> Contacted -> Requirement understood -> Sample sent -> Quotation sent -> Negotiation -> Won / Lost / On hold.",
        "boundary": "Loss/hold needs a reason; reopening keeps history.",
    }

    def scope(self, request, qs):
        return own_or_team(request, qs, "salesperson")

    def before_create(self, request, data):
        data.setdefault("salesperson", request.user)
        if not data.get("power_source"):
            raise BusinessRuleError("Choose the original POWER source (Paid, Organic, Word of mouth, External partner or Reach out).",
                                    "validation", {"power_source": ["This field is required."]})

    def create(self, request, data):
        lead = Lead(**data, created_by=request.user, owner=request.user)
        lead.priority_score = priority_score(lead)
        lead.save()
        return lead

    def after_update(self, request, obj, changes):
        score = priority_score(obj)
        if score != obj.priority_score:
            Lead.objects.filter(pk=obj.pk).update(priority_score=score)

    def _stage(request, obj, data):
        stage = data.get("stage")
        if stage not in dict(STAGES):
            raise BusinessRuleError("Unknown stage.", "validation")
        if stage in {"lost", "on_hold"} and len(str(data.get("reason", ""))) < 5:
            raise BusinessRuleError("Record the reason for loss or hold.", "reason_required")
        before = obj.stage
        obj.stage = stage
        if stage in {"lost", "on_hold"}:
            obj.lost_reason = data["reason"]
        obj.priority_score = priority_score(obj)
        obj.row_version += 1
        obj.save()
        audit.record("lead_stage", "crm.lead", obj, changes={"stage": [before, stage]}, reason=data.get("reason", ""))
        return obj

    def _convert(request, obj, data):
        if obj.customer_id:
            raise BusinessRuleError("Lead already linked to a customer.", "invalid_state")
        code = str(data.get("code", "")).strip().upper()
        if not code:
            raise BusinessRuleError("Customer code is required.", "validation")
        if Customer.objects.filter(code=code).exists():
            raise BusinessRuleError("Customer code exists - link instead of duplicating.", "duplicate_customer")
        cust = Customer.objects.create(code=code, name=obj.company, industry=obj.industry, owner=obj.salesperson, created_by=request.user)
        obj.customer = cust
        obj.row_version += 1
        obj.save()
        audit.record("lead_converted", "crm.lead", obj, changes={"customer": cust.code})
        return obj

    actions = [
        Action("set_stage", "Move stage", _stage, permission="crm.lead.update",
               fields=[{"name": "stage", "label": "Stage", "type": "choice", "choices": [{"value": v, "label": l} for v, l in STAGES], "required": True},
                       {"name": "reason", "label": "Reason (required for lost / on hold)", "type": "textarea"}]),
        Action("convert", "Create customer", _convert, permission="masters.customer.create",
               fields=[{"name": "code", "label": "New customer code", "type": "text", "required": True}],
               available=lambda o, r: o.customer_id is None),
    ]


@register
class ActivityResource(Resource):
    key = "crm.activity"
    server_defaults = ["responsible"]
    model = Activity
    label = "Activities & follow-ups"
    singular = "Activity"
    module = "crm"
    title_field = "subject"
    ordering = ["done", "next_follow_up", "-at"]
    list_fields = ["subject", "kind", "lead", "customer", "at", "responsible", "next_follow_up", "done"]
    form_fields = ["lead", "customer", "kind", "subject", "at", "outcome", "next_follow_up", "responsible", "done"]
    filter_fields = ["kind", "responsible", "done", "lead", "customer"]
    search_fields = ["subject", "outcome"]

    def scope(self, request, qs):
        return own_or_team(request, qs, "responsible")

    def before_create(self, request, data):
        data.setdefault("responsible", request.user)
        if not data.get("lead") and not data.get("customer"):
            raise BusinessRuleError("Link the activity to a lead or customer.", "validation")


@register
class SampleResource(Resource):
    key = "crm.sample"
    model = Sample
    label = "Samples"
    singular = "Sample"
    module = "crm"
    list_fields = ["doc_no", "lead", "customer", "product", "qty", "sent_date", "status", "next_action"]
    form_fields = ["lead", "customer", "product", "artwork", "qty", "sent_date", "shipment_ref", "received_by_customer", "feedback", "next_action", "cost", "status"]
    filter_fields = ["status", "customer", "lead"]
    search_fields = ["doc_no", "shipment_ref"]
    field_permissions = {"cost": COST}


@register
class QuotationResource(Resource):
    key = "crm.quotation"
    server_defaults = ["valid_until"]
    model = Quotation
    label = "Quotations"
    singular = "Quotation"
    module = "crm"
    list_fields = ["doc_no", "revision", "customer", "lead", "status", "valid_until", "approved_by", "sent_at"]
    create_only_fields = ["customer", "lead"]
    form_fields = ["valid_until", "price_basis", "gst_rate_pct", "payment_terms", "freight_basis", "block_charges", "delivery_lead_days", "min_margin_pct", "notes"]
    readonly_fields = ["previous", "snapshot"]
    filter_fields = ["status", "customer", "lead"]
    search_fields = ["doc_no", "customer__name", "lead__company"]
    field_permissions = {"block_charges": PRICE, "min_margin_pct": COST, "snapshot": PRICE}
    inlines = [{"resource": "crm.quotationline", "fk": "quotation", "label": "Lines / quantity slabs"}]
    help = {"why": "Retrieve exactly what was sent, convert it once to an SO and trace the order.",
            "boundary": "Margin below the configured minimum needs approval."}

    def scope(self, request, qs):
        if role_levels(request.user) & {"owner", "manager"}:
            return qs
        return qs.filter(created_by=request.user)

    def before_create(self, request, data):
        if not data.get("customer") and not data.get("lead"):
            raise BusinessRuleError("Quotation needs a customer or a lead.", "validation")
        data.setdefault("valid_until", timezone.localdate() + timedelta(days=30))

    def edit_block_reason(self, request, obj):
        return None if obj.status == "draft" else "Only draft quotations can be edited; create a new revision."

    def _margin_issue(obj):
        if obj.min_margin_pct is None:
            return None
        for line in obj.lines.all():
            if line.estimated_cost and line.unit_price:
                margin = (line.unit_price - line.estimated_cost) / line.unit_price * 100
                if margin < obj.min_margin_pct:
                    return f"Line {line.product.code}: margin {margin:.1f}% below {obj.min_margin_pct}%"
        return None

    def _submit(request, obj, data):
        if not obj.lines.exists():
            raise BusinessRuleError("Add lines first.", "no_lines")
        issue = QuotationResource._margin_issue(obj)
        if issue:
            obj.status = "pending_approval"
            obj.save()
            request_approval(kind="quotation_margin", resource="crm.quotation", obj=obj, summary=f"{obj.doc_no} margin exception",
                             payload={"issue": issue}, reason=data.get("reason") or issue, required_permission="crm.quotation.approve",
                             requester=request.user)
            return obj
        return QuotationResource._approve(request, obj, data)

    def _approve(request, obj, data):
        if obj.status not in {"draft", "pending_approval"}:
            raise BusinessRuleError("Quotation is not awaiting approval.", "invalid_state")
        if obj.status == "pending_approval" and obj.created_by_id == request.user.pk and not is_owner(request.user):
            raise BusinessRuleError("Maker/checker: another person must approve the margin exception.", "self_approval")
        obj.status = "approved"
        obj.approved_by = request.user
        obj.row_version += 1
        obj.save()
        audit.record("quotation_approved", "crm.quotation", obj)
        return obj

    def _send(request, obj, data):
        obj.status = "sent"
        obj.sent_at = timezone.now()
        obj.snapshot = {
            "header": {k: audit._jsonable(getattr(obj, k)) for k in ["doc_no", "revision", "valid_until", "price_basis", "gst_rate_pct", "payment_terms", "freight_basis",
                                                                     "block_charges", "delivery_lead_days"]},
            "lines": [{"product": l.product.code, "scope": l.scope, "flavour": l.flavour, "qty_from": l.qty_from, "qty": l.qty,
                       "unit_price": str(l.unit_price), "moq": l.moq, "specification": l.specification} for l in obj.lines.all()],
        }
        obj.row_version += 1
        obj.save()
        if obj.lead_id and obj.lead.stage in {"new", "contacted", "requirement", "sample"}:
            obj.lead.stage = "quotation"
            obj.lead.save(update_fields=["stage"])
        audit.record("quotation_sent", "crm.quotation", obj)
        return obj

    def _revise(request, obj, data):
        with transaction.atomic():
            new = Quotation.objects.create(
                lead=obj.lead, customer=obj.customer, revision=obj.revision + 1, previous=obj, valid_until=obj.valid_until,
                payment_terms=obj.payment_terms, freight_basis=obj.freight_basis, block_charges=obj.block_charges,
                price_basis=obj.price_basis, gst_rate_pct=obj.gst_rate_pct,
                delivery_lead_days=obj.delivery_lead_days, min_margin_pct=obj.min_margin_pct, created_by=request.user, owner=request.user,
            )
            for l in obj.lines.all():
                QuotationLine.objects.create(quotation=new, product=l.product, scope=l.scope, flavour=l.flavour, specification=l.specification,
                                             qty_from=l.qty_from, qty=l.qty, unit_price=l.unit_price, moq=l.moq, estimated_cost=l.estimated_cost, created_by=request.user)
            obj.status = "superseded"
            obj.save()
        audit.record("quotation_revised", "crm.quotation", new, changes={"previous": obj.doc_no}, reason=data.get("reason", ""))
        return new

    def _convert(request, obj, data):
        """Convert once to an SO using existing data - no retyping (p.6)."""
        if obj.status not in {"sent", "accepted", "approved"}:
            raise BusinessRuleError("Only an approved/sent quotation can be converted.", "invalid_state")
        if obj.orders.exists():
            raise BusinessRuleError(f"Already converted to {obj.orders.first().doc_no}.", "already_converted")
        if not obj.customer_id:
            if obj.lead_id and obj.lead.customer_id:
                obj.customer = obj.lead.customer
            else:
                raise BusinessRuleError("Create/link the customer from the lead first.", "customer_missing")
        so = SalesOrder.objects.create(
            customer=obj.customer, salesperson=obj.created_by or request.user, payment_terms=obj.payment_terms, freight_basis=obj.freight_basis,
            billing_address=obj.customer.billing_address, delivery_address=obj.customer.delivery_address,
            advance_required_pct=obj.customer.advance_required_pct, quotation=obj, customer_po_no=data.get("customer_po_no", ""),
            created_by=request.user, owner=request.user,
        )
        for i, l in enumerate(obj.lines.all(), start=1):
            SOLine.objects.create(so=so, line_no=i, product=l.product, scope=l.scope, flavour=l.flavour, qty=l.qty, unit_price=l.unit_price,
                                  iml=l.product.iml, created_by=request.user)
        obj.status = "converted"
        obj.save()
        if obj.lead_id:
            obj.lead.stage = "won"
            obj.lead.save(update_fields=["stage"])
        audit.record("quotation_converted", "crm.quotation", obj, changes={"so": so.doc_no})
        return so

    actions = [
        Action("submit", "Submit / approve", _submit, permission="crm.quotation.update", tone="primary", available=lambda o, r: o.status == "draft"),
        Action("approve", "Approve", _approve, available=lambda o, r: o.status == "pending_approval"),
        Action("send", "Mark sent (freeze snapshot)", _send, permission="crm.quotation.update", available=lambda o, r: o.status == "approved"),
        Action("revise", "New revision", _revise, permission="crm.quotation.create", reason_required=True,
               available=lambda o, r: o.status in {"approved", "sent", "rejected", "expired"}),
        Action("convert", "Convert to sales order", _convert, permission="sales.salesorder.create", tone="primary",
               fields=[{"name": "customer_po_no", "label": "Customer PO no.", "type": "text"}],
               available=lambda o, r: o.status in {"approved", "sent", "accepted"}),
    ]


@register
class QuotationLineResource(Resource):
    key = "crm.quotationline"
    in_nav = False
    model = QuotationLine
    label = "Quotation lines"
    singular = "Quotation line"
    module = "crm"
    title_field = "product"
    ordering = ["quotation", "qty_from"]
    list_fields = ["quotation", "product", "scope", "flavour", "qty_from", "qty", "unit_price", "moq", "estimated_cost"]
    form_fields = ["quotation", "product", "scope", "flavour", "specification", "qty_from", "qty", "unit_price", "moq", "estimated_cost"]
    filter_fields = ["quotation"]
    field_permissions = {"unit_price": PRICE, "estimated_cost": COST}

    def edit_block_reason(self, request, obj):
        return None if obj.quotation.status == "draft" else "Quotation is no longer a draft."

    def before_create(self, request, data):
        if data["quotation"].status != "draft":
            raise BusinessRuleError("Quotation is no longer a draft.", "record_locked")


from apps.core.services import approval_handler  # noqa: E402


@approval_handler("quotation_margin")
def _approve_margin_exception(approval, approver):
    q = Quotation.objects.select_for_update().get(pk=approval.object_id)
    if q.status == "pending_approval":
        q.status = "approved"
        q.approved_by = approver
        q.row_version += 1
        q.save()
