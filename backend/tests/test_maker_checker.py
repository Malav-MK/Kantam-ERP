"""Changes apply directly (maker / checker removed) and OWASP regression tests."""

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

    def test_changes_apply_directly_without_a_checker(self):
        maker = self.client_for(self.it1)
        r = maker.post(f"/api/r/core.user/{self.target.pk}/actions/grant_role", {"role": "store_staff", "reason": "New joiner in stores"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(UserRole.objects.filter(user=self.target, role__code="store_staff").exists())
        self.assertFalse(ApprovalRequest.objects.exists())
        self.assertFalse(ApprovalPolicy.objects.filter(active=True).exists())  # no default policies are seeded any more
        self.assertNotIn("approval_rules", maker.get("/api/r/core.user/meta").json())
        self.assertEqual(maker.get("/api/r/core.approvalpolicy").status_code, 404)

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
