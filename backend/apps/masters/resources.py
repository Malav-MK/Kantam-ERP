from django.db import transaction
from django.utils import timezone

from apps.core import audit
from apps.core.api import BusinessRuleError
from apps.core.calc import CalcError, recipe_split
from apps.core.registry import Action, Resource, register
from apps.core.services import notify_perm

from .models import (
    Artwork, Contact, Customer, Holiday, Item, Location, Machine, Mould, PackingRule, Product, ProductComponent,
    Reason, Recipe, RecipeLine, Supplier, UOMConversion,
)


class MasterResource(Resource):
    module = "masters"
    title_field = "name"
    ordering = ["code"]


def next_code(model, width=2):
    """Next free numeric code: 01, 02 ... (keeps the width already in use, e.g. 007 -> 008)."""
    codes = [c for c in model.objects.values_list("code", flat=True) if c.isdigit()]
    width = max([width] + [len(c) for c in codes])
    n = max([int(c) for c in codes], default=0) + 1
    while model.objects.filter(code=str(n).zfill(width)).exists():
        n += 1
    return str(n).zfill(width)


@register
class CustomerResource(MasterResource):
    key = "masters.customer"
    model = Customer
    label = "Customers"
    singular = "Customer"
    list_fields = ["code", "name", "plant_branch", "industry", "owner", "lead_time_days", "active"]
    form_fields = ["code", "name", "plant_branch", "industry", "gstin", "billing_address", "delivery_address", "payment_terms",
                   "freight_basis", "credit_limit", "advance_required_pct", "lead_time_days", "owner", "active"]
    search_fields = ["code", "name", "industry", "plant_branch"]
    filter_fields = ["active", "owner"]
    server_defaults = ["code"]
    field_help = {"code": "Filled in automatically with the next number; you can change it."}
    field_permissions = {"credit_limit": "commercial.price.view"}
    inlines = [{"resource": "masters.contact", "fk": "customer", "label": "Contacts"}]
    help = {"why": "One customer record shared by CRM, orders, dispatch and complaints - never duplicated.",
            "boundary": "Historical rates are reference data, not current pricing."}

    def form_defaults(self, user):
        return {"code": next_code(Customer)}

    def before_create(self, request, data):
        data["code"] = str(data.get("code") or "").strip() or next_code(Customer)

    def extra_detail(self, request, obj):
        from apps.sales.services import customer_timeline
        return {"timeline": customer_timeline(request, obj)}


@register
class SupplierResource(MasterResource):
    key = "masters.supplier"
    model = Supplier
    module = "purchase"  # suppliers are managed with purchasing, not in Masters
    label = "Suppliers"
    singular = "Supplier"
    list_fields = ["code", "name", "category", "lead_time_days", "active"]
    form_fields = ["code", "name", "category", "gstin", "address", "payment_terms", "lead_time_days", "active"]
    search_fields = ["code", "name", "category"]
    filter_fields = ["active", "category"]
    inlines = [{"resource": "masters.contact", "fk": "supplier", "label": "Contacts"}]


@register
class ContactResource(MasterResource):
    key = "masters.contact"
    in_nav = False
    model = Contact
    label = "Contacts"
    singular = "Contact"
    ordering = ["name"]
    list_fields = ["name", "customer", "supplier", "designation", "phone", "whatsapp_opt_in", "active"]
    form_fields = ["customer", "supplier", "name", "designation", "phone", "whatsapp_opt_in", "active"]
    search_fields = ["name", "phone"]
    filter_fields = ["customer", "supplier", "active"]


@register
class ItemResource(MasterResource):
    key = "masters.item"
    model = Item
    label = "Items & materials"
    singular = "Item"
    list_fields = ["code", "name", "kind", "stock_uom", "grade", "colour", "group", "min_stock", "active"]
    form_fields = ["code", "name", "kind", "stock_uom", "whole_units", "grade", "colour", "group", "min_stock", "safety_stock",
                   "moq", "order_multiple", "lead_time_days", "default_supplier", "standard_cost", "legacy_code", "active"]
    search_fields = ["code", "name", "grade", "colour", "group", "legacy_code"]
    filter_fields = ["kind", "active", "group", "stock_uom"]
    field_permissions = {"standard_cost": "commercial.cost.view"}
    inlines = [{"resource": "masters.uomconversion", "fk": "item", "label": "Unit conversions"}]
    help = {"why": "Six core store groups (polymer, label, masterbatch, polybag, outer box, BOPP tape) plus impact modifier and others - all accountable.",
            "boundary": "Used items are deactivated, never deleted."}


@register
class LabelItemResource(MasterResource):
    """IML label stock items, kept on their own screen so labels can be set up before linking them to artwork."""

    key = "masters.labelitem"
    model = Item
    canonical = False
    label = "Label items"
    singular = "Label item"
    list_fields = ["code", "name", "group", "stock_uom", "min_stock", "default_supplier", "active"]
    form_fields = ["code", "name", "group", "stock_uom", "min_stock", "safety_stock", "moq", "order_multiple", "lead_time_days",
                   "default_supplier", "standard_cost", "legacy_code", "active"]
    search_fields = ["code", "name", "group", "legacy_code"]
    filter_fields = ["active", "group"]
    server_defaults = ["stock_uom"]
    field_permissions = {"standard_cost": "commercial.cost.view"}
    field_help = {"group": "Label group / classification, e.g. Round 500 lids", "stock_uom": "Leave empty for pieces."}
    inlines = [{"resource": "masters.artwork", "fk": "label_item", "label": "Artworks printed on this label"}]
    help = {"why": "Labels are counted per piece and linked to an approved artwork revision, so the wrong label cannot be issued to a job.",
            "steps": "Add the label item here, then pick it as the label item on the artwork (Masters → Artwork & labels)."}

    def scope(self, request, qs):
        return qs.filter(kind="label")

    def before_create(self, request, data):
        data["kind"] = "label"
        data["whole_units"] = True
        data["stock_uom"] = data.get("stock_uom") or "pcs"


@register
class UOMConversionResource(MasterResource):
    key = "masters.uomconversion"
    in_nav = False
    model = UOMConversion
    label = "Unit conversions"
    singular = "Conversion"
    title_field = "from_uom"
    ordering = ["item"]
    list_fields = ["item", "from_uom", "factor", "approved"]
    form_fields = ["item", "from_uom", "factor", "approved"]
    filter_fields = ["item"]


@register
class ProductResource(MasterResource):
    key = "masters.product"
    model = Product
    label = "Products"
    singular = "Product"
    list_fields = ["code", "name", "shape", "capacity_ml", "customer", "iml", "active"]
    form_fields = ["code", "name", "shape", "capacity_ml", "customer", "customer_code", "iml", "set_item", "active"]
    search_fields = ["code", "name", "customer_code"]
    filter_fields = ["shape", "customer", "iml", "active"]
    inlines = [
        {"resource": "masters.productcomponent", "fk": "product", "label": "Components (BOM)"},
        {"resource": "masters.artwork", "fk": "product", "label": "Artworks"},
        {"resource": "masters.packingrule", "fk": "product", "label": "Packing rules"},
    ]


@register
class ProductComponentResource(MasterResource):
    key = "masters.productcomponent"
    model = ProductComponent
    deleted_with_parent = True
    label = "Product components"
    singular = "Component"
    title_field = "component_type"
    ordering = ["product", "component_type"]
    list_fields = ["product", "component_type", "item", "qty_per_set", "weight_g", "iml", "labels_per_piece", "procured"]
    form_fields = ["product", "component_type", "item", "qty_per_set", "weight_g", "iml", "labels_per_piece", "procured"]
    filter_fields = ["product", "component_type"]
    search_fields = ["product__code", "product__name", "item__code", "item__name"]
    help = {"why": "What a product is made of: container, lid, handle ... each with its moulded item and weight.",
            "steps": "Add one row per component, or open the product and use its Components tab."}


def _artwork_impact(artwork):
    """What is affected when an artwork revision is replaced (p.8)."""
    from apps.inventory.models import StockBalance
    from apps.planning.models import ProductionRequest
    from apps.purchase.models import POLine

    label_stock = list(
        StockBalance.objects.filter(lot__artwork=artwork, qty__gt=0).values("item__code", "lot__doc_no", "location__code", "qty")
    )
    open_po = list(
        POLine.objects.filter(artwork=artwork, status__in=["open", "partial"]).values("po__doc_no", "item__code", "qty", "received_qty")
    )
    prs = list(
        ProductionRequest.objects.filter(artwork=artwork, status__in=["draft", "released", "in_progress"]).values("doc_no", "status", "shift_date", "shift")
    )
    return {"label_stock": label_stock, "open_purchase_lines": open_po, "planned_or_running_prs": prs}


@register
class ArtworkResource(MasterResource):
    key = "masters.artwork"
    model = Artwork
    label = "Artwork & labels"
    singular = "Artwork revision"
    title_field = "code"
    ordering = ["code", "-revision"]
    list_fields = ["code", "revision", "product", "component_type", "flavour", "customers", "status", "finish", "label_item"]
    create_only_fields = ["code", "product", "component_type"]
    form_fields = ["customers", "flavour", "common_design", "label_item", "kld_reference", "height_mm", "width_mm", "colour_reference", "finish",
                   "finish_other", "supplier", "customer_approval_ref", "customer_approved_on"]
    required_fields = ["finish_other"]
    show_if = {"finish_other": {"finish": "other"}}
    filter_defaults = {"supplier": {"category": "labels"}}
    required_uploads = [
        {"purpose": "design_pdf", "label": "Designed PDF", "accept": "application/pdf"},
        {"purpose": "customer_approval", "label": "Customer approval photo", "accept": "image/jpeg,image/png"},
    ]
    readonly_fields = ["size", "internal_approved_by", "released_at", "supersedes"]
    search_fields = ["code", "flavour", "kld_reference"]
    filter_fields = ["product", "component_type", "status", "customers", "flavour", "label_item"]
    depends = {"label_item": {"kind": "=label"}}
    field_help = {
        "revision": "Each change is a new revision; production may only use an approved revision.",
        "height_mm": "Label height in millimetres, e.g. 98.25",
        "width_mm": "Label width in millimetres, e.g. 506",
        "label_item": "The label stock item (Masters → Label items) printed with this design.",
        "customer_approval_ref": "Never infer approval from a draft image or informal filename (p.8).",
    }
    help = {
        "why": "A correct container with the wrong label is still the wrong customer product.",
        "steps": "Requirement/KLD -> Draft -> Internal review -> Customer approval -> Print release -> Eligible for production.",
        "effect": "Approving a new revision makes the old one obsolete and lists old label stock, open POs and planned jobs for disposition.",
        "boundary": "Label-store v14 layout/logic must be inspected before migration (brief p.8).",
    }
    FLOW = ["requirement", "draft", "internal_review", "customer_approval", "print_release", "approved"]

    def edit_block_reason(self, request, obj):
        if obj.status in {"approved", "obsolete"}:
            return "Approved/obsolete artwork is frozen. Create a new revision to change it."
        return None

    @staticmethod
    def _check_finish(finish, data):
        if finish == "other":
            if not str(data.get("finish_other") or "").strip():
                raise BusinessRuleError("Describe the finish in 'Other finish'.", "validation", {"finish_other": "required"})
        else:
            data["finish_other"] = ""

    def before_create(self, request, data):
        self._check_finish(data.get("finish"), data)

    def before_update(self, request, obj, data):
        data.setdefault("finish_other", obj.finish_other)
        self._check_finish(data.get("finish", obj.finish), data)

    def _advance(request, obj, data):
        from apps.core.attachments import attachments_for

        flow = ArtworkResource.FLOW
        if obj.status not in flow or obj.status == "approved":
            raise BusinessRuleError("Artwork cannot advance from this state.", "invalid_state")
        missing = [u["label"] for u in ArtworkResource.required_uploads if not attachments_for(obj, u["purpose"]).exists()]
        if missing:
            raise BusinessRuleError(f"Attach {' and '.join(missing)} first (Attachments, below).", "upload_required", {"missing": missing})
        nxt = flow[flow.index(obj.status) + 1]
        if nxt == "customer_approval":
            obj.internal_approved_by = request.user
        if nxt == "print_release":
            if not obj.customer_approval_ref or not obj.customer_approved_on:
                raise BusinessRuleError("Record the customer approval reference and date before print release.", "approval_missing")
        if nxt == "approved":
            if obj.product.iml and obj.component_type and not obj.label_item_id:
                raise BusinessRuleError("Link the label item before making the artwork eligible.", "label_missing")
            obj.released_at = timezone.now()
            impact = {}
            with transaction.atomic():
                previous = Artwork.objects.select_for_update().filter(
                    code=obj.code, component_type=obj.component_type, flavour=obj.flavour, status="approved",
                ).exclude(pk=obj.pk)
                for old in previous:
                    impact[str(old)] = _artwork_impact(old)
                    old.status = "obsolete"
                    old.row_version += 1
                    old.save()
                    audit.record("artwork_obsoleted", "masters.artwork", old, reason=f"Superseded by R{obj.revision}")
            if impact:
                notify_perm(
                    "planning.productionrequest.view", f"Artwork {obj.code} changed to R{obj.revision}",
                    body=str(impact), severity="warning", resource="masters.artwork", object_id=obj.pk,
                    next_action="Review old label stock, open POs and planned jobs; record disposition", dedupe_key=f"artwork-change:{obj.pk}",
                )
        before = obj.status
        obj.status = nxt
        obj.row_version += 1
        obj.save()
        audit.record("artwork_advanced", "masters.artwork", obj, changes={"status": [before, nxt]}, reason=data.get("reason", ""))
        return obj

    def _new_revision(request, obj, data):
        latest = Artwork.objects.filter(code=obj.code).order_by("-revision").first()
        new = Artwork.objects.create(
            code=obj.code, revision=latest.revision + 1, product=obj.product, component_type=obj.component_type,
            flavour=obj.flavour, common_design=obj.common_design, label_item=obj.label_item,
            kld_reference=obj.kld_reference, height_mm=obj.height_mm, width_mm=obj.width_mm, colour_reference=obj.colour_reference,
            finish=obj.finish, finish_other=obj.finish_other, supplier=obj.supplier, status="draft", supersedes=obj, created_by=request.user,
        )
        new.customers.set(obj.customers.all())
        audit.record("artwork_revision_created", "masters.artwork", new, reason=data.get("reason", ""))
        return new

    def _obsolete(request, obj, data):
        impact = _artwork_impact(obj)
        obj.status = "obsolete"
        obj.row_version += 1
        obj.save()
        audit.record("artwork_obsoleted", "masters.artwork", obj, changes={"impact": impact}, reason=data.get("reason", ""))
        return obj

    def extra_detail(self, request, obj):
        return {"impact_if_replaced": _artwork_impact(obj)}

    actions = [
        Action("advance", "Advance workflow", _advance, tone="primary", available=lambda o, r: o.status in ArtworkResource.FLOW[:-1]),
        Action("new_revision", "Create new revision", _new_revision, reason_required=True, available=lambda o, r: o.status in {"approved", "obsolete"}),
        Action("obsolete", "Mark obsolete", _obsolete, reason_required=True, tone="danger", available=lambda o, r: o.status != "obsolete"),
    ]


@register
class RecipeResource(MasterResource):
    key = "masters.recipe"
    model = Recipe
    label = "Recipes / BOM"
    singular = "Recipe"
    title_field = "revision"
    ordering = ["product_component", "-revision"]
    list_fields = ["product_component", "revision", "basis", "loss_allowance_pct", "expected_yield_pct", "effective_from", "approved"]
    create_only_fields = ["product_component", "revision"]
    form_fields = ["basis", "loss_allowance_pct", "label_allowance_pct", "expected_yield_pct", "effective_from", "notes"]
    readonly_fields = ["approved_by"]
    filter_fields = ["product_component", "approved"]
    inlines = [{"resource": "masters.recipeline", "fk": "recipe", "label": "Recipe lines"}]
    help = {"why": "Polymer blend and masterbatch % are checked against the agreed basis; released jobs keep a snapshot.",
            "boundary": "Masterbatch colour follows the SO; a different colour needs an SO amendment."}

    def edit_block_reason(self, request, obj):
        return "Approved recipes are frozen; create a new revision." if obj.approved else None

    def _approve(request, obj, data):
        lines = list(obj.lines.values_list("item_id", "percent"))
        try:
            recipe_split(100, lines, obj.basis)
        except CalcError as exc:
            raise BusinessRuleError(str(exc), "recipe_invalid")
        obj.approved = True
        obj.approved_by = request.user
        obj.row_version += 1
        obj.save()
        audit.record("recipe_approved", "masters.recipe", obj)
        return obj

    actions = [Action("approve", "Approve recipe", _approve, tone="primary", available=lambda o, r: not o.approved)]


@register
class RecipeLineResource(MasterResource):
    key = "masters.recipeline"
    in_nav = False
    model = RecipeLine
    label = "Recipe lines"
    singular = "Recipe line"
    title_field = "item"
    ordering = ["recipe", "sequence"]
    list_fields = ["recipe", "sequence", "item", "percent", "is_masterbatch"]
    form_fields = ["recipe", "sequence", "item", "percent", "is_masterbatch"]
    filter_fields = ["recipe"]

    def edit_block_reason(self, request, obj):
        return "Recipe is approved and frozen." if obj.recipe.approved else None

    def before_create(self, request, data):
        if data["recipe"].approved:
            raise BusinessRuleError("Recipe is approved and frozen.", "record_locked")


@register
class PackingRuleResource(MasterResource):
    key = "masters.packingrule"
    model = PackingRule
    label = "Packing rules"
    singular = "Packing rule"
    title_field = "revision"
    ordering = ["product", "-revision"]
    list_fields = ["product", "revision", "pcs_per_bag", "pcs_per_carton", "carton_item", "effective_from", "active"]
    form_fields = ["product", "revision", "pcs_per_bag", "pcs_per_carton", "bag_item", "carton_item", "tape_item", "cartons_per_tape_roll", "effective_from", "active"]
    filter_fields = ["product", "active"]


@register
class MachineResource(MasterResource):
    key = "masters.machine"
    model = Machine
    label = "Machines"
    singular = "Machine"
    list_fields = ["code", "name", "make", "tonnage", "drive_type", "has_iml_robot", "status", "active"]
    form_fields = ["code", "name", "make", "model_no", "serial_no", "tonnage", "drive_type", "has_iml_robot", "robot_ref", "status", "sequence", "active"]
    search_fields = ["code", "name", "make"]
    filter_fields = ["status", "active", "drive_type"]
    help = {"boundary": "Machine count is not hard-coded (10 JSW vs 9-machine planning view to be confirmed)."}


@register
class MouldResource(MasterResource):
    key = "masters.mould"
    model = Mould
    label = "Moulds"
    singular = "Mould"
    list_fields = ["code", "name", "product_component", "cavities", "active_cavities", "standard_cycle_s", "status", "shot_count"]
    form_fields = ["code", "name", "product_component", "cavities", "active_cavities", "standard_cycle_s", "setup_minutes", "min_tonnage",
                   "requires_robot", "ownership", "status", "location"]
    search_fields = ["code", "name"]
    filter_fields = ["status", "product_component", "ownership"]


@register
class LocationResource(MasterResource):
    key = "masters.location"
    model = Location
    label = "Locations"
    singular = "Location"
    list_fields = ["code", "name", "kind", "machine", "parent", "active"]
    form_fields = ["code", "name", "kind", "machine", "parent", "active"]
    search_fields = ["code", "name"]
    filter_fields = ["kind", "active"]


@register
class ReasonResource(MasterResource):
    key = "masters.reason"
    model = Reason
    label = "Reason codes"
    singular = "Reason"
    title_field = "text"
    ordering = ["kind", "code"]
    list_fields = ["kind", "code", "text", "active"]
    form_fields = ["kind", "code", "text", "active"]
    filter_fields = ["kind", "active"]


@register
class HolidayResource(MasterResource):
    key = "masters.holiday"
    model = Holiday
    label = "Holidays"
    singular = "Holiday"
    ordering = ["date"]
    list_fields = ["date", "name"]
    form_fields = ["date", "name"]
