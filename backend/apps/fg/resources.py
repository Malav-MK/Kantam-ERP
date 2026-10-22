from django.utils import timezone

from apps.core import audit
from apps.core.api import BusinessRuleError
from apps.core.registry import Action, Resource, register
from apps.core.resource_api import scoped_get
from apps.core.services import notify_perm

from . import services
from .models import DeliveryException, Dispatch, DispatchLine, PackingRecord


def _pack(request, obj, data):
    product = scoped_get(request, "masters.product", data.get("product"), "product")
    location = scoped_get(request, "masters.location", data.get("location"), "location")
    so_line = scoped_get(request, "sales.soline", data["so_line"], "SO line") if data.get("so_line") else None
    rule = so_line.packing_rule if so_line and so_line.packing_rule_id else product.packing_rules.filter(active=True).order_by("-revision").first()
    return services.pack_sets(product=product, sets=data.get("sets"), location=location, user=request.user, flavour=data.get("flavour", ""),
                              so_line=so_line, packer_name=data.get("packer_name", ""), packing_rule=rule)


def _matching(request, obj, data):
    product = scoped_get(request, "masters.product", data.get("product"), "product")
    return {"product": product.code, "variants": services.matching_summary(product)}


@register
class PackingRecordResource(Resource):
    key = "fg.packingrecord"
    model = PackingRecord
    label = "Packing"
    singular = "Packing record"
    module = "fg"
    allow_create = False
    allow_update = False
    list_fields = ["doc_no", "product", "flavour", "so_line", "sets_packed", "packed_lot", "cartons", "partial_carton_pcs", "packer_name", "status", "created_at"]
    readonly_fields = ["consumption", "location", "bags"]
    filter_fields = ["product", "so_line", "status"]
    search_fields = ["doc_no", "packed_lot__doc_no"]
    help = {
        "why": "Complete sets consume compatible released components; packed and loose stock are never the same pieces twice.",
        "boundary": "Excess lids do not cover a container shortage. Matching is by compatible variant (flavour), not nominal size.",
    }

    def _unpack(request, obj, data):
        return services.unpack(obj, request.user, data["reason"])

    actions = [
        Action("pack", "Pack sets", _pack, permission="fg.packingrecord.create", detail=False, tone="primary",
               fields=[{"name": "product", "label": "Product", "type": "fk", "target": "masters.product", "required": True},
                       {"name": "so_line", "label": "SO line (optional)", "type": "fk", "target": "sales.soline", "depends": {"product": "product", "status": "=open"}},
                       {"name": "flavour", "label": "Flavour (when no SO line)", "type": "text"},
                       {"name": "sets", "label": "Sets to pack", "type": "integer", "required": True},
                       {"name": "location", "label": "FG location", "type": "fk", "target": "masters.location", "required": True, "depends": {"kind": "=fg,dispatch"}},
                       {"name": "packer_name", "label": "Packer", "type": "text"}]),
        Action("matching", "Component matching view", _matching, permission="fg.packingrecord.view", detail=False,
               fields=[{"name": "product", "label": "Product", "type": "fk", "target": "masters.product", "required": True}]),
        Action("unpack", "Unpack (reverse)", _unpack, permission="fg.packingrecord.create", reason_required=True, tone="danger",
               available=lambda o, r: o.status == "posted"),
    ]

    def permission_codes(self):
        codes = super().permission_codes()
        codes["fg.packingrecord.create"] = "Post packing"
        return codes


@register
class DispatchResource(Resource):
    key = "fg.dispatch"
    server_defaults = ["planned_date"]
    model = Dispatch
    label = "Dispatches"
    singular = "Dispatch"
    module = "fg"
    list_fields = ["doc_no", "so", "customer", "planned_date", "status", "vehicle_no", "transporter", "invoice_ref", "posted_at"]
    create_only_fields = ["so"]
    form_fields = ["planned_date", "destination", "transporter", "vehicle_no", "driver_name", "driver_phone", "freight_basis", "invoice_ref",
                   "eway_bill", "loading_checks", "monsoon_tarpaulin_ok", "total_weight_kg"]
    readonly_fields = ["approved_by", "departed_at", "delivered_at", "delivery_note"]
    filter_fields = ["status", "customer", "so"]
    search_fields = ["doc_no", "vehicle_no", "invoice_ref", "so__doc_no"]
    field_permissions = {"driver_phone": "hr.personal.view"}
    inlines = [{"resource": "fg.dispatchline", "fk": "dispatch", "label": "Picked batches"}, {"resource": "fg.deliveryexception", "fk": "dispatch", "label": "Delivery exceptions"}]
    depends = {"so": {"status": "=confirmed"}}
    help = {
        "steps": "Ready order -> Allocate/pick batches -> Verify packing/QC -> Approve loading -> Post dispatch -> Record delivery -> Resolve exceptions.",
        "boundary": "Held, insufficient or incompatible goods and unmet commercial gates block release. Stock is relieved once, at posting.",
    }

    def before_create(self, request, data):
        so = data["so"]
        if so.status != "confirmed":
            raise BusinessRuleError("Only confirmed orders can be dispatched.", "invalid_state")
        data["customer"] = so.customer
        data.setdefault("destination", so.delivery_address)
        data.setdefault("freight_basis", so.freight_basis)
        data.setdefault("planned_date", timezone.localdate())

    def edit_block_reason(self, request, obj):
        return None if obj.status in {"planned", "picked", "verified"} else f"Dispatch is {obj.get_status_display()}."

    def extra_detail(self, request, obj):
        return {"blockers": services.validate_dispatch(obj) if obj.status not in {"posted", "delivered", "cancelled"} else []}

    def _step(target, require):
        def handler(request, obj, data):
            if obj.status != require:
                raise BusinessRuleError(f"Dispatch must be '{require}' first.", "invalid_state")
            if target in {"verified", "approved"}:
                blockers = services.validate_dispatch(obj)
                if blockers:
                    raise BusinessRuleError(" | ".join(blockers), "dispatch_blocked", {"blockers": blockers})
            if target == "approved":
                checks = obj.loading_checks or {}
                if checks.get("monsoon_season") and not obj.monsoon_tarpaulin_ok:
                    raise BusinessRuleError("Monsoon shipment: confirm vehicle/tarpaulin check.", "loading_check")
                if not obj.vehicle_no:
                    raise BusinessRuleError("Vehicle number required before loading approval.", "validation")
                obj.approved_by = request.user
            obj.status = target
            obj.row_version += 1
            obj.save()
            audit.record(f"dispatch_{target}", "fg.dispatch", obj)
            return obj
        return handler

    def _post(request, obj, data):
        return services.post_dispatch(obj, request.user)

    def _deliver(request, obj, data):
        obj.status = "delivered"
        obj.delivered_at = timezone.now()
        obj.delivery_note = data.get("delivery_note", "")
        obj.save()
        audit.record("dispatch_delivered", "fg.dispatch", obj)
        return obj

    def _exception(request, obj, data):
        exc = DeliveryException.objects.create(dispatch=obj, kind=data["kind"], qty=int(data.get("qty") or 0), description=data.get("description", ""),
                                               action=data.get("action", "none"), created_by=request.user)
        audit.record("delivery_exception", "fg.dispatch", obj, changes={"kind": exc.kind, "qty": exc.qty, "action": exc.action})
        notify_perm("quality.complaint.create", f"Delivery {exc.get_kind_display()} on {obj.doc_no}", severity="warning", resource="fg.dispatch",
                    object_id=obj.pk, next_action="Open complaint / arrange replacement", dedupe_key=f"dex:{exc.pk}")
        return exc

    def _cancel(request, obj, data):
        obj.status = "cancelled"
        obj.save()
        audit.record("dispatch_cancelled", "fg.dispatch", obj, reason=data["reason"])
        return obj

    actions = [
        Action("pick", "Mark picked", _step("picked", "planned"), permission="fg.dispatch.update"),
        Action("verify", "Verify packing / QC", _step("verified", "picked"), permission="fg.dispatch.update"),
        Action("approve", "Approve loading", _step("approved", "verified"), permission="fg.dispatch.update", tone="primary"),
        Action("post", "Post dispatch", _post, permission="fg.dispatch.update", tone="primary", available=lambda o, r: o.status == "approved",
               confirm="Stock and SO balance will be relieved now. The gate records physical departure separately."),
        Action("deliver", "Record delivery", _deliver, permission="fg.dispatch.update",
               fields=[{"name": "delivery_note", "label": "Delivery evidence / POD", "type": "textarea"}], available=lambda o, r: o.status == "posted"),
        Action("exception", "Report shortage / damage / return", _exception, permission="fg.dispatch.update",
               fields=[{"name": "kind", "label": "Type", "type": "choice", "choices": [{"value": v, "label": l} for v, l in DeliveryException.KINDS], "required": True},
                       {"name": "qty", "label": "Quantity", "type": "integer"},
                       {"name": "action", "label": "Action", "type": "choice", "choices": [{"value": v, "label": l} for v, l in DeliveryException.ACTIONS]},
                       {"name": "description", "label": "Description", "type": "textarea", "required": True}],
               available=lambda o, r: o.status in {"posted", "delivered"}),
        Action("cancel", "Cancel", _cancel, permission="fg.dispatch.update", reason_required=True, tone="danger",
               available=lambda o, r: o.status in {"planned", "picked", "verified", "approved"}),
    ]


@register
class DispatchLineResource(Resource):
    key = "fg.dispatchline"
    in_nav = False
    model = DispatchLine
    label = "Dispatch lines"
    singular = "Dispatch line"
    module = "fg"
    title_field = "lot"
    ordering = ["dispatch", "created_at"]
    list_fields = ["dispatch", "so_line", "lot", "qty", "cartons"]
    create_only_fields = ["dispatch", "so_line", "lot"]
    form_fields = ["qty", "cartons"]
    filter_fields = ["dispatch", "so_line"]
    depends = {"so_line": {"so": "@parent.so", "status": "=open"}, "lot": {"quality_state": "=released"}}

    def before_create(self, request, data):
        if data["dispatch"].status not in {"planned", "picked"}:
            raise BusinessRuleError("Lines can only be picked while planned/picked.", "record_locked")
        if data["so_line"].so_id != data["dispatch"].so_id:
            raise BusinessRuleError("SO line belongs to another order.", "incompatible_line")

    def edit_block_reason(self, request, obj):
        return None if obj.dispatch.status in {"planned", "picked"} else "Dispatch past picking."


@register
class DeliveryExceptionResource(Resource):
    key = "fg.deliveryexception"
    model = DeliveryException
    label = "Delivery exceptions"
    singular = "Delivery exception"
    module = "fg"
    title_field = "kind"
    allow_create = False
    list_fields = ["dispatch", "kind", "qty", "action", "status", "complaint", "created_at"]
    form_fields = ["action", "status", "complaint", "description"]
    filter_fields = ["status", "kind", "dispatch"]
