"""Rules added from the 16-Oct-2026 design and workflow books (UI-05/07/08/09/11/20/21)."""
from datetime import timedelta

from django.utils import timezone

from apps.core.models import OutboxMessage
from apps.core.services import draft_outbox, record_delivery_status
from apps.crm.models import Lead
from apps.purchase.models import GRN, POLine, PurchaseOrder
from apps.tasks.models import Task

from .helpers import PlantTestCase


class FollowUpDueDateTests(PlantTestCase):
    def _task(self):
        mm = self.client_for(self.maint)
        due = timezone.now() + timedelta(hours=2)
        t = mm.post("/api/r/tasks.task", {"title": "Confirm label delivery", "assignee": str(self.tech.pk), "due_at": due.isoformat()}).json()
        return mm, t, due

    def test_original_due_kept_and_revision_needs_reason(self):
        mm, t, due = self._task()
        task = Task.objects.get(pk=t["id"])
        self.assertEqual(task.original_due_at, task.due_at)
        # due date cannot be silently edited
        mm.patch(f"/api/r/tasks.task/{t['id']}", {"due_at": (due + timedelta(days=3)).isoformat(), "row_version": task.row_version})
        task.refresh_from_db()
        self.assertEqual(task.due_at, task.original_due_at)
        new_due = (due + timedelta(days=1)).isoformat()
        no_reason = mm.post(f"/api/r/tasks.task/{t['id']}/actions/revise_due", {"due_at": new_due})
        self.assertEqual(no_reason.json()["error"], "reason_required")
        revised = mm.post(f"/api/r/tasks.task/{t['id']}/actions/revise_due", {"due_at": new_due, "reason": "Supplier moved dispatch to Friday"})
        self.assertEqual(revised.status_code, 200, revised.content)  # applies directly
        task.refresh_from_db()
        self.assertEqual(task.original_due_at, due.replace(microsecond=task.original_due_at.microsecond))
        self.assertNotEqual(task.due_at, task.original_due_at)
        self.assertEqual(task.due_revisions[0]["reason"], "Supplier moved dispatch to Friday")

    def test_lateness_measured_against_original_due(self):
        _, t, _ = self._task()
        task = Task.objects.get(pk=t["id"])
        Task.objects.filter(pk=task.pk).update(original_due_at=timezone.now() - timedelta(hours=1), due_at=timezone.now() + timedelta(days=2))
        task.refresh_from_db()
        self.assertTrue(task.late_vs_original)


class PurchasePromiseTests(PlantTestCase):
    def _sent_line(self):
        sup = self.client_for(self.pur_sup)
        po = sup.post("/api/r/purchase.purchaseorder", {"supplier": str(self.supplier.pk)}).json()
        today = timezone.localdate()
        sup.post("/api/r/purchase.poline", {"po": po["id"], "item": str(self.pp.pk), "qty": 50, "rate": "1", "promised_date": str(today)})
        sup.post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/submit")
        self.client_for(self.pur_mgr).post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/approve")
        return sup, PurchaseOrder.objects.get(pk=po["id"]).lines.get(), today

    def test_original_supplier_promise_is_kept(self):
        sup, line, today = self._sent_line()
        later = today + timedelta(days=4)
        no_reason = sup.post(f"/api/r/purchase.poline/{line.pk}/actions/followup", {"promised_date": str(later)})
        self.assertEqual(no_reason.json()["error"], "reason_required")
        ok = sup.post(f"/api/r/purchase.poline/{line.pk}/actions/followup", {"promised_date": str(later), "overdue_reason": "Label printer breakdown"})
        self.assertEqual(ok.status_code, 200, ok.content)
        line.refresh_from_db()
        self.assertEqual((line.original_promised_date, line.promised_date, line.slip_days), (today, later, 4))
        self.assertEqual(line.promise_revisions[0]["reason"], "Label printer breakdown")

    def test_promise_change_suppresses_unsent_supplier_message(self):
        sup, line, today = self._sent_line()
        msg = draft_outbox(channel="whatsapp", recipient="+910000000002", template="purchase_order", payload={}, source_resource="purchase.purchaseorder",
                           source_id=line.po_id, source_version=line.po.row_version, dedupe_key=f"t:{line.po_id}")
        sup.post(f"/api/r/purchase.poline/{line.pk}/actions/followup", {"promised_date": str(today + timedelta(days=2)), "overdue_reason": "Truck delayed"})
        msg.refresh_from_db()
        self.assertEqual(msg.status, "suppressed")
        self.assertIn("promise revised", msg.last_error)


class OutboxStateTests(PlantTestCase):
    def _msg(self, status="approved"):
        m = draft_outbox(channel="whatsapp", recipient="+910000000000", template="t", payload={}, source_resource="fg.dispatch",
                         source_id="x", dedupe_key="k1")
        OutboxMessage.objects.filter(pk=m.pk).update(status=status)
        m.refresh_from_db()
        return m

    def test_callbacks_only_move_forward(self):
        m = self._msg()
        self.assertTrue(record_delivery_status(m, "accepted", provider_id="wamid.1"))
        self.assertTrue(record_delivery_status(m, "delivered"))
        self.assertFalse(record_delivery_status(m, "delivered"))  # duplicate
        self.assertFalse(record_delivery_status(m, "accepted"))   # out of order
        self.assertFalse(record_delivery_status(m, "failed"))     # cannot regress a delivered message
        self.assertTrue(record_delivery_status(m, "read"))
        self.assertFalse(record_delivery_status(m, "delivered"))
        m.refresh_from_db()
        self.assertEqual(m.status, "read")

    def test_draft_never_sent_by_callback(self):
        m = self._msg("draft")
        self.assertFalse(record_delivery_status(m, "delivered"))

    def test_newer_draft_suppresses_older(self):
        old = draft_outbox(channel="whatsapp", recipient="+910000000003", template="po", payload={}, source_resource="purchase.purchaseorder",
                           source_id="p1", source_version=1, dedupe_key="po:1")
        draft_outbox(channel="whatsapp", recipient="+910000000003", template="po", payload={}, source_resource="purchase.purchaseorder",
                     source_id="p1", source_version=2, dedupe_key="po:2")
        old.refresh_from_db()
        self.assertEqual(old.status, "suppressed")


class CommercialDataTests(PlantTestCase):
    def test_lead_needs_power_source_and_it_cannot_be_rewritten(self):
        c = self.client_for(self.sales)
        missing = c.post("/api/r/crm.lead", {"company": "Demo Dairy"})
        self.assertEqual(missing.status_code, 409)
        self.assertIn("power_source", missing.json()["details"])
        ok = c.post("/api/r/crm.lead", {"company": "Demo Dairy", "power_source": "word_of_mouth", "source": "Referred by Dairy Co"})
        self.assertEqual(ok.status_code, 201, ok.content)
        lead = Lead.objects.get(pk=ok.json()["id"])
        c.patch(f"/api/r/crm.lead/{lead.pk}", {"power_source": "paid", "row_version": lead.row_version})
        lead.refresh_from_db()
        self.assertEqual(lead.power_source, "word_of_mouth")
        meta = c.get("/api/meta").json()
        lead_meta = next(r for m in meta["modules"] for r in m["resources"] if r["key"] == "crm.lead")
        self.assertTrue(lead_meta["fields"]["power_source"]["required"])

    def test_quotation_states_price_basis(self):
        c = self.client_for(self.sales)
        q = c.post("/api/r/crm.quotation", {"customer": str(self.customer.pk), "valid_until": str(timezone.localdate() + timedelta(days=15))})
        self.assertEqual(q.status_code, 201, q.content)
        self.assertEqual((q.json()["price_basis"], q.json()["gst_rate_pct"]), ("excl_gst", "18.00"))

    def test_duplicate_supplier_invoice_is_flagged(self):
        store = self.client_for(self.store_mgr)
        base = {"supplier": str(self.supplier.pk), "location": str(self.loc("RM").pk)}
        first = store.post("/api/r/purchase.grn", {**base, "invoice_no": "INV/0042"})
        self.assertEqual(first.status_code, 201, first.content)
        dup = store.post("/api/r/purchase.grn", {**base, "invoice_no": "inv-42"})
        self.assertEqual(dup.json()["error"], "duplicate_supplier_invoice")
        explained = store.post("/api/r/purchase.grn", {**base, "invoice_no": "inv-42", "duplicate_note": "Second truck on the same supplier invoice"})
        self.assertEqual(explained.status_code, 201, explained.content)
        self.assertEqual(str(GRN.objects.get(pk=explained.json()["id"]).duplicate_of_id), first.json()["id"])
        other_supplier_same_no = GRN.normalise_invoice("INV 42")
        self.assertEqual(other_supplier_same_no, "INV42")


class NavigationMetaTests(PlantTestCase):
    def test_child_records_are_hidden_from_the_menu_but_still_described(self):
        meta = self.client_for(self.owner).get("/api/meta").json()
        nav = {r["key"]: r["nav"] for m in meta["modules"] for r in m["resources"]}
        self.assertFalse(nav["sales.soline"])
        self.assertFalse(nav["masters.recipeline"])
        self.assertTrue(nav["sales.salesorder"])
        self.assertTrue(nav["purchase.poline"])  # the purchase follow-up desk stays prominent
