from decimal import Decimal

from django.db.models import Sum

from apps.core import audit
from apps.core.api import BusinessRuleError
from apps.core.registry import Action, Resource, register
from apps.core.services import approval_handler, request_approval
from apps.masters.models import Item, Location
from apps.planning.models import ProductionRequest

from . import services
from .models import Lot, Reservation, StockBalance, StockCount, StockMove

ISSUE_FIELDS = [
    {"name": "pr", "label": "Shift PR", "type": "fk", "target": "planning.productionrequest", "required": True, "filter": {"status": "released,in_progress"}},
    {"name": "item", "label": "Material", "type": "fk", "target": "masters.item", "required": True},
    {"name": "lot", "label": "Lot", "type": "fk", "target": "inventory.lot", "required": True, "depends": {"item": "item", "quality_state": "=released"}},
    {"name": "location", "label": "From location", "type": "fk", "target": "masters.location", "required": True},
    {"name": "qty", "label": "Quantity", "type": "decimal", "required": True},
    {"name": "receiver_name", "label": "Received by (machine side)", "type": "text"},
]


KEYS = {ProductionRequest: "planning.productionrequest", Item: "masters.item", Lot: "inventory.lot", Location: "masters.location"}


def _get(model, pk, label, request=None):
    from apps.core.resource_api import scoped_get
    return scoped_get(request, KEYS[model], pk, label)


def _issue(request, obj, data):
    pr = _get(ProductionRequest, data.get("pr"), "PR", request)
    item = _get(Item, data.get("item"), "material", request)
    lot = _get(Lot, data.get("lot"), "lot", request)
    loc = _get(Location, data.get("location"), "location", request)
    move = services.issue_to_pr(pr=pr, item=item, lot=lot, qty=Decimal(str(data.get("qty"))), from_location=loc, user=request.user,
                                receiver_name=data.get("receiver_name", ""))
    audit.record("material_issued", "inventory.stockmove", move, changes={"pr": pr.doc_no, "item": item.code, "lot": lot.doc_no, "qty": str(move.qty)})
    return move


def _return(request, obj, data):
    pr = _get(ProductionRequest, data.get("pr"), "PR", request)
    item = _get(Item, data.get("item"), "material", request)
    lot = _get(Lot, data.get("lot"), "lot", request)
    loc = _get(Location, data.get("location"), "location", request)
    move = services.return_from_pr(pr=pr, item=item, lot=lot, qty=Decimal(str(data.get("qty"))), to_location=loc, user=request.user,
                                   reason=data.get("reason", ""))
    audit.record("material_returned", "inventory.stockmove", move, changes={"pr": pr.doc_no, "item": item.code, "qty": str(move.qty)})
    return move


def _transfer(request, obj, data):
    lot = _get(Lot, data.get("lot"), "lot", request)
    src = _get(Location, data.get("from_location"), "source", request)
    dst = _get(Location, data.get("to_location"), "destination", request)
    if src.is_virtual or dst.is_virtual or "machine_side" in {src.kind, dst.kind}:
        raise BusinessRuleError("Transfers move stock between physical store locations; use issue/return for machines.", "invalid_move")
    move = services.post_move("transfer", item=lot.item, lot=lot, qty=data.get("qty"), from_location=src, to_location=dst, user=request.user,
                              reason=data.get("reason", ""))
    audit.record("stock_transferred", "inventory.stockmove", move)
    return move


def _request_additional(request, obj, data):
    pr = _get(ProductionRequest, data.get("pr"), "PR", request)
    item = _get(Item, data.get("item"), "material", request)
    approval = request_approval(
        kind="additional_issue", resource="planning.productionrequest", obj=pr, summary=f"Additional {item.code} for {pr.doc_no}",
        payload={"pr": str(pr.pk), "item": str(item.pk), "lot": data.get("lot"), "location": data.get("location"), "qty": str(data.get("qty"))},
        reason=data["reason"], required_permission="inventory.additional_issue.approve", requester=request.user,
    )
    if approval.status == "approved":
        return {"ok": True, "approval": str(approval.pk), "message": "Additional issue posted (owner approval)."}
    return {"ok": True, "approval": str(approval.pk), "message": "Additional issue sent for approval."}


@approval_handler("additional_issue")
def _apply_additional(approval, approver):
    p = approval.payload
    pr = ProductionRequest.objects.get(pk=p["pr"])
    services.issue_to_pr(pr=pr, item=Item.objects.get(pk=p["item"]), lot=Lot.objects.get(pk=p["lot"]), qty=Decimal(p["qty"]),
                         from_location=Location.objects.get(pk=p["location"]), user=approval.created_by,
                         additional_reason=f"Approved additional issue: {approval.reason}", approval=approval)


@register
class StockMoveResource(Resource):
    key = "inventory.stockmove"
    model = StockMove
    label = "Stock ledger"
    singular = "Movement"
    module = "inventory"
    allow_create = False
    allow_update = False
    list_fields = ["doc_no", "event_time", "move_type", "item", "lot", "from_location", "to_location", "qty", "uom", "pr", "ref_doc", "created_by"]
    readonly_fields = ["reason", "reversal_of", "receiver_name", "shift_date", "shift", "so_line"]
    filter_fields = ["move_type", "item", "lot", "pr", "from_location", "to_location", "shift_date"]
    search_fields = ["doc_no", "ref_doc", "item__code", "lot__doc_no"]
    ordering = ["-event_time"]
    help = {
        "why": "One line per material / lot / location transaction - a summary row never hides repeated issues or lots.",
        "boundary": "Posted movements are reversed with a link, never edited or deleted. Negative stock is blocked.",
    }

    def _reverse(request, obj, data):
        return services.reverse_move(obj, request.user, data["reason"])

    actions = [
        Action("issue", "Issue to shift PR", _issue, permission="inventory.issue.create", detail=False, tone="primary", fields=ISSUE_FIELDS),
        Action("return", "Return from machine", _return, permission="inventory.return.create", detail=False,
               fields=[f if f["name"] != "location" else {**f, "label": "Return to location"} for f in ISSUE_FIELDS if f["name"] != "receiver_name"]
               + [{"name": "reason", "label": "Reason / cancelled PR note", "type": "text"}]),
        Action("transfer", "Location transfer", _transfer, permission="inventory.stockmove.create", detail=False,
               fields=[{"name": "lot", "label": "Lot", "type": "fk", "target": "inventory.lot", "required": True},
                       {"name": "from_location", "label": "From", "type": "fk", "target": "masters.location", "required": True},
                       {"name": "to_location", "label": "To", "type": "fk", "target": "masters.location", "required": True},
                       {"name": "qty", "label": "Quantity", "type": "decimal", "required": True},
                       {"name": "reason", "label": "Remarks", "type": "text"}]),
        Action("request_additional", "Request additional issue", _request_additional, permission="inventory.issue.create", detail=False,
               reason_required=True, fields=ISSUE_FIELDS[:5]),
        Action("reverse", "Reverse movement", _reverse, permission="inventory.adjust.approve", reason_required=True, tone="danger",
               available=lambda o, r: o.move_type in {"transfer", "issue", "return", "adjustment"} and not hasattr(o, "reversed_by")),
    ]

    # Ledger actions do not have dedicated generated permissions beyond these:
    def permission_codes(self):
        codes = super().permission_codes()
        codes["inventory.issue.create"] = "Issue material to shift PR"
        codes["inventory.return.create"] = "Return material from machine"
        codes["inventory.stockmove.create"] = "Location transfers"
        return codes


@register
class LotResource(Resource):
    key = "inventory.lot"
    model = Lot
    label = "Lots & batches"
    singular = "Lot"
    module = "inventory"
    allow_create = False
    list_fields = ["doc_no", "item", "artwork", "product", "component_type", "flavour", "quality_state", "source", "supplier_lot", "pr", "shift_date", "excess"]
    form_fields = ["notes"]
    readonly_fields = ["parent", "grn_line", "produced_at", "shift"]
    filter_fields = ["item", "quality_state", "artwork", "product", "component_type", "flavour", "source", "pr", "excess"]
    search_fields = ["doc_no", "supplier_lot", "item__code"]
    help = {"why": "Lots carry quality state and genealogy so a complaint traces back to machine, shift, polymer and label lots."}

    def extra_detail(self, request, obj):
        from .trace import trace_lot
        bals = StockBalance.objects.filter(lot=obj, qty__gt=0).values("location__code", "location__kind", "qty")
        return {"balances": list(bals), "on_hand": str(services.lot_on_hand(obj)), "free": str(services.lot_free_qty(obj)), "trace": trace_lot(request, obj)}


@register
class StockBalanceResource(Resource):
    key = "inventory.stockbalance"
    model = StockBalance
    label = "Stock on hand"
    singular = "Balance"
    module = "inventory"
    title_field = "item"
    allow_create = False
    allow_update = False
    list_fields = ["item", "lot", "location", "qty", "lot_state", "lot_artwork", "updated_at"]
    filter_fields = ["item", "location", "lot"]
    search_fields = ["item__code", "item__name", "lot__doc_no", "location__code"]
    ordering = ["item__code", "location__code"]

    def scope(self, request, qs):
        return qs.filter(qty__gt=0).select_related("item", "lot", "location")

    def extra_detail(self, request, obj):
        return {"summary": {k: str(v) for k, v in services.balance_summary(obj.item).items()}}


StockBalance.lot_state = property(lambda self: self.lot.get_quality_state_display())
StockBalance.lot_artwork = property(lambda self: str(self.lot.artwork) if self.lot.artwork_id else "")


@register
class ReservationResource(Resource):
    key = "inventory.reservation"
    model = Reservation
    label = "Reservations"
    singular = "Reservation"
    module = "inventory"
    title_field = "item"
    allow_create = False
    allow_update = False
    list_fields = ["item", "lot", "qty", "consumed_qty", "so_line", "pr", "status", "created_by", "created_at"]
    filter_fields = ["item", "status", "so_line", "pr", "lot"]

    def _release(request, obj, data):
        obj.status = "released"
        obj.note = data["reason"][:200]
        obj.save()
        audit.record("reservation_released", "inventory.reservation", obj, reason=data["reason"])
        return obj

    actions = [Action("release", "Release reservation", _release, permission="inventory.adjust.approve", reason_required=True,
                      available=lambda o, r: o.status == "active")]


@register
class StockCountResource(Resource):
    key = "inventory.stockcount"
    model = StockCount
    label = "Physical counts"
    singular = "Stock count"
    module = "inventory"
    list_fields = ["doc_no", "location", "item", "lot", "system_qty", "counted_qty", "variance", "status", "created_by"]
    create_only_fields = ["location", "item", "lot", "counted_qty"]
    form_fields = ["reason"]
    filter_fields = ["status", "location", "item"]

    def before_create(self, request, data):
        bal = StockBalance.objects.filter(item=data["item"], lot=data["lot"], location=data["location"]).first()
        data["system_qty"] = bal.qty if bal else Decimal("0")
        data["variance"] = data["counted_qty"] - data["system_qty"]

    def edit_block_reason(self, request, obj):
        return None if obj.status == "draft" else f"Count is {obj.status}."

    def _submit(request, obj, data):
        if not obj.variance:
            obj.status = "approved"
            obj.save()
            return obj
        obj.status = "submitted"
        obj.save()
        request_approval(kind="stock_adjustment", resource="inventory.stockcount", obj=obj, summary=f"Count variance {obj.variance} {obj.item.code}",
                         payload={"count": str(obj.pk), "variance": str(obj.variance)}, reason=obj.reason or data.get("reason", "Physical count variance"),
                         required_permission="inventory.adjust.approve", requester=request.user)
        return obj

    actions = [Action("submit", "Submit count", _submit, permission="inventory.stockcount.create", tone="primary", available=lambda o, r: o.status == "draft")]


@approval_handler("stock_adjustment")
def _apply_count(approval, approver):
    count = StockCount.objects.select_for_update().get(pk=approval.payload["count"])
    bal = StockBalance.objects.filter(item=count.item, lot=count.lot, location=count.location).first()
    current = bal.qty if bal else Decimal("0")
    variance = count.counted_qty - current  # recompute against the live balance
    adj = services.virtual_location("adjustment")
    if variance:
        move = services.post_move("adjustment", item=count.item, lot=count.lot, qty=abs(variance),
                                  from_location=adj if variance > 0 else count.location, to_location=count.location if variance > 0 else adj,
                                  user=approver, reason=f"Count {count.doc_no}: {approval.reason}",
                                  ref={"resource": "inventory.stockcount", "id": count.pk, "doc": count.doc_no})
        count.adjustment_move = move
    count.status = "approved"
    count.save()


def _current_stock_summary(request, obj, data):
    item = _get(Item, data.get("item"), "item", request)
    s = services.balance_summary(item)
    return {k: str(v) for k, v in s.items()} | {"item": item.code, "uom": item.stock_uom}


StockBalanceResource.actions = [
    Action("summary", "Balance summary", _current_stock_summary, permission="inventory.stockbalance.view", detail=False,
           fields=[{"name": "item", "label": "Item", "type": "fk", "target": "masters.item", "required": True}]),
]


def label_store_summary():
    """Label stock by design/group/revision (p.8)."""
    rows = (StockBalance.objects.filter(item__kind="label", qty__gt=0)
            .values("item__group", "lot__artwork__code", "lot__artwork__revision", "lot__artwork__flavour", "lot__artwork__component_type",
                    "lot__artwork__status", "lot__quality_state")
            .annotate(qty=Sum("qty")).order_by("item__group", "lot__artwork__code"))
    return list(rows)
