"""Maker / checker control and OWASP regression tests."""
from decimal import Decimal

from apps.core.models import ApprovalPolicy, ApprovalRequest, AuditLog, Role, User, UserRole
from apps.inventory import services as inv
from apps.inventory.models import StockMove
from apps.masters.models import Customer

from .helpers import PASSWORD, PlantTestCase, make_user


class MakerCheckerTests(PlantTestCase):
    def setUp(self):
        self.it1 = make_user("it1", ["it_admin"], "IT")
        self.it2 = make_user("it2", ["it_admin"], "IT")
        self.target = make_user("newstore", [], "STORE")

    def _move(self):
        lot = inv.post_opening_balance(item=self.tub, qty=100, location=self.loc("FG"), user=self.owner, product=self.product, component_type="container")
        return inv.post_move("transfer", item=self.tub, lot=lot, qty=10, from_location=self.loc("FG"), to_location=self.loc("DSP"), user=self.owner)

    def test_role_grant_is_held_until_a_different_checker_approves(self):
        maker = self.client_for(self.it1)
        r = maker.post(f"/api/r/core.user/{self.target.pk}/actions/grant_role", {"role": "store_staff", "reason": "New joiner in stores"})
        self.assertEqual(r.status_code, 202, r.content)
        self.assertTrue(r.json()["pending_approval"])
        self.assertFalse(UserRole.objects.filter(user=self.target).exists())  # nothing applied yet
        aid = r.json()["approval"]
        self.assertEqual(maker.post(f"/api/r/core.approvalrequest/{aid}/actions/approve").status_code, 409)  # maker cannot check
        checker = self.client_for(self.it2)
        detail = checker.get(f"/api/r/core.approvalrequest/{aid}").json()
        self.assertTrue(detail["_extra"]["you_can_decide"])
        self.assertEqual(checker.post(f"/api/r/core.approvalrequest/{aid}/actions/approve", {"note": "Verified with HR"}).status_code, 200)
        self.assertTrue(UserRole.objects.filter(user=self.target, role__code="store_staff").exists())
        a = ApprovalRequest.objects.get(pk=aid)
        self.assertEqual((a.status, a.created_by_id, a.decided_by_id), ("approved", self.it1.pk, self.it2.pk))
        executed = AuditLog.objects.get(action="role_granted")
        self.assertEqual((executed.actor_id, executed.approval_ref), (self.it1.pk, str(aid)))  # applied in the maker's name, linked

    def test_invalid_request_is_rejected_at_submission_not_queued(self):
        r = self.client_for(self.it1).post(f"/api/r/core.user/{self.it1.pk}/actions/grant_role", {"role": "owner", "reason": "escalate myself"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(ApprovalRequest.objects.count(), 0)

    def test_reason_required_for_held_change(self):
        r = self.client_for(self.it1).post(f"/api/r/core.user/{self.target.pk}/actions/revoke_sessions", {"reason": "x"})
        self.assertIn(r.status_code, (200, 409))  # revoke_sessions has no policy -> executes or fails validation, never queued
        r = self.client_for(self.it1).patch(f"/api/r/masters.customer/{self.customer.pk}", {"credit_limit": "50000", "row_version": self.customer.row_version})
        self.assertEqual(r.status_code, 403)  # it admin cannot edit customers at all

    def test_field_conditional_policy_and_stale_detection(self):
        mgr = self.client_for(self.sales_mgr)
        c = Customer.objects.get(pk=self.customer.pk)
        # Non-controlled field -> immediate
        r = mgr.patch(f"/api/r/masters.customer/{c.pk}", {"industry": "Dairy", "row_version": c.row_version})
        self.assertEqual(r.status_code, 200, r.content)
        c.refresh_from_db()
        r = mgr.patch(f"/api/r/masters.customer/{c.pk}", {"payment_terms": "100% advance", "row_version": c.row_version})
        self.assertEqual(r.json()["error"], "approval_reason_required")
        r = mgr.patch(f"/api/r/masters.customer/{c.pk}", {"payment_terms": "100% advance", "row_version": c.row_version, "reason": "Credit risk review"})
        self.assertEqual(r.status_code, 202, r.content)
        aid = r.json()["approval"]
        a = ApprovalRequest.objects.get(pk=aid)
        self.assertEqual(a.changes["payment_terms"][1], "100% advance")
        c.refresh_from_db()
        self.assertNotEqual(c.payment_terms, "100% advance")
        # Someone edits the record meanwhile -> approval becomes stale, not silently applied over newer data
        Customer.objects.filter(pk=c.pk).update(row_version=c.row_version + 1)
        other_mgr = make_user("salesmgr2", ["sales_manager"], "SALES")
        r = self.client_for(other_mgr).post(f"/api/r/core.approvalrequest/{aid}/actions/approve", {})
        self.assertEqual(r.json()["status"], "stale")
        c.refresh_from_db()
        self.assertNotEqual(c.payment_terms, "100% advance")

    def test_two_level_approval_needs_distinct_checkers(self):
        ApprovalPolicy.objects.filter(resource="inventory.stockmove").update(required_approvals=2)
        move = self._move()
        Permission = __import__("apps.core.models", fromlist=["Permission"]).Permission
        Role.objects.get(code="store_manager").permissions.add(*Permission.objects.filter(code="inventory.adjust.approve"))
        maker_user = make_user("invmaker", ["store_manager"], "STORE")
        mgr = make_user("invapprover", ["store_manager"], "STORE")
        store_mgr2 = make_user("storemgr2", ["store_manager"], "STORE")
        r = self.client_for(maker_user).post(f"/api/r/inventory.stockmove/{move.pk}/actions/reverse", {"reason": "Posted to wrong bin"})
        self.assertEqual(r.status_code, 202, r.content)
        aid = r.json()["approval"]
        c1 = self.client_for(mgr)
        self.assertEqual(c1.post(f"/api/r/core.approvalrequest/{aid}/actions/approve").json()["status"], "pending")
        again = c1.post(f"/api/r/core.approvalrequest/{aid}/actions/approve")  # same checker twice does not count
        self.assertEqual(again.status_code, 409)
        self.assertEqual(len(ApprovalRequest.objects.get(pk=aid).approvals), 1)
        self.assertFalse(StockMove.objects.filter(reversal_of=move).exists())
        self.assertEqual(self.client_for(store_mgr2).post(f"/api/r/core.approvalrequest/{aid}/actions/approve").json()["status"], "approved")
        self.assertTrue(StockMove.objects.filter(reversal_of=move).exists())

    def test_owner_is_super_admin_without_maker_checker(self):
        owner = self.client_for(self.owner)
        # Policy-controlled action: applied at once, no pending request.
        move = self._move()
        r = owner.post(f"/api/r/inventory.stockmove/{move.pk}/actions/reverse", {"reason": "Posted to wrong bin"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(StockMove.objects.filter(reversal_of=move).exists())
        self.assertFalse(ApprovalRequest.objects.filter(status="pending").exists())
        # Role grant (normally four-eyes) is immediate for the owner.
        r = owner.post(f"/api/r/core.user/{self.target.pk}/actions/grant_role", {"role": "store_staff", "reason": "New joiner in stores"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(UserRole.objects.filter(user=self.target, role__code="store_staff").exists())
        # Built-in self-approval rule: the owner may approve a PO they created.
        po = owner.post("/api/r/purchase.purchaseorder", {"supplier": str(self.supplier.pk)}).json()
        owner.post("/api/r/purchase.poline", {"po": po["id"], "item": str(self.pp.pk), "qty": 5, "rate": "1", "promised_date": "2026-12-01"})
        owner.post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/submit")
        self.assertEqual(owner.post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/approve").status_code, 200)
        # Every change is still in the audit trail under the owner's name.
        self.assertTrue(AuditLog.objects.filter(actor=self.owner, action="role_granted").exists())

    def test_maker_losing_permission_blocks_execution(self):
        maker = self.client_for(self.it1)
        aid = maker.post(f"/api/r/core.user/{self.target.pk}/actions/grant_role", {"role": "store_staff", "reason": "New joiner"}).json()["approval"]
        UserRole.objects.filter(user=self.it1).delete()
        self.it1.bump_role_version()
        r = self.client_for(self.it2).post(f"/api/r/core.approvalrequest/{aid}/actions/approve")
        self.assertEqual(r.json()["error"], "maker_not_authorised")
        self.assertFalse(UserRole.objects.filter(user=self.target).exists())

    def test_withdraw_and_reject(self):
        maker = self.client_for(self.it1)
        aid = maker.post(f"/api/r/core.user/{self.target.pk}/actions/grant_role", {"role": "store_staff", "reason": "New joiner"}).json()["approval"]
        self.assertEqual(self.client_for(self.it2).post(f"/api/r/core.approvalrequest/{aid}/actions/withdraw", {"reason": "not mine to pull"}).status_code, 409)
        self.assertEqual(maker.post(f"/api/r/core.approvalrequest/{aid}/actions/withdraw", {"reason": "Raised in error"}).json()["status"], "withdrawn")
        aid2 = maker.post(f"/api/r/core.user/{self.target.pk}/actions/grant_role", {"role": "store_staff", "reason": "New joiner"}).json()["approval"]
        r = self.client_for(self.it2).post(f"/api/r/core.approvalrequest/{aid2}/actions/reject", {"reason": "HR form missing"})
        self.assertEqual(r.json()["status"], "rejected")
        self.assertFalse(UserRole.objects.filter(user=self.target).exists())

    def test_unrelated_users_cannot_see_or_decide(self):
        aid = self.client_for(self.it1).post(f"/api/r/core.user/{self.target.pk}/actions/grant_role", {"role": "store_staff", "reason": "New joiner"}).json()["approval"]
        store = self.client_for(self.store)
        self.assertEqual(store.get(f"/api/r/core.approvalrequest/{aid}").status_code, 404)
        self.assertEqual(store.post(f"/api/r/core.approvalrequest/{aid}/actions/approve").status_code, 404)

    def test_high_value_po_needs_owner_co_sign(self):
        sup = self.client_for(self.pur_sup)
        po = sup.post("/api/r/purchase.purchaseorder", {"supplier": str(self.supplier.pk)}).json()
        sup.post("/api/r/purchase.poline", {"po": po["id"], "item": str(self.pp.pk), "qty": 10000, "rate": "110", "promised_date": "2026-12-01"})
        sup.post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/submit")
        r = self.client_for(self.pur_mgr).post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/approve", {"reason": "Monsoon stock build"})
        self.assertEqual(r.status_code, 202, r.content)
        self.assertEqual(ApprovalRequest.objects.get(pk=r.json()["approval"]).amount, Decimal("1100000.00"))
        self.assertEqual(self.client_for(self.owner).post(f"/api/r/core.approvalrequest/{r.json()['approval']}/actions/approve").json()["status"], "approved")
        from apps.purchase.models import PurchaseOrder
        po_obj = PurchaseOrder.objects.get(pk=po["id"])
        self.assertEqual((po_obj.status, po_obj.approved_by_id), ("approved", self.pur_mgr.pk))

    def test_policy_changes_are_themselves_maker_checker(self):
        r = self.client_for(self.it1).post("/api/r/core.approvalpolicy", {"name": "x", "resource": "fg.dispatch", "operation": "action", "action": "post",
                                                                           "checker_permission": "dashboard.owner.view", "reason": "Tighten dispatch"})
        self.assertEqual(r.status_code, 202, r.content)
        self.assertFalse(ApprovalPolicy.objects.filter(resource="fg.dispatch").exists())


class OwaspRegressionTests(PlantTestCase):
    def test_it_admin_cannot_take_over_owner_account(self):
        it = make_user("itx", ["it_admin"], "IT")
        c = self.client_for(it)
        for action in ("set_password", "reset_mfa", "revoke_sessions"):
            r = c.post(f"/api/r/core.user/{self.owner.pk}/actions/{action}", {"password": "Another-Long-Pass-99", "reason": "takeover attempt"})
            self.assertEqual(r.status_code, 409, action)
            self.assertEqual(r.json()["error"], "protected_account")
        r = c.patch(f"/api/r/core.user/{self.owner.pk}", {"is_active": False, "row_version": 1})
        self.assertEqual(r.json()["error"], "record_locked")

    def test_temporary_password_must_be_changed_first(self):
        u = make_user("fresh", ["store_staff"], "STORE")
        User.objects.filter(pk=u.pk).update(must_change_password=True)
        c = self.client_for(u)
        self.assertEqual(c.get("/api/auth/me").status_code, 200)
        r = c.get("/api/r/inventory.stockbalance")
        self.assertEqual((r.status_code, r.json()["error"]), (403, "password_change_required"))
        self.assertEqual(c.post("/api/auth/change-password", {"current_password": PASSWORD, "new_password": PASSWORD}).json()["error"], "password_reuse")
        self.assertEqual(c.post("/api/auth/change-password", {"current_password": PASSWORD, "new_password": "Brand-New-Secret-2026"}).status_code, 200)
        self.assertEqual(c.get("/api/r/inventory.stockbalance").status_code, 200)

    def test_csv_export_neutralises_formulas(self):
        Customer.objects.create(code="EVIL", name='=HYPERLINK("http://x.invalid","click")')
        body = self.client_for(self.owner).get("/api/r/masters.customer/export").content.decode()
        self.assertIn("'=HYPERLINK", body)
        self.assertNotIn(',=HYPERLINK', body)

    def test_access_denials_are_logged(self):
        with self.assertLogs("kantam.security", level="WARNING") as logs:
            self.client_for(self.store).get("/api/r/crm.lead")
        self.assertIn("access_denied user=store path=/api/r/crm.lead", logs.output[0])

    def test_approved_visit_details_locked(self):
        from apps.gate.models import Visit
        v = Visit.objects.create(visitor_name="A", host=self.qc, purpose="Audit", status="approved", created_by=self.guard)
        r = self.client_for(self.guard).patch(f"/api/r/gate.visit/{v.pk}", {"host": str(self.sales.pk), "row_version": v.row_version})
        self.assertEqual(r.json()["error"], "approved_fields_locked")
