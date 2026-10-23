from datetime import date

from django.db.models import Max
from django.utils import timezone

from apps.core import audit
from apps.core.rbac import is_owner
from apps.core.api import BusinessRuleError
from apps.core.registry import Action, Resource, register
from apps.core.services import draft_outbox, notify_perm, suppress_stale_outbox
from apps.inventory.services import balance_summary

from . import services
from .models import GRN, GRNLine, POLine, PurchaseOrder, Requisition

RATE = "purchase.rate.view"


@register
class RequisitionResource(Resource):
    key = "purchase.requisition"
    model = Requisition
    label = "Material requisitions"
    singular = "Requisition"
    module = "purchase"
    list_fields = ["doc_no", "item", "qty", "needed_date", "urgency", "status", "created_by", "approved_by"]
    form_fields = ["item", "qty", "needed_date", "source_demand", "urgency", "notes"]
    readonly_fields = ["stock_basis"]
    filter_fields = ["status", "urgency", "item"]
    search_fields = ["doc_no", "item__code", "source_demand"]

    def before_create(self, request, data):
        data["stock_basis"] = {k: str(v) for k, v in balance_summary(data["item"]).items()}

    def edit_block_reason(self, request, obj):
        return None if obj.status in {"draft", "submitted"} else f"Requisition is {obj.status}."

    def _approve(request, obj, data):
        if obj.created_by_id == request.user.pk and not is_owner(request.user):  # owner = super admin: no maker/checker
            raise BusinessRuleError("Maker/checker: requester cannot approve.", "self_approval")
        obj.status = "approved"
        obj.approved_by = request.user
        obj.row_version += 1
        obj.save()
        audit.record("requisition_approved", "purchase.requisition", obj)
        return obj

    def _reject(request, obj, data):
        obj.status = "rejected"
        obj.notes = (obj.notes + "\nRejected: " + data["reason"]).strip()
        obj.save()
        audit.record("requisition_rejected", "purchase.requisition", obj, reason=data["reason"])
        return obj

    actions = [
        Action("approve", "Approve demand", _approve, tone="primary", available=lambda o, r: o.status in {"draft", "submitted"}),
        Action("reject", "Reject", _reject, permission="purchase.requisition.approve", reason_required=True, tone="danger",
               available=lambda o, r: o.status in {"draft", "submitted"}),
    ]


@register
class PurchaseOrderResource(Resource):
    key = "purchase.purchaseorder"
    server_defaults = ["order_date"]
    model = PurchaseOrder
    label = "Purchase orders"
    singular = "Purchase order"
    module = "purchase"
    list_fields = ["doc_no", "supplier", "order_date", "status", "supplier_confirmed", "approved_by", "total_value"]
    create_only_fields = ["supplier"]
    form_fields = ["order_date", "terms", "freight_charges", "notes"]
    readonly_fields = ["approved_at"]
    filter_fields = ["status", "supplier"]
    search_fields = ["doc_no", "supplier__name"]
    field_permissions = {"total_value": RATE, "freight_charges": RATE}
    inlines = [{"resource": "purchase.poline", "fk": "po", "label": "PO lines / delivery lots"}]
    help = {"boundary": "Approval is separate from follow-up. A reminder never creates a binding PO."}

    def before_create(self, request, data):
        data.setdefault("order_date", timezone.localdate())

    def approval_amount(self, request, obj, data):
        return services.po_value(obj) if obj is not None else None

    def edit_block_reason(self, request, obj):
        return None if obj.status == "draft" else f"PO is {obj.get_status_display()}."

    def _submit(request, obj, data):
        if not obj.lines.exists():
            raise BusinessRuleError("Add lines first.", "no_lines")
        for line in obj.lines.select_related("item", "artwork"):
            if line.item.kind == "label":
                if not line.artwork_id or line.artwork.status not in {"print_release", "approved"}:
                    raise BusinessRuleError(f"Label line {line.line_no} needs an approved / print-released artwork revision.", "artwork_not_released")
        obj.status = "pending_approval"
        obj.row_version += 1
        obj.save()
        audit.record("po_submitted", "purchase.purchaseorder", obj)
        return obj

    def _approve(request, obj, data):
        if obj.created_by_id == request.user.pk and not is_owner(request.user):  # owner = super admin: no maker/checker
            raise BusinessRuleError("Maker/checker: the PO creator cannot approve it.", "self_approval")
        obj.status = "approved"
        obj.approved_by = request.user
        obj.approved_at = timezone.now()
        obj.row_version += 1
        obj.save()
        audit.record("po_approved", "purchase.purchaseorder", obj, changes={"value": str(services.po_value(obj))})
        return obj

    def _send(request, obj, data):
        contact = obj.supplier.contacts.filter(active=True, whatsapp_opt_in=True).exclude(phone="").first()
        if contact is None:
            raise BusinessRuleError("No supplier contact with a WhatsApp number and recorded consent. Add one under the supplier's contacts.",
                                    "no_verified_contact")
        draft_outbox(channel="whatsapp", recipient=contact.phone, template="purchase_order", source_resource="purchase.purchaseorder",
                     source_id=obj.pk, source_version=obj.row_version, dedupe_key=f"po-send:{obj.pk}:{obj.row_version}",
                     payload={"po": obj.doc_no, "supplier": obj.supplier.name, "lines": [
                         {"item": l.item.code, "qty": str(l.qty), "promised": str(l.promised_date)} for l in obj.lines.all()]})
        obj.status = "sent"
        obj.row_version += 1
        obj.save()
        audit.record("po_sent", "purchase.purchaseorder", obj, changes={"recipient": contact.phone})
        return obj

    def _confirm(request, obj, data):
        obj.supplier_confirmed = True
        obj.save()
        audit.record("po_supplier_confirmed", "purchase.purchaseorder", obj, reason=data.get("note", ""))
        return obj

    def _cancel(request, obj, data):
        if obj.lines.filter(received_qty__gt=0).exists():
            raise BusinessRuleError("Receipts exist; short-close the open lines instead.", "has_receipts")
        obj.status = "cancelled"
        obj.save()
        POLine.objects.filter(po=obj).update(status="cancelled")
        audit.record("po_cancelled", "purchase.purchaseorder", obj, reason=data["reason"])
        return obj

    actions = [
        Action("submit", "Submit for approval", _submit, permission="purchase.purchaseorder.update", tone="primary", available=lambda o, r: o.status == "draft"),
        Action("approve", "Approve PO", _approve, permission="purchase.po.approve", tone="primary", available=lambda o, r: o.status == "pending_approval"),
        Action("send", "Send to supplier (WhatsApp draft)", _send, permission="purchase.po.approve", available=lambda o, r: o.status == "approved"),
        Action("supplier_confirm", "Record supplier confirmation", _confirm, permission="purchase.purchaseorder.update",
               fields=[{"name": "note", "label": "Confirmation details", "type": "text"}], available=lambda o, r: o.status in {"approved", "sent", "partial"}),
        Action("cancel", "Cancel PO", _cancel, permission="purchase.po.approve", reason_required=True, tone="danger",
               available=lambda o, r: o.status in {"draft", "pending_approval", "approved", "sent"}),
    ]


@register
class POLineResource(Resource):
    key = "purchase.poline"
    model = POLine
    label = "PO lines & follow-up"
    singular = "PO line"
    module = "purchase"
    title_field = "line_no"
    ordering = ["promised_date"]
    list_fields = ["po", "line_no", "item", "artwork", "qty", "rate", "original_promised_date", "promised_date", "slip_days", "received_qty",
                   "accepted_qty", "balance_qty", "status", "follow_up_date", "follow_up_owner"]
    create_only_fields = ["po", "item", "requisition"]
    form_fields = ["artwork", "qty", "rate", "promised_date"]
    readonly_fields = ["overdue_reason", "rejected_qty", "promise_revisions"]
    filter_fields = ["po", "item", "status", "follow_up_owner"]
    search_fields = ["po__doc_no", "item__code"]
    field_permissions = {"rate": RATE}
    depends = {"artwork": {"label_item": "item"}}

    def before_create(self, request, data):
        po = data["po"]
        if po.status != "draft":
            raise BusinessRuleError("Lines can be added only to draft POs.", "record_locked")
        data["line_no"] = (po.lines.aggregate(m=Max("line_no"))["m"] or 0) + 1
        if data["item"].kind == "label" and not data.get("artwork"):
            raise BusinessRuleError("Label purchases must specify the artwork revision.", "artwork_required")

    def edit_block_reason(self, request, obj):
        return None if obj.po.status == "draft" else "PO is no longer a draft. Use follow-up for promise changes."

    def outbox_source(self, obj):
        return "purchase.purchaseorder", obj.po_id

    def _followup(request, obj, data):
        before = {"promised_date": str(obj.promised_date), "follow_up_date": str(obj.follow_up_date)}
        new_promise = data.get("promised_date")
        if isinstance(new_promise, str) and new_promise:
            new_promise = date.fromisoformat(new_promise)
        if new_promise and new_promise != obj.promised_date:
            if len((data.get("overdue_reason") or "").strip()) < 5:
                raise BusinessRuleError("Record why the supplier promise changed.", "reason_required")
            obj.promise_revisions = [*obj.promise_revisions, {"from": str(obj.promised_date), "to": str(new_promise),
                                                              "reason": data["overdue_reason"].strip(), "by": request.user.username,
                                                              "at": timezone.now().isoformat()}]
            obj.promised_date = new_promise
            suppress_stale_outbox("purchase.purchaseorder", obj.po_id, f"PO line {obj.line_no} promise revised; message must be re-drafted")
        if data.get("follow_up_date"):
            obj.follow_up_date = data["follow_up_date"]
        obj.overdue_reason = data.get("overdue_reason", obj.overdue_reason)
        obj.follow_up_owner = request.user
        obj.save()
        audit.record("po_followup", "purchase.poline", obj, changes={"before": before, "after": {"promised_date": str(obj.promised_date), "follow_up_date": str(obj.follow_up_date)}},
                     reason=data.get("overdue_reason", ""))
        return obj

    def _short_close(request, obj, data):
        obj.status = "closed"
        obj.save()
        audit.record("po_line_short_closed", "purchase.poline", obj, reason=data["reason"])
        return obj

    actions = [
        Action("followup", "Record follow-up", _followup,
               fields=[{"name": "promised_date", "label": "New supplier promise (original is kept)", "type": "date"},
                       {"name": "follow_up_date", "label": "Next follow-up", "type": "date"},
                       {"name": "overdue_reason", "label": "Reason (required if the promise changes)", "type": "text"}],
               available=lambda o, r: o.status in {"open", "partial"}),
        Action("short_close", "Short-close balance", _short_close, permission="purchase.po.approve", reason_required=True, tone="danger",
               available=lambda o, r: o.status in {"open", "partial"}),
    ]


@register
class GRNResource(Resource):
    key = "purchase.grn"
    server_defaults = ["invoice_no"]
    model = GRN
    label = "Goods receipts (GRN)"
    singular = "GRN"
    module = "purchase"
    list_fields = ["doc_no", "supplier", "invoice_no", "invoice_date", "duplicate_of", "gate_entry", "location", "status", "posted_at"]
    create_only_fields = ["supplier", "gate_entry"]
    form_fields = ["invoice_no", "invoice_date", "duplicate_note", "location", "notes"]
    readonly_fields = ["duplicate_of"]
    filter_fields = ["status", "supplier", "gate_entry"]
    search_fields = ["doc_no", "invoice_no", "supplier__name"]
    inlines = [{"resource": "purchase.grnline", "fk": "grn", "label": "Receipt lines"}]
    depends = {"location": {"kind": "=warehouse,label_store"}}
    help = {
        "why": "Gate arrival is not a goods receipt. Stores count/weigh; QC accepts; only accepted quantity becomes usable stock.",
        "boundary": "Declared, received, held and accepted quantities are kept separately. Re-attaching an invoice photo never re-posts stock.",
    }

    def before_create(self, request, data):
        g = data.get("gate_entry")
        if g is not None:
            if g.direction != "in" or g.status not in {"handed_over", "unloading", "receiver_notified", "arrived", "discrepancy"}:
                raise BusinessRuleError("Gate entry is not an open inward arrival.", "invalid_gate_entry")
            data.setdefault("invoice_no", g.document_no)
            data.setdefault("invoice_date", g.document_date)
        self._check_duplicate_invoice(request, data["supplier"], data.get("invoice_no"), data, None)

    def before_update(self, request, obj, data):
        super().before_update(request, obj, data)
        if "invoice_no" in data or "duplicate_note" in data:
            self._check_duplicate_invoice(request, obj.supplier, data.get("invoice_no", obj.invoice_no), data, obj)

    @staticmethod
    def _check_duplicate_invoice(request, supplier, invoice_no, data, obj):
        """Book UI-21: a supplier invoice already used on another receipt is flagged, never silently accepted."""
        key = GRN.normalise_invoice(invoice_no)
        data["duplicate_of"] = None
        if not key:
            return
        others = GRN.objects.filter(supplier=supplier).exclude(status="cancelled")
        if obj is not None:
            others = others.exclude(pk=obj.pk)
        clash = next((g for g in others.only("id", "doc_no", "invoice_no", "status") if GRN.normalise_invoice(g.invoice_no) == key), None)
        if clash is None:
            return
        note = (data.get("duplicate_note") if "duplicate_note" in data else getattr(obj, "duplicate_note", "")) or ""
        if len(note.strip()) < 10:
            raise BusinessRuleError(
                f"Supplier invoice {invoice_no} is already on {clash.doc_no}. If one invoice really covers several receipts, "
                "explain why in 'Reason invoice is reused'.", "duplicate_supplier_invoice",
                {"duplicate_note": [f"Invoice already used on {clash.doc_no} ({clash.get_status_display()})."]})
        data["duplicate_of"] = clash
        notify_perm("finance.dashboard.view", f"Supplier invoice reused: {invoice_no}",
                    body=f"{supplier} invoice {invoice_no} is also on {clash.doc_no}. Reason: {note.strip()}", severity="warning",
                    resource="purchase.grn", object_id=clash.pk, next_action="Check for duplicate billing",
                    dedupe_key=f"dup-invoice:{supplier.pk}:{key}:{obj.pk if obj else 'new'}")

    def edit_block_reason(self, request, obj):
        return None if obj.status == "draft" else "Posted GRNs are immutable; use supplier return / adjustment."

    def _post(request, obj, data):
        return services.post_grn(obj, request.user)

    actions = [Action("post", "Post receipt", _post, permission="purchase.grn.create", tone="primary", available=lambda o, r: o.status == "draft")]


@register
class GRNLineResource(Resource):
    key = "purchase.grnline"
    in_nav = False
    model = GRNLine
    label = "GRN lines"
    singular = "GRN line"
    module = "purchase"
    title_field = "item"
    ordering = ["grn", "created_at"]
    list_fields = ["grn", "item", "artwork", "supplier_lot", "declared_qty", "received_qty", "accepted_qty", "held_qty", "rejected_qty", "qc_status", "lot", "discrepancy"]
    create_only_fields = ["grn", "po_line", "gate_line", "item"]
    form_fields = ["artwork", "supplier_lot", "declared_qty", "received_qty"]
    filter_fields = ["grn", "qc_status", "item", "po_line"]
    depends = {"po_line": {"status": "=open,partial"}}

    def before_create(self, request, data):
        if data["grn"].status != "draft":
            raise BusinessRuleError("GRN already posted.", "record_locked")
        if data.get("po_line") and data["po_line"].item_id != data["item"].pk:
            raise BusinessRuleError("Item differs from the PO line.", "po_mismatch")
        if data.get("po_line") and not data.get("artwork") and data["po_line"].artwork_id:
            data["artwork"] = data["po_line"].artwork

    def edit_block_reason(self, request, obj):
        return None if obj.grn.status == "draft" else "GRN posted."

    def _decide(request, obj, data):
        if obj.grn.status != "posted":
            raise BusinessRuleError("Post the GRN first.", "invalid_state")
        return services.decide_grn_line(obj, data.get("accepted"), data.get("held"), data.get("rejected"), request.user, data.get("reason", ""))

    actions = [Action("qc_decide", "QC accept / hold / reject", _decide, permission="quality.release", tone="primary",
                      fields=[{"name": "accepted", "label": "Accepted (usable)", "type": "decimal", "required": True},
                              {"name": "held", "label": "Held", "type": "decimal"},
                              {"name": "rejected", "label": "Rejected", "type": "decimal"},
                              {"name": "reason", "label": "Remarks / defect", "type": "text"}],
                      available=lambda o, r: o.qc_status == "pending" and o.grn.status == "posted")]
