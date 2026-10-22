"""
Matching, packing and dispatch (brief p.15-16, p.67).

* Potential sets = smallest eligible component balance (per compatible variant).
* Packing consumes component stock and creates packed-set stock in one posting,
  so the same pieces are never counted as both loose components and packed FG.
* Dispatch reduces released FG and the SO balance in one posting; the gate only
  records the physical departure.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.core import audit, calc
from apps.core.api import BusinessRuleError
from apps.core.services import draft_outbox, notify_perm
from apps.inventory import services as inv
from apps.inventory.models import Lot, Reservation, StockBalance

from .models import Dispatch, PackingRecord


def matching_summary(product):
    """Container/lid/handle balances by compatible variant (flavour), with potential sets and imbalance (p.15)."""
    comps = list(product.components.select_related("item"))
    flavours = set(Lot.objects.filter(product=product, quality_state="released").values_list("flavour", flat=True))
    rows = []
    for flavour in sorted(flavours):
        balances = {}
        for c in comps:
            q = (StockBalance.objects.filter(item=c.item, lot__product=product, lot__flavour=flavour, lot__quality_state="released", qty__gt=0)
                 .exclude(location__kind__in=inv.STORAGE_EXCLUDED_FROM_USABLE).aggregate(t=Sum("qty"))["t"] or 0)
            reserved = Reservation.objects.filter(item=c.item, lot__product=product, lot__flavour=flavour, status="active").aggregate(t=Sum("qty"))["t"] or 0
            balances[c.component_type] = {"released": Decimal(q), "reserved": Decimal(reserved), "qty_per_set": c.qty_per_set}
        sets = calc.matchable_sets({k: v["released"] for k, v in balances.items()}, {k: v["qty_per_set"] for k, v in balances.items()})
        imbalance = {k: str(v["released"] - sets * v["qty_per_set"]) for k, v in balances.items()}
        rows.append({"flavour": flavour or "(none)", "components": {k: {kk: str(vv) for kk, vv in v.items()} for k, v in balances.items()},
                     "potential_sets": sets, "excess_after_matching": imbalance})
    return rows


def _take_from_lots(item, product, flavour, qty, so_line, user, ref):
    """Consume component qty FIFO from released lots, preferring lots reserved to the SO line."""
    remaining = Decimal(qty)
    consumed = []
    balances = list(inv.usable_lot_balances(item, lot_filter={"lot__product": product, "lot__flavour": flavour}))
    if so_line is not None:
        reserved_lots = set(Reservation.objects.filter(so_line=so_line, status="active", item=item).values_list("lot_id", flat=True))
        balances.sort(key=lambda b: (b.lot_id not in reserved_lots, b.lot.created_at))
    prod_loc = inv.virtual_location("production")
    for bal in balances:
        if remaining <= 0:
            break
        others = Reservation.objects.filter(lot=bal.lot, status="active")
        if so_line is not None:
            others = others.exclude(so_line=so_line)
        blocked = sum((r.open_qty for r in others), Decimal("0"))
        usable = max(bal.qty - blocked, Decimal("0"))
        take = min(usable, remaining)
        if take <= 0:
            continue
        inv.post_move("pack_consume", item=item, lot=bal.lot, qty=take, from_location=bal.location, to_location=prod_loc, user=user,
                      so_line=so_line, ref=ref)
        if so_line is not None:
            inv.consume_reservation(item=item, qty=take, so_line=so_line, lot=bal.lot)
        consumed.append((bal.lot, take))
        remaining -= take
    if remaining > 0:
        raise BusinessRuleError(f"Not enough released, compatible {item.code} ({flavour or 'no flavour'}) - short by {remaining}.", "insufficient_components")
    return consumed


def pack_sets(*, product, sets, location, user, flavour="", so_line=None, packer_name="", packing_rule=None):
    if not product.set_item_id:
        raise BusinessRuleError("Product has no packed-set FG item configured.", "set_item_missing")
    sets = int(sets)
    if sets <= 0:
        raise BusinessRuleError("Sets must be positive.", "invalid_qty")
    if so_line is not None:
        if so_line.product_id != product.pk or so_line.scope != "set":
            raise BusinessRuleError("SO line is not a complete-set line for this product.", "incompatible_line")
        flavour = so_line.flavour
    comps = list(product.components.select_related("item"))
    if not comps:
        raise BusinessRuleError("Product BOM has no components.", "bom_missing")
    record = PackingRecord.objects.create(product=product, so_line=so_line, flavour=flavour, sets_packed=sets, location=location,
                                          packer_name=packer_name, created_by=user, owner=user)
    ref = {"resource": "fg.packingrecord", "id": record.pk, "doc": record.doc_no}
    consumption, parents = [], []
    for c in comps:
        for lot, qty in _take_from_lots(c.item, product, flavour, sets * c.qty_per_set, so_line, user, ref):
            consumption.append({"component": c.component_type, "lot": lot.doc_no, "qty": str(qty)})
            parents.append((lot, qty))
    packed = inv.create_lot(item=product.set_item, source="packing", user=user, quality_state="released", product=product, flavour=flavour,
                            produced_at=timezone.now())
    inv.post_move("pack_output", item=product.set_item, lot=packed, qty=sets, from_location=inv.virtual_location("production"),
                  to_location=location, user=user, so_line=so_line, ref=ref)
    inv.link_genealogy(parents, packed, relation="packed")
    if packing_rule:
        pack = calc.packing_requirement(sets, packing_rule.pcs_per_bag, packing_rule.pcs_per_carton)
        record.pcs_per_carton = packing_rule.pcs_per_carton
        record.cartons = pack["cartons"] - (1 if pack["partial_carton_pcs"] else 0)
        record.partial_carton_pcs = pack["partial_carton_pcs"]
        record.bags = pack["bags"]
    if so_line is not None:
        inv.reserve(item=product.set_item, qty=sets, user=user, so_line=so_line, lot=packed, note=f"Packed for {so_line}")
    record.packed_lot = packed
    record.consumption = consumption
    record.save()
    audit.record("packed", "fg.packingrecord", record, changes={"sets": sets, "packed_lot": packed.doc_no, "consumed": consumption})
    return record


def unpack(record, user, reason):
    """Explicit unpack movement - genealogy preserved (p.15)."""
    if record.status != "posted":
        raise BusinessRuleError("Already reversed.", "invalid_state")
    lot = record.packed_lot
    on_hand = inv.lot_on_hand(lot)
    if on_hand < record.sets_packed:
        raise BusinessRuleError("Packed stock already partly dispatched; cannot unpack the full record.", "insufficient_stock")
    inv.release_reservations(so_line=record.so_line, reason="unpack") if record.so_line_id else None
    for bal in StockBalance.objects.filter(lot=lot, qty__gt=0).exclude(location__kind__in={"customer", "production", "adjustment", "supplier"}):
        inv.post_move("reversal", item=lot.item, lot=lot, qty=bal.qty, from_location=bal.location, to_location=inv.virtual_location("production"),
                      user=user, reason=f"Unpack {record.doc_no}: {reason}")
    for row in record.consumption:
        comp_lot = Lot.objects.get(doc_no=row["lot"])
        inv.post_move("reversal", item=comp_lot.item, lot=comp_lot, qty=Decimal(row["qty"]), from_location=inv.virtual_location("production"),
                      to_location=record.location, user=user, reason=f"Unpack {record.doc_no}: {reason}")
    record.status = "reversed"
    record.save()
    audit.record("unpacked", "fg.packingrecord", record, reason=reason)
    return record


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def _expected_item(so_line):
    if so_line.scope == "set":
        return so_line.product.set_item
    comp = so_line.product.components.filter(component_type=so_line.scope).first()
    return comp.item if comp else None


def commercial_gate(so):
    """Configured release requirement: advance received or accounts credit release (p.16)."""
    if so.credit_released:
        return None
    if so.advance_required_pct and so.advance_required_pct > 0:
        needed = so.order_value * so.advance_required_pct / 100
        if so.advance_received < needed:
            return f"Advance {so.advance_received} below required {needed:.2f} ({so.advance_required_pct}%). Accounts must record advance or release credit."
    return None


def validate_dispatch(dispatch):
    blockers = []
    so = dispatch.so
    if not dispatch.lines.exists():
        blockers.append("No dispatch lines.")
    per_line = defaultdict(int)
    for dl in dispatch.lines.select_related("lot", "so_line__product", "lot__item"):
        line, lot = dl.so_line, dl.lot
        per_line[line.pk] += dl.qty
        if line.so_id != so.pk:
            blockers.append(f"Line {line} belongs to another order.")
        if lot.quality_state != "released":
            blockers.append(f"Lot {lot.doc_no} is {lot.get_quality_state_display()} - QC hold prevents shipment.")
        exp = _expected_item(line)
        if exp is None or lot.item_id != exp.pk:
            blockers.append(f"Lot {lot.doc_no} ({lot.item.code}) is not the item required for {line} ({exp.code if exp else '?'}).")
        if lot.product_id and lot.product_id != line.product_id:
            blockers.append(f"Lot {lot.doc_no} is a different product.")
        if line.flavour and lot.flavour != line.flavour:
            blockers.append(f"Lot {lot.doc_no} flavour '{lot.flavour}' does not match '{line.flavour}' (wrong design).")
        if inv.lot_on_hand(lot) < dl.qty:
            blockers.append(f"Lot {lot.doc_no} has only {inv.lot_on_hand(lot)} on hand.")
    for line_id, qty in per_line.items():
        line = so.lines.get(pk=line_id)
        if qty > line.balance_qty:
            blockers.append(f"{line}: dispatch {qty} exceeds open balance {line.balance_qty}.")
    gate = commercial_gate(so)
    if gate:
        blockers.append(gate)
    return blockers


def post_dispatch(dispatch, user):
    if dispatch.status != "approved":
        raise BusinessRuleError("Loading must be approved before posting.", "invalid_state")
    blockers = validate_dispatch(dispatch)
    if blockers:
        raise BusinessRuleError("Dispatch blocked: " + " | ".join(blockers), "dispatch_blocked", {"blockers": blockers})
    customer_loc = inv.virtual_location("customer")
    ref = {"resource": "fg.dispatch", "id": dispatch.pk, "doc": dispatch.doc_no}
    for dl in dispatch.lines.select_related("lot", "so_line"):
        remaining = Decimal(dl.qty)
        for bal in StockBalance.objects.select_for_update().filter(lot=dl.lot, qty__gt=0).exclude(location__kind__in=inv.STORAGE_EXCLUDED_FROM_USABLE):
            if remaining <= 0:
                break
            take = min(bal.qty, remaining)
            inv.post_move("dispatch", item=dl.lot.item, lot=dl.lot, qty=take, from_location=bal.location, to_location=customer_loc, user=user,
                          so_line=dl.so_line, ref=ref)
            remaining -= take
        inv.consume_reservation(item=dl.lot.item, qty=dl.qty, so_line=dl.so_line, lot=dl.lot)
        line = type(dl.so_line).objects.select_for_update().get(pk=dl.so_line_id)
        line.dispatched_qty += dl.qty
        if line.balance_qty == 0:
            line.status = "closed"
        line.save()
    so = dispatch.so
    if not so.lines.filter(status="open").exists():
        so.status = "closed"
        so.save(update_fields=["status", "updated_at"])
    dispatch.status = "posted"
    dispatch.posted_at = timezone.now()
    dispatch.row_version += 1
    dispatch.save()
    audit.record("dispatch_posted", "fg.dispatch", dispatch, changes={"lines": [(str(l.so_line), l.lot.doc_no, l.qty) for l in dispatch.lines.all()]})
    contact = so.customer.contacts.filter(active=True, whatsapp_opt_in=True).exclude(phone="").first()
    if contact:
        draft_outbox(channel="whatsapp", recipient=contact.phone, template="dispatch_departed", source_resource="fg.dispatch", source_id=dispatch.pk,
                     source_version=dispatch.row_version, dedupe_key=f"dispatch-notice:{dispatch.pk}",
                     payload={"so": so.doc_no, "dispatch": dispatch.doc_no, "vehicle": dispatch.vehicle_no,
                              "qty": sum(l.qty for l in dispatch.lines.all())})
    notify_perm("gate.gatematerial.authorise", f"Dispatch {dispatch.doc_no} posted - vehicle {dispatch.vehicle_no}", resource="fg.dispatch",
                object_id=dispatch.pk, next_action="Authorise gate-out", dedupe_key=f"dispatch-gate:{dispatch.pk}")
    return dispatch
