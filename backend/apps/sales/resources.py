from django.db.models import Max
from django.utils import timezone

from apps.core import audit
from apps.core.api import BusinessRuleError
from apps.core.rbac import has_perm, role_levels
from apps.core.registry import Action, Resource, register
from apps.inventory.services import lot_free_qty, reserve, usable_lot_balances
from apps.masters.models import Artwork, Item

from . import services
from .models import ComponentDemand, SalesOrder, SOLine, SOVersion

PRICE = "commercial.price.view"


def _own_scope(request, qs, field):
    """Sales executives see their own orders; managers and downstream departments
    (planning, dispatch, accounts) see all orders they have view permission for."""
    levels = role_levels(request.user)
    if levels & {"owner", "manager"} or not has_perm(request.user, "sales.salesorder.create"):
        return qs
    return qs.filter(**{field: request.user})


@register
class SalesOrderResource(Resource):
    key = "sales.salesorder"
    server_defaults = ["salesperson"]
    model = SalesOrder
    label = "Sales orders"
    singular = "Sales order"
    module = "sales"
    list_fields = ["doc_no", "customer", "customer_po_no", "order_date", "salesperson", "status", "version", "locked_at", "commercial_approved"]
    create_only_fields = ["customer"]
    form_fields = ["customer_po_no", "customer_po_date", "order_date", "salesperson", "billing_address", "delivery_address", "payment_terms",
                   "freight_basis", "advance_required_pct", "notes"]
    readonly_fields = ["advance_received", "credit_released", "quotation", "cancel_reason", "created_at", "order_value"]
    search_fields = ["doc_no", "customer__name", "customer_po_no"]
    filter_fields = ["status", "customer", "salesperson"]
    field_permissions = {"advance_required_pct": PRICE, "advance_received": PRICE, "order_value": PRICE}
    inlines = [{"resource": "sales.soline", "fk": "so", "label": "Order lines"}, {"resource": "sales.soversion", "fk": "so", "label": "Versions"}]
    help = {
        "why": "Turns an agreed customer requirement into exact product/configuration demand at line level.",
        "steps": "Create order -> add lines (product, set/component, flavour, artworks, colours, qty, dates) -> Confirm.",
        "effect": "Confirmation generates container/lid/handle demand for planning, purchase and stores.",
        "boundary": "Auto-locks 24h after creation (server time). Later changes need reason + approval and create a new version.",
    }

    def scope(self, request, qs):
        return _own_scope(request, qs, "salesperson")

    def before_create(self, request, data):
        c = data["customer"]
        data.setdefault("salesperson", request.user)
        data.setdefault("payment_terms", c.payment_terms)
        data.setdefault("freight_basis", c.freight_basis)
        data.setdefault("billing_address", c.billing_address)
        data.setdefault("delivery_address", c.delivery_address)
        data.setdefault("advance_required_pct", c.advance_required_pct)

    def edit_block_reason(self, request, obj):
        if obj.status in {"cancelled", "closed"}:
            return f"Order is {obj.status}."
        if obj.is_locked:
            return "Order auto-locked 24 hours after creation. Use 'Request amendment' on the line."
        return None

    def extra_detail(self, request, obj):
        lines = []
        for line in obj.lines.select_related("product"):
            lines.append({"line": str(line), "id": str(line.pk), "quantities": services.quantities(line), "readiness": services.readiness(line)})
        return {
            "locked": obj.is_locked,
            "lock_due": obj.locked_at,
            "lines": lines,
            "order_value": str(obj.order_value) if has_perm(request.user, PRICE) else None,
        }

    def _confirm(request, obj, data):
        return services.confirm(obj, request.user)

    def _commercial_approve(request, obj, data):
        obj.commercial_approved = True
        obj.row_version += 1
        obj.save()
        audit.record("so_commercial_approved", "sales.salesorder", obj, reason=data.get("note", ""))
        return obj

    def _record_advance(request, obj, data):
        amount = data.get("amount")
        if amount in (None, ""):
            raise BusinessRuleError("Amount is required.", "validation")
        before = obj.advance_received
        obj.advance_received = amount
        obj.row_version += 1
        obj.save()
        audit.record("so_advance_recorded", "sales.salesorder", obj, changes={"advance_received": [str(before), str(amount)]}, reason=data.get("reason", ""))
        return obj

    def _credit_release(request, obj, data):
        obj.credit_released = True
        obj.row_version += 1
        obj.save()
        audit.record("so_credit_released", "sales.salesorder", obj, reason=data.get("reason", ""))
        return obj

    def _cancel(request, obj, data):
        for line in obj.lines.exclude(status__in=["cancelled", "closed"]):
            services.cancel_line_balance(line, data["reason"], request.user)
        obj.status = "cancelled" if not obj.lines.filter(dispatched_qty__gt=0).exists() else "closed"
        obj.cancel_reason = data["reason"]
        obj.row_version += 1
        obj.save()
        audit.record("so_cancelled", "sales.salesorder", obj, reason=data["reason"])
        return obj

    actions = [
        Action("confirm", "Confirm order", _confirm, tone="primary", available=lambda o, r: o.status == "draft"),
        Action("commercial_approve", "Commercial approval", _commercial_approve, permission="sales.so.approve_amendment",
               available=lambda o, r: not o.commercial_approved and o.status != "cancelled"),
        Action("record_advance", "Record advance received", _record_advance, permission="dispatch.credit_release", reason_required=True,
               fields=[{"name": "amount", "label": "Advance received (INR)", "type": "decimal", "required": True}]),
        Action("credit_release", "Release for dispatch on credit", _credit_release, permission="dispatch.credit_release", reason_required=True,
               available=lambda o, r: not o.credit_released),
        Action("cancel", "Cancel order balance", _cancel, reason_required=True, tone="danger",
               confirm="Produced and dispatched quantities stay recorded and will need disposition.",
               available=lambda o, r: o.status in {"draft", "confirmed"}),
    ]


@register
class SOLineResource(Resource):
    key = "sales.soline"
    in_nav = False
    model = SOLine
    label = "SO lines"
    singular = "Order line"
    module = "sales"
    title_field = "line_no"
    ordering = ["so", "line_no"]
    list_fields = ["so", "line_no", "product", "scope", "flavour", "qty", "unit_price", "committed_date", "status", "dispatched_qty", "at_risk"]
    create_only_fields = ["so", "product", "scope"]
    form_fields = ["flavour", "container_artwork", "lid_artwork", "container_colour", "lid_colour", "masterbatch", "impact_modifier", "iml", "qty",
                   "unit_price", "requested_date", "committed_date", "packing_rule", "priority", "special_instructions"]
    readonly_fields = ["cancelled_qty", "risk_note"]
    filter_fields = ["so", "status", "product", "at_risk"]
    search_fields = ["so__doc_no", "product__code", "flavour"]
    field_permissions = {"unit_price": PRICE}
    depends = {
        "container_artwork": {"product": "product", "component_type": "=container"},
        "lid_artwork": {"product": "product", "component_type": "=lid"},
        "masterbatch": {"kind": "=masterbatch"},
        "packing_rule": {"product": "product"},
    }
    field_help = {
        "scope": "Complete set needs every approved component; container-only can dispatch without a lid.",
        "masterbatch": "Colour follows the SO. A different colour requires an SO amendment.",
    }

    def outbox_source(self, obj):
        return "sales.salesorder", obj.so_id

    def scope(self, request, qs):
        levels = role_levels(request.user)
        if levels & {"owner", "manager"} or not has_perm(request.user, "sales.salesorder.create"):
            return qs
        return qs.filter(so__salesperson=request.user)

    def before_create(self, request, data):
        so = data["so"]
        if so.is_locked:
            raise BusinessRuleError("Order is locked; new lines need an amendment approval.", "record_locked")
        if so.status not in {"draft", "confirmed"}:
            raise BusinessRuleError("Order is not open.", "invalid_state")
        data["line_no"] = (so.lines.aggregate(m=Max("line_no"))["m"] or 0) + 1

    def create(self, request, data):
        line = SOLine(**data, created_by=request.user)
        services.validate_line(line)
        line.full_clean()
        line.save()
        if line.so.status == "confirmed":
            services.regenerate_demand(line)
        return line

    def edit_block_reason(self, request, obj):
        if obj.status != "open":
            return f"Line is {obj.status}."
        if obj.so.is_locked:
            return "Order locked after 24 hours - use 'Request amendment'."
        return None

    def after_update(self, request, obj, changes):
        services.validate_line(obj)
        if obj.so.status == "confirmed":
            services.regenerate_demand(obj)

    def extra_detail(self, request, obj):
        return {"quantities": services.quantities(obj), "readiness": services.readiness(obj),
                "demands": list(obj.demands.values("component_type", "item__code", "required_qty", "active", "artwork__code"))}

    def _request_amendment(request, obj, data):
        changes = {}
        for f in ["qty", "committed_date", "requested_date", "flavour", "container_artwork", "lid_artwork", "masterbatch", "special_instructions"]:
            if data.get(f) not in (None, ""):
                changes[f] = data[f]
        if not changes:
            raise BusinessRuleError("Enter at least one new value.", "validation")
        if "qty" in changes:
            changes["qty"] = int(changes["qty"])
        approval = services.request_line_amendment(obj, changes, data["reason"], request.user)
        return {"ok": True, "approval": str(approval.pk), "message": "Amendment sent for approval. The current version stays in force until approved."}

    def _cancel_balance(request, obj, data):
        return services.cancel_line_balance(obj, data["reason"], request.user)

    def _allocate(request, obj, data):
        """Reserve compatible QC-released stock for this line (p.7, p.15)."""
        results = []
        for dem in obj.demands.filter(active=True).select_related("item", "artwork"):
            lot_filter = {"lot__product": obj.product, "lot__component_type": dem.component_type}
            if dem.artwork_id:
                lot_filter["lot__artwork"] = dem.artwork
            if obj.flavour:
                lot_filter["lot__flavour"] = obj.flavour
            already = sum((r.open_qty for r in obj.reservations.filter(item=dem.item, status="active")), 0)
            need = dem.required_qty - already
            for bal in usable_lot_balances(dem.item, lot_filter=lot_filter):
                if need <= 0:
                    break
                free = lot_free_qty(bal.lot)
                take = min(free, need)
                if take > 0:
                    reserve(item=dem.item, qty=take, user=request.user, so_line=obj, lot=bal.lot, note=f"Allocation {obj}")
                    need -= take
            results.append({"component": dem.component_type, "required": dem.required_qty, "allocated_now": str(dem.required_qty - already - max(need, 0)), "still_to_produce": str(max(need, 0))})
        audit.record("so_line_allocated", "sales.soline", obj, changes={"result": results})
        return {"ok": True, "allocation": results}

    actions = [
        Action("request_amendment", "Request amendment", _request_amendment, permission="sales.so.amend_locked", reason_required=True,
               fields=[
                   {"name": "qty", "label": "New quantity", "type": "integer"},
                   {"name": "committed_date", "label": "New committed date", "type": "date"},
                   {"name": "requested_date", "label": "New requested date", "type": "date"},
                   {"name": "flavour", "label": "New flavour", "type": "text"},
                   {"name": "container_artwork", "label": "New container artwork", "type": "fk", "target": "masters.artwork"},
                   {"name": "lid_artwork", "label": "New lid artwork", "type": "fk", "target": "masters.artwork"},
                   {"name": "masterbatch", "label": "New masterbatch", "type": "fk", "target": "masters.item"},
                   {"name": "special_instructions", "label": "Instructions", "type": "textarea"},
               ],
               available=lambda o, r: o.status == "open"),
        Action("cancel_balance", "Cancel balance", _cancel_balance, reason_required=True, tone="danger", available=lambda o, r: o.status == "open"),
        Action("allocate", "Allocate released stock", _allocate, permission="fg.dispatch.create", available=lambda o, r: o.status == "open"),
    ]


@register
class ComponentDemandResource(Resource):
    key = "sales.componentdemand"
    model = ComponentDemand
    label = "Component demand"
    singular = "Component demand"
    module = "sales"
    title_field = "component_type"
    allow_create = False
    allow_update = False
    list_fields = ["so_line", "component_type", "item", "artwork", "required_qty", "active"]
    filter_fields = ["so_line", "component_type", "item", "active"]
    ordering = ["-created_at"]


@register
class SOVersionResource(Resource):
    key = "sales.soversion"
    in_nav = False
    model = SOVersion
    label = "SO versions"
    singular = "SO version"
    module = "sales"
    title_field = "version"
    allow_create = False
    allow_update = False
    list_fields = ["so", "version", "reason", "approval", "created_by", "created_at"]
    readonly_fields = ["snapshot"]
    field_permissions = {"snapshot": PRICE}
    filter_fields = ["so"]
    ordering = ["so", "-version"]
