from decimal import Decimal

from django.utils import timezone

from apps.core import audit
from apps.core.api import BusinessRuleError
from apps.core.registry import Action, Resource, register
from apps.core.services import notify_perm
from apps.inventory import services as inv
from apps.inventory.models import Lot

from .models import Complaint, Inspection


@register
class InspectionResource(Resource):
    key = "quality.inspection"
    model = Inspection
    label = "Inspections"
    singular = "Inspection"
    module = "quality"
    list_fields = ["doc_no", "kind", "lot", "pr", "inspector", "status", "qty_release", "qty_hold", "qty_reject", "decided_at"]
    create_only_fields = ["kind", "lot", "pr", "grn_line"]
    form_fields = ["spec_revision", "sample_size", "results", "checklist", "remarks"]
    readonly_fields = ["decided_by", "qty_rework"]
    filter_fields = ["kind", "status", "lot", "pr"]
    search_fields = ["doc_no", "lot__doc_no"]
    depends = {"lot": {"quality_state": "=pending,held,rework"}}
    field_help = {
        "results": "Container and lid are recorded as separate rows with their own dimensions/results; COA is generated only from these.",
        "spec_revision": "Use the customer/product-specific approved tolerance revision. 0-5 mm label-join is not a universal spec.",
    }
    help = {
        "why": "Accepted stock needs a specification and a test record.",
        "effect": "Hold / release / reject / rework are explicit stock dispositions; only released quantities can be packed or shipped.",
    }

    def before_create(self, request, data):
        data["inspector"] = request.user
        if not data.get("lot") and not data.get("pr"):
            raise BusinessRuleError("Inspect a lot or a PR (first-piece).", "validation")

    def edit_block_reason(self, request, obj):
        return None if obj.status == "draft" else "Disposition posted; inspection is frozen."

    def _decide(request, obj, data):
        rel, hold, rej, rew = (Decimal(str(data.get(k) or 0)) for k in ("release", "hold", "reject", "rework"))
        if obj.lot_id:
            lot = Lot.objects.select_for_update().get(pk=obj.lot_id)
            on_hand = inv.lot_on_hand(lot)
            if rel + hold + rej + rew != on_hand:
                raise BusinessRuleError(f"Dispositions must account for the lot's {on_hand} on hand.", "not_reconciled")
            if obj.results and any(r.get("pass") is False and r.get("critical") for r in obj.results) and rel > 0:
                raise BusinessRuleError("A failed critical check blocks release.", "critical_failure")
            for qty, state in ((hold, "held"), (rej, "rejected"), (rew, "rework"), (rel, "released")):
                if qty:
                    inv.set_quality(lot, qty, state, request.user, f"Inspection {obj.doc_no}")
        obj.qty_release, obj.qty_hold, obj.qty_reject, obj.qty_rework = rel, hold, rej, rew
        obj.status = "decided"
        obj.decided_by = request.user
        obj.decided_at = timezone.now()
        obj.row_version += 1
        obj.save()
        audit.record("inspection_decided", "quality.inspection", obj, changes={"release": str(rel), "hold": str(hold), "reject": str(rej), "rework": str(rew)})
        if rel:
            notify_perm("fg.packingrecord.create", f"QC released {rel} pcs ({obj.lot})", resource="inventory.lot", object_id=obj.lot_id,
                        next_action="Eligible for packing / allocation", dedupe_key=f"qc-release:{obj.pk}")
        return obj

    actions = [Action("decide", "Post disposition", _decide, permission="quality.release", tone="primary",
                      fields=[{"name": "release", "label": "Release qty", "type": "decimal"}, {"name": "hold", "label": "Hold qty", "type": "decimal"},
                              {"name": "reject", "label": "Reject qty", "type": "decimal"}, {"name": "rework", "label": "Rework qty", "type": "decimal"}],
                      available=lambda o, r: o.status == "draft")]


@register
class ComplaintResource(Resource):
    key = "quality.complaint"
    model = Complaint
    label = "Complaints & CAPA"
    singular = "Complaint"
    module = "quality"
    list_fields = ["doc_no", "customer", "complaint_date", "kind", "severity", "qty_affected", "status", "responsible", "due_date"]
    create_only_fields = ["customer", "dispatch", "product", "complaint_date", "kind"]
    form_fields = ["severity", "qty_affected", "description", "containment", "root_cause", "corrective_action", "preventive_action",
                   "effectiveness_review", "responsible", "due_date", "replacement_required", "response_reference"]
    readonly_fields = ["closed_by"]
    filter_fields = ["status", "customer", "kind", "severity", "responsible"]
    search_fields = ["doc_no", "description"]
    FLOW = ["reported", "contained", "investigation", "action", "verification", "closed"]
    REQUIRED = {"contained": "containment", "investigation": "root_cause", "action": "corrective_action", "verification": "preventive_action", "closed": "effectiveness_review"}
    help = {"steps": "Reported -> Contained -> Investigation/RCA -> Corrective action -> Effectiveness verification -> Closed.",
            "boundary": "A recurring defect needs verified action, not just a status change."}

    def edit_block_reason(self, request, obj):
        return "Closed complaints are frozen." if obj.status == "closed" else None

    def extra_detail(self, request, obj):
        from apps.inventory.trace import backward
        lots = list(obj.lots.all())
        if obj.dispatch_id and not lots:
            lots = [dl.lot for dl in obj.dispatch.lines.select_related("lot")]
        return {"traceability": [backward(l) for l in lots]}

    def _advance(request, obj, data):
        flow = ComplaintResource.FLOW
        nxt = flow[flow.index(obj.status) + 1]
        need = ComplaintResource.REQUIRED.get(nxt)
        if need and not getattr(obj, need):
            raise BusinessRuleError(f"Record {need.replace('_', ' ')} before moving to {nxt}.", "missing_evidence")
        if nxt == "closed":
            if obj.responsible_id == request.user.pk:
                raise BusinessRuleError("Closure approval must come from someone other than the responsible owner.", "self_approval")
            obj.closed_by = request.user
        if nxt == "contained" and data.get("hold_lots"):
            for lot in list(obj.lots.all()):
                on_hand = inv.lot_on_hand(lot)
                if on_hand and lot.quality_state == "released":
                    inv.set_quality(lot, on_hand, "held", request.user, f"Complaint {obj.doc_no} containment")
        before = obj.status
        obj.status = nxt
        obj.row_version += 1
        obj.save()
        audit.record("complaint_advanced", "quality.complaint", obj, changes={"status": [before, nxt]}, reason=data.get("reason", ""))
        return obj

    def _link_dispatch_lots(request, obj, data):
        if not obj.dispatch_id:
            raise BusinessRuleError("Link a dispatch first.", "validation")
        lots = [dl.lot for dl in obj.dispatch.lines.select_related("lot")]
        parents = []
        from apps.inventory.models import LotGenealogy
        for lot in lots:
            parents += [g.parent for g in LotGenealogy.objects.filter(child=lot)]
        obj.lots.add(*lots, *parents)
        audit.record("complaint_lots_linked", "quality.complaint", obj, changes={"lots": [l.doc_no for l in lots + parents]})
        return obj

    actions = [
        Action("advance", "Advance CAPA stage", _advance, permission="quality.complaint.update", tone="primary",
               fields=[{"name": "hold_lots", "label": "Hold linked lots still in stock (containment)", "type": "boolean"}],
               available=lambda o, r: o.status != "closed"),
        Action("link_lots", "Link supplied batches from dispatch", _link_dispatch_lots, permission="quality.complaint.update",
               available=lambda o, r: bool(o.dispatch_id)),
    ]
