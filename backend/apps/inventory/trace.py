"""
Lot traceability (brief p.14, p.42).

Backward: lot -> production report/PR -> machine, mould, shift, operators,
consumed polymer/masterbatch/label lots; packed lots -> component lots.
Forward: lot -> lots made from it -> packed sets -> dispatches -> customers.
Search obeys the same access barriers: customer names only for users who may
see customers.
"""
from apps.core.rbac import has_perm

from .models import Lot, LotGenealogy, StockMove


def _lot_brief(lot):
    return {"lot": lot.doc_no, "id": str(lot.pk), "item": lot.item.code, "kind": lot.item.kind, "state": lot.quality_state,
            "artwork": str(lot.artwork) if lot.artwork_id else None, "supplier_lot": lot.supplier_lot}


def backward(lot, depth=0, seen=None):
    seen = seen if seen is not None else set()
    if lot.pk in seen or depth > 6:
        return {}
    seen.add(lot.pk)
    node = _lot_brief(lot)
    if lot.pr_id:
        pr = lot.pr
        from apps.production.models import ProductionReport
        reports = ProductionReport.objects.filter(pr=pr, status__in=["submitted", "corrected"])
        node["production"] = {
            "pr": pr.doc_no, "machine": pr.machine.code, "mould": pr.mould.code, "shift_date": str(pr.shift_date), "shift": pr.shift,
            "operators": sorted({r.operator_name or (r.operator.full_name if r.operator_id else "") for r in reports} - {""}),
            "reports": [r.doc_no for r in reports],
        }
        consumed = (StockMove.objects.filter(pr=pr, move_type__in=["consumption", "issue"]).select_related("lot", "item")
                    .values("lot", "move_type").distinct())
        mats = {}
        for row in consumed:
            src = Lot.objects.select_related("item", "artwork").get(pk=row["lot"])
            mats[src.pk] = _lot_brief(src) | {"via": row["move_type"]}
        node["materials"] = list(mats.values())
    parents = LotGenealogy.objects.filter(child=lot).select_related("parent__item", "parent__artwork", "parent__pr")
    if parents:
        node["made_from"] = [backward(g.parent, depth + 1, seen) | {"qty": str(g.qty), "relation": g.relation} for g in parents]
    if lot.parent_id:
        node["split_from"] = lot.parent.doc_no
    return node


def forward(lot, request, depth=0, seen=None):
    seen = seen if seen is not None else set()
    if lot.pk in seen or depth > 6:
        return {}
    seen.add(lot.pk)
    node = _lot_brief(lot)
    show_customer = has_perm(request.user, "masters.customer.view")
    children = LotGenealogy.objects.filter(parent=lot).select_related("child__item")
    if children:
        node["used_in"] = [forward(g.child, request, depth + 1, seen) | {"qty": str(g.qty)} for g in children]
    # Material lot issued/consumed into PRs -> their output lots
    prs = StockMove.objects.filter(lot=lot, move_type__in=["issue", "consumption"]).exclude(pr__isnull=True).values_list("pr", flat=True).distinct()
    outputs = Lot.objects.filter(pr__in=list(prs)).exclude(pk=lot.pk)
    if outputs:
        node["production_output"] = [forward(o, request, depth + 1, seen) for o in outputs]
    from apps.fg.models import DispatchLine
    dls = DispatchLine.objects.filter(lot=lot, dispatch__status__in=["posted", "delivered"]).select_related("dispatch__customer")
    if dls:
        node["dispatches"] = [{"dispatch": d.dispatch.doc_no, "qty": d.qty, "date": d.dispatch.posted_at,
                               "customer": d.dispatch.customer.name if show_customer else "(restricted)"} for d in dls]
    return node


def trace_lot(request, lot):
    return {"backward": backward(lot), "forward": forward(lot, request)}


def affected_by_material_lot(request, lot):
    """From a suspect material lot, every affected production lot and customer dispatch."""
    tree = forward(lot, request)
    lots, dispatches = [], []

    def walk(n):
        if not n:
            return
        lots.append(n.get("lot"))
        dispatches.extend(n.get("dispatches", []))
        for key in ("used_in", "production_output"):
            for child in n.get(key, []):
                walk(child)

    walk(tree)
    return {"lots": [l for l in lots if l], "dispatches": dispatches}
