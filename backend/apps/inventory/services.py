"""
Stock ledger postings.

All functions must be called inside a transaction. Balance rows are locked
with SELECT ... FOR UPDATE and the item row is locked before computing
availability, so two users can never allocate the same free stock and a
posting either fully commits or not at all (brief p.4).
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.core import audit
from apps.core.api import BusinessRuleError
from apps.core.shifts import shift_for
from apps.masters.models import Item, Location

from .models import Lot, LotGenealogy, Reservation, StockBalance, StockMove

STORAGE_EXCLUDED_FROM_USABLE = {"machine_side", "qc_hold", "scrap", "supplier", "customer", "production", "adjustment"}


def D(v):
    return v if isinstance(v, Decimal) else Decimal(str(v))


def virtual_location(kind):
    loc = Location.objects.filter(kind=kind, active=True).order_by("code").first()
    if loc is None:
        loc = Location.objects.create(code=f"V-{kind.upper()}", name=f"{kind.title()} (virtual)", kind=kind)
    return loc


def machine_side_location(machine):
    loc = Location.objects.filter(kind="machine_side", machine=machine).first()
    if loc is None:
        loc = Location.objects.create(code=f"MS-{machine.code}", name=f"Machine side {machine.code}", kind="machine_side", machine=machine)
    return loc


def _validate_qty(item, qty):
    qty = D(qty)
    if qty <= 0:
        raise BusinessRuleError("Quantity must be greater than zero.", "invalid_qty")
    if item.whole_units and qty != qty.to_integral_value():
        raise BusinessRuleError(f"{item.code} is counted in whole {item.stock_uom}; fractional quantity not allowed.", "whole_units")
    return qty


def _lock_balance(item, lot, location):
    bal = StockBalance.objects.select_for_update().filter(item=item, lot=lot, location=location).first()
    if bal is None:
        StockBalance.objects.get_or_create(item=item, lot=lot, location=location, defaults={"qty": 0})
        bal = StockBalance.objects.select_for_update().get(item=item, lot=lot, location=location)
    return bal


def post_move(move_type, *, item, lot, qty, from_location=None, to_location=None, user=None, pr=None, so_line=None,
              ref=None, reason="", event_time=None, issuer=None, receiver_name="", reversal_of=None):
    """Post one ledger movement. Virtual locations carry no balance."""
    qty = _validate_qty(item, qty)
    if lot.item_id != item.id:
        raise BusinessRuleError("Lot does not belong to this item.", "lot_mismatch")
    if from_location is None and to_location is None:
        raise BusinessRuleError("A movement needs a source or destination.", "invalid_move")
    if from_location is not None and not from_location.is_virtual:
        bal = _lock_balance(item, lot, from_location)
        if bal.qty < qty:
            raise BusinessRuleError(
                f"Insufficient stock of {item.code} lot {lot.doc_no} at {from_location.code}: {bal.qty} available, {qty} requested.",
                "negative_stock", {"available": str(bal.qty), "requested": str(qty)},
            )
        bal.qty -= qty
        bal.save(update_fields=["qty", "updated_at"])
    if to_location is not None and not to_location.is_virtual:
        bal = _lock_balance(item, lot, to_location)
        bal.qty += qty
        bal.save(update_fields=["qty", "updated_at"])
    event_time = event_time or timezone.now()
    slot = shift_for(event_time)
    move = StockMove.objects.create(
        move_type=move_type, item=item, lot=lot, from_location=from_location, to_location=to_location, qty=qty,
        uom=item.stock_uom, pr=pr, so_line=so_line, reason=reason[:300], event_time=event_time,
        shift_date=slot.shift_date, shift=slot.shift, issuer=issuer or user, receiver_name=receiver_name,
        created_by=user, owner=user, reversal_of=reversal_of,
        ref_resource=(ref or {}).get("resource", ""), ref_id=str((ref or {}).get("id", "")), ref_doc=(ref or {}).get("doc", ""),
    )
    return move


def reverse_move(move, user, reason):
    """Posted movements are reversed with a link, never deleted (p.12)."""
    if hasattr(move, "reversed_by"):
        raise BusinessRuleError("Movement already reversed.", "already_reversed")
    rev = post_move(
        "reversal", item=move.item, lot=move.lot, qty=move.qty, from_location=move.to_location, to_location=move.from_location,
        user=user, pr=move.pr, so_line=move.so_line, reason=f"Reversal of {move.doc_no}: {reason}", reversal_of=move,
        ref={"resource": move.ref_resource, "id": move.ref_id, "doc": move.ref_doc},
    )
    audit.record("stock_move_reversed", "inventory.stockmove", move, changes={"reversal": rev.doc_no}, reason=reason)
    return rev


def undo_move(move):
    """Owner deleting a wrongly posted movement: take it back off the balances first."""
    if move.to_location is not None and not move.to_location.is_virtual:
        bal = _lock_balance(move.item, move.lot, move.to_location)
        if bal.qty < move.qty:
            raise BusinessRuleError(
                f"Only {bal.qty} of lot {move.lot.doc_no} is left at {move.to_location.code}; {move.doc_no} put {move.qty} there. "
                "Some of it has already been used - delete or reverse those later movements first.",
                "negative_stock", {"available": str(bal.qty), "requested": str(move.qty)},
            )
        bal.qty -= move.qty
        bal.save(update_fields=["qty", "updated_at"])
    if move.from_location is not None and not move.from_location.is_virtual:
        bal = _lock_balance(move.item, move.lot, move.from_location)
        bal.qty += move.qty
        bal.save(update_fields=["qty", "updated_at"])


def create_lot(*, item, source, user, quality_state="pending", **fields):
    return Lot.objects.create(item=item, source=source, quality_state=quality_state, created_by=user, owner=user, **fields)


# ---------------------------------------------------------------------------
# Balances & availability
# ---------------------------------------------------------------------------
def balance_summary(item, *, artwork=None, flavour=None, product=None):
    """On-hand / held / reserved / available / machine-side / incoming (p.12)."""
    qs = StockBalance.objects.filter(item=item, qty__gt=0)
    if artwork is not None:
        qs = qs.filter(lot__artwork=artwork)
    if flavour is not None:
        qs = qs.filter(lot__flavour=flavour)
    if product is not None:
        qs = qs.filter(lot__product=product)
    rows = qs.values("location__kind", "lot__quality_state").annotate(total=Sum("qty"))
    on_hand = held = usable = machine_side = pending = 0
    for r in rows:
        kind, state, total = r["location__kind"], r["lot__quality_state"], r["total"]
        if kind == "machine_side":
            machine_side += total
            continue
        if kind in STORAGE_EXCLUDED_FROM_USABLE and kind != "qc_hold":
            continue
        on_hand += total
        if state == "released" and kind not in STORAGE_EXCLUDED_FROM_USABLE:
            usable += total
        elif state == "pending":
            pending += total
        else:
            held += total
    res_qs = Reservation.objects.filter(item=item, status="active")
    if artwork is not None or flavour is not None or product is not None:
        lot_filter = {}
        if artwork is not None:
            lot_filter["lot__artwork"] = artwork
        if flavour is not None:
            lot_filter["lot__flavour"] = flavour
        res_qs = res_qs.filter(**lot_filter) if lot_filter else res_qs
    reserved = sum((r.open_qty for r in res_qs), Decimal("0"))
    from apps.purchase.models import POLine
    incoming = sum(
        (l.balance_qty for l in POLine.objects.filter(item=item, status__in=["open", "partial"], po__status__in=["approved", "sent", "partial"])),
        Decimal("0"),
    )
    return {
        "on_hand": D(on_hand), "usable": D(usable), "pending_qc": D(pending), "held": D(held), "reserved": D(reserved),
        "available": D(usable) - D(reserved), "machine_side": D(machine_side), "incoming": D(incoming),
    }


def usable_lot_balances(item, *, lot_filter=None, location_kinds=None):
    qs = StockBalance.objects.filter(item=item, qty__gt=0, lot__quality_state="released").exclude(location__kind__in=STORAGE_EXCLUDED_FROM_USABLE)
    if location_kinds:
        qs = qs.filter(location__kind__in=location_kinds)
    if lot_filter:
        qs = qs.filter(**lot_filter)
    return qs.select_related("lot", "location").order_by("lot__created_at")


def lot_free_qty(lot):
    usable = StockBalance.objects.filter(lot=lot).exclude(location__kind__in=STORAGE_EXCLUDED_FROM_USABLE).aggregate(t=Sum("qty"))["t"] or 0
    if lot.quality_state != "released":
        return Decimal("0")
    reserved = sum((r.open_qty for r in Reservation.objects.filter(lot=lot, status="active")), Decimal("0"))
    return D(usable) - reserved


def reserve(*, item, qty, user, so_line=None, pr=None, lot=None, lot_filter=None, note=""):
    """Reserve free stock. The item row lock serialises competing reservations."""
    qty = _validate_qty(item, qty)
    Item.objects.select_for_update().get(pk=item.pk)
    if lot is not None:
        free = lot_free_qty(lot)
    else:
        usable = sum((b.qty for b in usable_lot_balances(item, lot_filter=lot_filter)), Decimal("0"))
        res_qs = Reservation.objects.filter(item=item, status="active")
        if lot_filter:
            res_qs = res_qs.filter(**{k: v for k, v in lot_filter.items()})
        free = usable - sum((r.open_qty for r in res_qs), Decimal("0"))
    if qty > free:
        raise BusinessRuleError(
            f"Only {free} {item.stock_uom} of {item.code} is free to allocate; {qty} requested.", "insufficient_available",
            {"free": str(free), "requested": str(qty)},
        )
    r = Reservation.objects.create(item=item, lot=lot, qty=qty, so_line=so_line, pr=pr, note=note, created_by=user)
    audit.record("reserved", "inventory.reservation", r, changes={"item": item.code, "qty": str(qty), "lot": lot.doc_no if lot else None})
    return r


def consume_reservation(*, item, qty, so_line=None, pr=None, lot=None):
    """Reduce matching active reservations by a posted quantity (issue / dispatch)."""
    remaining = D(qty)
    qs = Reservation.objects.select_for_update().filter(item=item, status="active")
    if so_line is not None:
        qs = qs.filter(so_line=so_line)
    if pr is not None:
        qs = qs.filter(pr=pr)
    for r in qs.order_by("lot_id" if lot is None else "created_at"):
        if remaining <= 0:
            break
        if lot is not None and r.lot_id not in (None, lot.pk):
            continue
        take = min(r.open_qty, remaining)
        r.consumed_qty += take
        if r.open_qty <= 0:
            r.status = "consumed"
        r.save(update_fields=["consumed_qty", "status", "updated_at"])
        remaining -= take


def release_reservations(*, so_line=None, pr=None, reason=""):
    qs = Reservation.objects.filter(status="active")
    if so_line is not None:
        qs = qs.filter(so_line=so_line)
    if pr is not None:
        qs = qs.filter(pr=pr)
    n = qs.update(status="released", note=reason[:200])
    return n


# ---------------------------------------------------------------------------
# Quality state changes (explicit dispositions with lot split)
# ---------------------------------------------------------------------------
def set_quality(lot, qty, state, user, reason=""):
    """Move `qty` of a lot into `state`. Partial quantities split into a child lot."""
    qty = _validate_qty(lot.item, qty)
    balances = list(StockBalance.objects.select_for_update().filter(lot=lot, qty__gt=0).exclude(location__kind__in={"customer", "production", "supplier"}))
    total = sum((b.qty for b in balances), Decimal("0"))
    if qty > total:
        raise BusinessRuleError(f"Lot {lot.doc_no} has only {total} on hand.", "invalid_qty")
    if qty == total:
        before = lot.quality_state
        lot.quality_state = state
        lot.row_version += 1
        lot.save(update_fields=["quality_state", "row_version", "updated_at"])
        audit.record("quality_state", "inventory.lot", lot, changes={"quality_state": [before, state], "qty": str(qty)}, reason=reason)
        return lot
    child = Lot.objects.create(
        item=lot.item, artwork=lot.artwork, product=lot.product, component_type=lot.component_type, flavour=lot.flavour,
        supplier_lot=lot.supplier_lot, source="split", quality_state=state, pr=lot.pr, grn_line=lot.grn_line, parent=lot,
        produced_at=lot.produced_at, shift_date=lot.shift_date, shift=lot.shift, created_by=user, owner=user,
    )
    LotGenealogy.objects.create(parent=lot, child=child, qty=qty, relation="split")
    remaining = qty
    for b in balances:
        if remaining <= 0:
            break
        take = min(b.qty, remaining)
        post_move("quality", item=lot.item, lot=lot, qty=take, from_location=b.location, to_location=virtual_location("adjustment"), user=user,
                  reason=f"Split to {child.doc_no} ({state})", ref={"resource": "inventory.lot", "id": child.pk, "doc": child.doc_no})
        post_move("quality", item=lot.item, lot=child, qty=take, from_location=virtual_location("adjustment"), to_location=b.location, user=user,
                  reason=f"Split from {lot.doc_no} ({state})", ref={"resource": "inventory.lot", "id": lot.pk, "doc": lot.doc_no})
        remaining -= take
    audit.record("lot_split", "inventory.lot", lot, changes={"child": child.doc_no, "qty": str(qty), "state": state}, reason=reason)
    return child


def lot_on_hand(lot):
    return StockBalance.objects.filter(lot=lot).exclude(location__kind__in={"customer", "production", "supplier", "adjustment"}).aggregate(t=Sum("qty"))["t"] or Decimal("0")


# ---------------------------------------------------------------------------
# Shift PR issue / return (p.10, p.12)
# ---------------------------------------------------------------------------
def issue_to_pr(*, pr, item, lot, qty, from_location, user, receiver_name="", additional_reason="", approval=None):
    from apps.core.shifts import is_shift_closed
    from apps.planning.models import PRMaterial

    if pr.status not in {"released", "in_progress"}:
        raise BusinessRuleError("Material can be issued only against a released shift PR.", "pr_not_released")
    if is_shift_closed(pr.shift_date, pr.shift):
        raise BusinessRuleError("The PR's shift is closed. Issue against the current shift's PR.", "shift_closed")
    line = PRMaterial.objects.select_for_update().filter(pr=pr, item=item).first()
    if line is None:
        raise BusinessRuleError(
            f"{item.code} is not on this PR's material requirement. Substitutions are controlled changes.", "substitution_blocked",
        )
    if lot.quality_state != "released":
        raise BusinessRuleError(f"Lot {lot.doc_no} is {lot.get_quality_state_display()}; only released lots can be issued.", "lot_not_released")
    if item.kind == "label":
        if lot.artwork_id != pr.artwork_id:
            raise BusinessRuleError(
                f"Wrong label: lot {lot.doc_no} is artwork {lot.artwork or '-'}, PR requires {pr.artwork}.", "wrong_artwork",
            )
        if lot.artwork and lot.artwork.status != "approved":
            raise BusinessRuleError("Label artwork revision is not approved/eligible.", "artwork_not_eligible")
    qty = D(qty)
    pending = line.required_qty - line.net_issued
    if qty > pending and not approval:
        raise BusinessRuleError(
            f"Issue exceeds released shift requirement by {qty - pending} {item.stock_uom}. Request an additional-issue approval.",
            "additional_issue_requires_approval", {"pending": str(pending), "requested": str(qty)},
        )
    to_loc = machine_side_location(pr.machine)
    move = post_move("issue", item=item, lot=lot, qty=qty, from_location=from_location, to_location=to_loc, user=user, pr=pr,
                     so_line=pr.so_line, ref={"resource": "planning.productionrequest", "id": pr.pk, "doc": pr.doc_no},
                     receiver_name=receiver_name, reason=additional_reason)
    consume_reservation(item=item, qty=qty, pr=pr)
    line.issued_qty += qty
    line.row_version += 1
    line.save(update_fields=["issued_qty", "row_version", "updated_at"])
    if pr.status == "released":
        pr.status = "in_progress"
        pr.save(update_fields=["status", "updated_at"])
    return move


def return_from_pr(*, pr, item, lot, qty, to_location, user, reason=""):
    """Unused material returns against the same (possibly cancelled) PR (p.10)."""
    from apps.planning.models import PRMaterial

    line = PRMaterial.objects.select_for_update().filter(pr=pr, item=item).first()
    if line is None:
        raise BusinessRuleError("This material was not issued against the PR.", "not_issued")
    qty = D(qty)
    if qty > line.machine_side_balance:
        raise BusinessRuleError(f"Return exceeds machine-side balance ({line.machine_side_balance}).", "return_exceeds")
    from_loc = machine_side_location(pr.machine)
    move = post_move("return", item=item, lot=lot, qty=qty, from_location=from_loc, to_location=to_location, user=user, pr=pr,
                     ref={"resource": "planning.productionrequest", "id": pr.pk, "doc": pr.doc_no}, reason=reason)
    line.returned_qty += qty
    line.row_version += 1
    line.save(update_fields=["returned_qty", "row_version", "updated_at"])
    return move


def consume_for_pr(*, pr, item, qty, user, reason="Production consumption"):
    """Consumption is a separate production event; it draws from machine-side lots FIFO."""
    from apps.planning.models import PRMaterial

    line = PRMaterial.objects.select_for_update().filter(pr=pr, item=item).first()
    if line is None:
        raise BusinessRuleError("Material not on PR.", "not_issued")
    qty = D(qty)
    if qty > line.machine_side_balance:
        raise BusinessRuleError(f"Consumption {qty} exceeds machine-side balance {line.machine_side_balance} for {item.code}.", "consumption_exceeds")
    loc = machine_side_location(pr.machine)
    remaining = qty
    moves = []
    for bal in StockBalance.objects.select_for_update().filter(item=item, location=loc, qty__gt=0).select_related("lot").order_by("lot__created_at"):
        if remaining <= 0:
            break
        take = min(bal.qty, remaining)
        moves.append(post_move("consumption", item=item, lot=bal.lot, qty=take, from_location=loc, to_location=virtual_location("production"),
                               user=user, pr=pr, reason=reason, ref={"resource": "planning.productionrequest", "id": pr.pk, "doc": pr.doc_no}))
        remaining -= take
    line.consumed_qty += qty
    line.save(update_fields=["consumed_qty", "updated_at"])
    return moves


def link_genealogy(parent_lots, child, relation="consumed"):
    for lot, qty in parent_lots:
        LotGenealogy.objects.create(parent=lot, child=child, qty=qty, relation=relation)


def receipt_into_stock(*, item, qty, location, user, source, quality_state, ref, **lot_fields):
    lot = create_lot(item=item, source=source, user=user, quality_state=quality_state, **lot_fields)
    post_move("receipt" if source == "grn" else ("opening" if source == "opening" else "production"),
              item=item, lot=lot, qty=qty, from_location=virtual_location("supplier" if source == "grn" else "adjustment"),
              to_location=location, user=user, ref=ref)
    return lot


@transaction.atomic
def post_opening_balance(*, item, qty, location, user, quality_state="released", **lot_fields):
    lot = receipt_into_stock(item=item, qty=qty, location=location, user=user, source="opening", quality_state=quality_state,
                             ref={"resource": "inventory.opening", "id": "", "doc": "OPENING"}, **lot_fields)
    audit.record("opening_balance", "inventory.lot", lot, changes={"qty": str(qty), "location": location.code})
    return lot


def stock_overview(*, include_value=False):
    """Item-wise stock position for the inventory screen: the same buckets as balance_summary,
    computed for every active item in a handful of grouped queries."""
    from apps.purchase.models import POLine

    buckets = {}
    rows = (StockBalance.objects.filter(qty__gt=0)
            .values("item_id", "location__kind", "lot__quality_state").annotate(total=Sum("qty")))
    for r in rows:
        b = buckets.setdefault(r["item_id"], {"on_hand": Decimal("0"), "usable": Decimal("0"), "pending_qc": Decimal("0"),
                                              "held": Decimal("0"), "machine_side": Decimal("0")})
        kind, state, total = r["location__kind"], r["lot__quality_state"], r["total"]
        if kind == "machine_side":
            b["machine_side"] += total
            continue
        if kind in STORAGE_EXCLUDED_FROM_USABLE and kind != "qc_hold":
            continue
        b["on_hand"] += total
        if state == "released" and kind not in STORAGE_EXCLUDED_FROM_USABLE:
            b["usable"] += total
        elif state == "pending":
            b["pending_qc"] += total
        else:
            b["held"] += total
    reserved = {}
    for r in Reservation.objects.filter(status="active").values("item_id").annotate(q=Sum("qty"), c=Sum("consumed_qty")):
        reserved[r["item_id"]] = (r["q"] or 0) - (r["c"] or 0)
    incoming = {}
    for line in POLine.objects.filter(status__in=["open", "partial"], po__status__in=["approved", "sent", "partial"]).only(
            "item_id", "qty", "accepted_qty"):
        incoming[line.item_id] = incoming.get(line.item_id, Decimal("0")) + D(line.balance_qty)
    locations = {r["item_id"]: r["n"] for r in StockBalance.objects.filter(qty__gt=0).exclude(location__kind__in=STORAGE_EXCLUDED_FROM_USABLE - {"qc_hold"})
                 .values("item_id").annotate(n=Count("location", distinct=True))}

    zero = {"on_hand": Decimal("0"), "usable": Decimal("0"), "pending_qc": Decimal("0"), "held": Decimal("0"), "machine_side": Decimal("0")}
    kinds = dict(Item.KINDS)
    out, totals = [], {"items": 0, "in_stock": 0, "low": 0, "out": 0, "value": Decimal("0")}
    for item in Item.objects.filter(active=True).order_by("kind", "code"):
        b = buckets.get(item.id, zero)
        res = D(reserved.get(item.id, 0))
        available = b["usable"] - res
        floor = max(item.min_stock, item.safety_stock)
        if floor > 0 and available <= 0:
            status = "out"
        elif floor > 0 and available < floor:
            status = "low"
        elif b["on_hand"] > 0 or b["machine_side"] > 0:
            status = "ok"
        else:
            status = "none"
        row = {
            "id": str(item.pk), "code": item.code, "name": item.name, "kind": item.kind, "kind_label": kinds.get(item.kind, item.kind),
            "group": item.group, "uom": item.stock_uom, "min_stock": str(D(item.min_stock)), "safety_stock": str(D(item.safety_stock)),
            "on_hand": str(D(b["on_hand"])), "usable": str(D(b["usable"])), "pending_qc": str(D(b["pending_qc"])), "held": str(D(b["held"])),
            "reserved": str(res), "available": str(D(available)), "machine_side": str(D(b["machine_side"])),
            "incoming": str(D(incoming.get(item.id, 0))), "locations": locations.get(item.id, 0), "status": status,
        }
        if include_value:
            value = D(b["on_hand"] * item.standard_cost)
            row["value"] = str(value.quantize(Decimal("0.01")))
            totals["value"] += value
        totals["items"] += 1
        totals["in_stock"] += 1 if b["on_hand"] > 0 else 0
        totals["low"] += 1 if status == "low" else 0
        totals["out"] += 1 if status == "out" else 0
        out.append(row)
    totals["value"] = str(totals["value"].quantize(Decimal("0.01"))) if include_value else None
    return {"generated_at": timezone.now(), "totals": totals, "rows": out}
