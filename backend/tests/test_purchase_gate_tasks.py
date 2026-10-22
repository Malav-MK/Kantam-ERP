"""Purchase/GRN (p.13, p.39), gate security (p.37-45), tasks & PM (p.48-57)."""
import io
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from apps.core.models import Attachment
from apps.gate.models import Visit
from apps.inventory import services as inv
from apps.purchase.models import POLine, PurchaseOrder
from apps.tasks.models import Asset, PMPlan, Task
from apps.tasks.services import generate_pm_tasks

from .helpers import PlantTestCase


def img(color=(10, 120, 10)):
    buf = io.BytesIO()
    Image.new("RGB", (6, 6), color).save(buf, format="JPEG")
    return SimpleUploadedFile("photo.jpg", buf.getvalue(), "image/jpeg")


class PurchaseReceiptTests(PlantTestCase):
    def approved_po(self, qty=500):
        sup = self.client_for(self.pur_sup)
        po = sup.post("/api/r/purchase.purchaseorder", {"supplier": str(self.supplier.pk)}).json()
        r = sup.post("/api/r/purchase.poline", {"po": po["id"], "item": str(self.pp.pk), "qty": qty, "rate": "110", "promised_date": str(timezone.localdate())})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(sup.post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/submit").status_code, 200)
        self.assertEqual(sup.post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/approve").status_code, 403)  # no authority
        mgr = self.client_for(self.pur_mgr)
        self.assertEqual(mgr.post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/approve").status_code, 200)
        return PurchaseOrder.objects.get(pk=po["id"])

    def test_500_declared_490_received_20_held_gives_470_usable(self):
        """Acceptance p.39 + p.13 partial receipt."""
        po = self.approved_po(1000)
        pol = po.lines.get()
        guard = self.client_for(self.guard)
        gate = guard.post("/api/r/gate.gatematerial", {"direction": "in", "supplier": str(self.supplier.pk), "document_no": "INV-9", "packages": 20}).json()
        guard.post("/api/r/gate.gatematerialline", {"entry": gate["id"], "description": "PP bags", "declared_qty": 500, "uom": "kg"})
        blocked = guard.post(f"/api/r/gate.gatematerial/{gate['id']}/actions/arrive")
        self.assertEqual(blocked.json()["error"], "evidence_required")
        first = guard.upload(f"/api/r/gate.gatematerial/{gate['id']}/attachments", img(), "invoice")
        again = guard.upload(f"/api/r/gate.gatematerial/{gate['id']}/attachments", img(), "invoice")
        self.assertEqual((first.status_code, again.status_code), (201, 200))  # same photo -> same attachment
        self.assertEqual(Attachment.objects.filter(purpose="invoice").count(), 1)
        self.assertEqual(guard.post(f"/api/r/gate.gatematerial/{gate['id']}/actions/arrive").status_code, 200)
        self.assertEqual(inv.balance_summary(self.pp)["usable"], 0)  # guard cannot create usable stock

        store = self.client_for(self.store_mgr)
        grn = store.post("/api/r/purchase.grn", {"supplier": str(self.supplier.pk), "gate_entry": gate["id"], "location": str(self.loc("RM").pk)}).json()
        line = store.post("/api/r/purchase.grnline", {"grn": grn["id"], "po_line": str(pol.pk), "item": str(self.pp.pk), "declared_qty": 500, "received_qty": 490}).json()
        self.assertEqual(store.post(f"/api/r/purchase.grn/{grn['id']}/actions/post").status_code, 200)
        self.assertEqual(inv.balance_summary(self.pp)["usable"], 0)  # awaiting QC
        qc = self.client_for(self.qc)
        bad = qc.post(f"/api/r/purchase.grnline/{line['id']}/actions/qc_decide", {"accepted": 480, "held": 20})
        self.assertEqual(bad.json()["error"], "not_reconciled")
        ok = qc.post(f"/api/r/purchase.grnline/{line['id']}/actions/qc_decide", {"accepted": 470, "held": 20})
        self.assertEqual(ok.status_code, 200, ok.content)
        bal = inv.balance_summary(self.pp)
        self.assertEqual((bal["usable"], bal["held"]), (Decimal("470"), Decimal("20")))
        pol.refresh_from_db()
        self.assertEqual((pol.received_qty, pol.accepted_qty, pol.balance_qty, pol.status), (Decimal("490"), Decimal("470"), Decimal("530"), "partial"))
        self.assertEqual(po.lines.get().grn_lines.get().declared_qty, Decimal("500"))

    def test_creator_cannot_approve_own_po(self):
        mgr = self.client_for(self.pur_mgr)
        po = mgr.post("/api/r/purchase.purchaseorder", {"supplier": str(self.supplier.pk)}).json()
        mgr.post("/api/r/purchase.poline", {"po": po["id"], "item": str(self.pp.pk), "qty": 5, "rate": "1", "promised_date": str(timezone.localdate())})
        mgr.post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/submit")
        self.assertEqual(mgr.post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/approve").json()["error"], "self_approval")

    def test_supplier_rates_hidden_from_store(self):
        po = self.approved_po()
        row = self.client_for(self.store_mgr).get(f"/api/r/purchase.poline/{po.lines.get().pk}").json()
        self.assertNotIn("rate", row)

    def test_late_arrival_does_not_cover_earlier_shortage(self):
        from apps.purchase.services import shortage_report
        self.pp.safety_stock = 0
        self.pp.min_stock = 100
        self.pp.save()
        po = self.approved_po(1000)
        POLine.objects.filter(po=po).update(promised_date=timezone.localdate() + timedelta(days=30))
        rows = {r["item_code"]: r for r in shortage_report()}
        self.assertIn("PP", rows)


class GateTests(PlantTestCase):
    def test_visitor_flow_host_approval_and_guard_cannot_self_approve(self):
        guard = self.client_for(self.guard)
        v = guard.post("/api/r/gate.visit", {"visitor_name": "Mr Auditor", "organisation": "Cust QA", "host": str(self.qc.pk), "purpose": "Audit"}).json()
        self.assertEqual(guard.post(f"/api/r/gate.visit/{v['id']}/actions/request_approval").json()["error"], "evidence_required")
        guard.upload(f"/api/r/gate.visit/{v['id']}/attachments", img(), "visitor_photo", capture_source="live")
        self.assertEqual(guard.post(f"/api/r/gate.visit/{v['id']}/actions/request_approval").status_code, 200)
        self.assertEqual(guard.post(f"/api/r/gate.visit/{v['id']}/actions/approve").status_code, 403)
        host = self.client_for(self.qc)
        inbox = host.get("/api/r/gate.myvisit").json()
        self.assertEqual(inbox["count"], 1)
        self.assertEqual(self.client_for(self.store).get("/api/r/gate.myvisit").json()["count"], 0)  # not their visit
        self.assertEqual(host.post(f"/api/r/gate.myvisit/{v['id']}/actions/approve", {"note": "escort to QC lab"}).status_code, 200)
        guard.patch(f"/api/r/gate.visit/{v['id']}", {"badge_no": "V-12", "safety_briefing_ack": True, "row_version": Visit.objects.get(pk=v["id"]).row_version})
        self.assertEqual(guard.post(f"/api/r/gate.visit/{v['id']}/actions/enter").json()["error"], "evidence_required")
        guard.upload(f"/api/r/gate.visit/{v['id']}/attachments", img((1, 2, 3)), "entry_photo")
        self.assertEqual(guard.post(f"/api/r/gate.visit/{v['id']}/actions/enter").status_code, 200)
        guard.upload(f"/api/r/gate.visit/{v['id']}/attachments", img((4, 5, 6)), "exit_photo")
        self.assertEqual(guard.post(f"/api/r/gate.visit/{v['id']}/actions/exit", {"badge_returned": True}).status_code, 200)
        visit = Visit.objects.get(pk=v["id"])
        self.assertEqual(visit.status, "exited")
        self.assertIsNotNone(visit.entry_at)
        locked = guard.upload(f"/api/r/gate.visit/{v['id']}/attachments", img((9, 9, 9)), "extra")
        self.assertEqual(locked.json()["error"], "attachment_locked")

    def test_server_generates_gate_timestamps(self):
        from apps.gate.models import GateEvent, Person
        p = Person.objects.create(person_code="E1", name="Worker", kind="employee")
        guard = self.client_for(self.guard)
        r = guard.post("/api/r/gate.gateevent", {"person": str(p.pk), "kind": "entry", "at": "2001-01-01T00:00:00Z"})
        self.assertEqual(r.status_code, 201)
        self.assertGreater(GateEvent.objects.get().at, timezone.now() - timedelta(minutes=1))  # forged time ignored
        self.assertEqual(guard.post("/api/r/gate.gateevent", {"person": str(p.pk), "kind": "entry"}).json()["error"], "repeat_tap")

    def test_unauthorised_outward_load_cannot_be_cleared(self):
        from apps.fg.models import Dispatch
        from apps.sales.models import SalesOrder
        so = SalesOrder.objects.create(customer=self.customer, salesperson=self.sales, status="confirmed")
        d = Dispatch.objects.create(so=so, customer=self.customer, planned_date=timezone.localdate(), status="approved")
        disp = self.client_for(self.dispatcher)
        self.assertEqual(self.client_for(self.guard).post("/api/r/gate.gatematerial", {"direction": "out", "dispatch": str(d.pk), "document_no": "CH-0"}).status_code, 409)
        g = disp.post("/api/r/gate.gatematerial", {"direction": "out", "dispatch": str(d.pk), "document_no": "CH-1", "packages": 10})
        self.assertEqual(g.status_code, 201, g.content)
        guard = self.client_for(self.guard)
        self.assertEqual(guard.post(f"/api/r/gate.gatematerial/{g.json()['id']}/actions/verify", {"packages_counted": 10}).status_code, 409)
        r = disp.post(f"/api/r/gate.gatematerial/{g.json()['id']}/actions/authorise")
        self.assertEqual(r.json()["error"], "dispatch_not_posted")

    def test_courier_needs_named_receiver_acknowledgement(self):
        guard = self.client_for(self.guard)
        cr = guard.post("/api/r/gate.courier", {"direction": "in", "courier_company": "BlueDart", "awb": "123", "sender": "Vendor", "receiver": str(self.qc.pk)}).json()
        guard.upload(f"/api/r/gate.courier/{cr['id']}/attachments", img(), "parcel")
        self.assertEqual(self.client_for(self.store).post(f"/api/r/gate.mycourier/{cr['id']}/actions/acknowledge").status_code, 404)
        self.assertEqual(self.client_for(self.qc).post(f"/api/r/gate.mycourier/{cr['id']}/actions/acknowledge", {"outcome": "sample"}).status_code, 200)

    def test_returnable_pass_partial_return_stays_open(self):
        mm = self.client_for(self.maint)
        gp = mm.post("/api/r/gate.gatepass", {"returnable": True, "asset_description": "Tub mould", "qty": 3, "recipient": "Tool room vendor",
                                             "purpose": "Service", "due_back": str(timezone.localdate() + timedelta(days=7))}).json()
        self.assertEqual(mm.post(f"/api/r/gate.gatepass/{gp['id']}/actions/approve").json()["error"], "self_approval")
        self.assertEqual(self.client_for(self.owner).post(f"/api/r/gate.gatepass/{gp['id']}/actions/approve").status_code, 200)
        guard = self.client_for(self.guard)
        guard.upload(f"/api/r/gate.gatepass/{gp['id']}/attachments", img(), "out_photo")
        self.assertEqual(guard.post(f"/api/r/gate.gatepass/{gp['id']}/actions/out").status_code, 200)
        r = guard.post(f"/api/r/gate.gatepass/{gp['id']}/actions/return", {"qty": 2, "condition": "accessories pending"})
        self.assertEqual(r.json()["status"], "partial")


class TaskTests(PlantTestCase):
    def test_proof_rounds_and_reviewer_separation(self):
        mm = self.client_for(self.maint)
        t = mm.post("/api/r/tasks.task", {"title": "Fix robot gripper", "assignee": str(self.tech.pk), "due_at": (timezone.now() + timedelta(hours=2)).isoformat(),
                                          "evidence_required": "photo"}).json()
        tech = self.client_for(self.tech)
        for a in ("accept", "start"):
            self.assertEqual(tech.post(f"/api/r/tasks.task/{t['id']}/actions/{a}").status_code, 200)
        blocked = tech.post(f"/api/r/tasks.task/{t['id']}/actions/submit", {"completion_note": "done"})
        self.assertEqual(blocked.json()["error"], "proof_required")
        tech.upload(f"/api/r/tasks.task/{t['id']}/attachments", img(), "after_photo")
        self.assertEqual(tech.post(f"/api/r/tasks.task/{t['id']}/actions/submit", {"completion_note": "done"}).status_code, 200)
        self.assertEqual(tech.post(f"/api/r/tasks.task/{t['id']}/actions/verify").status_code, 403)
        self.assertEqual(mm.post(f"/api/r/tasks.task/{t['id']}/actions/reopen", {"reason": "Photo does not show gripper"}).status_code, 200)
        task = Task.objects.get(pk=t["id"])
        self.assertEqual((task.state, task.proof_round), ("reopened", 2))
        tech.post(f"/api/r/tasks.task/{t['id']}/actions/start")
        again = tech.post(f"/api/r/tasks.task/{t['id']}/actions/submit", {"completion_note": "done"})
        self.assertEqual(again.json()["error"], "proof_required")  # old round's photo does not count
        tech.upload(f"/api/r/tasks.task/{t['id']}/attachments", img((50, 50, 50)), "after_photo")
        self.assertEqual(tech.post(f"/api/r/tasks.task/{t['id']}/actions/submit", {}).status_code, 200)
        self.assertEqual(mm.post(f"/api/r/tasks.task/{t['id']}/actions/verify", {"note": "OK"}).status_code, 200)

    def test_assignee_sees_only_own_tasks(self):
        Task.objects.create(title="Secret", assignee=self.maint, due_at=timezone.now())
        self.assertEqual(self.client_for(self.tech).get("/api/r/tasks.task").json()["count"], 0)

    def test_pm_generation_is_idempotent_and_does_not_flood(self):
        asset = Asset.objects.create(code="A1", name="M01", kind="machine", machine=self.m1)
        plan = PMPlan.objects.create(asset=asset, title="Shift check", interval="12h", oem_reference="manual", assignee=self.tech,
                                     next_due=timezone.now() - timedelta(days=5), status="approved", checklist=[])
        self.assertEqual(generate_pm_tasks(), 1)
        self.assertEqual(generate_pm_tasks(), 0)  # concurrent scheduler / retry
        plan.refresh_from_db()
        self.assertGreater(plan.next_due, timezone.now())  # missed slots skipped, not flooded
        self.assertEqual(Task.objects.filter(pm_plan=plan).count(), 1)

    def test_unapproved_pm_plan_is_inactive(self):
        asset = Asset.objects.create(code="A2", name="M02", kind="machine")
        PMPlan.objects.create(asset=asset, title="x", interval="24h", assignee=self.tech, next_due=timezone.now() - timedelta(hours=1))
        self.assertEqual(generate_pm_tasks(), 0)

    def test_quarterly_interval_month_end(self):
        import datetime
        from apps.tasks.services import next_due
        d = datetime.datetime(2026, 11, 30, 9, tzinfo=datetime.timezone.utc)
        self.assertEqual(next_due("quarterly", d).date(), datetime.date(2027, 2, 28))
        self.assertEqual(next_due("yearly", datetime.datetime(2028, 2, 29, tzinfo=datetime.timezone.utc)).date(), datetime.date(2029, 2, 28))
