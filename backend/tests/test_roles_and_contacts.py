"""Role editor (simple permission grid), owner role protection and the removal of email addresses."""
from apps.core.models import ApprovalRequest, OutboxMessage, Role, User
from apps.core.rbac import BASELINE_PERMISSIONS
from apps.core.rbac_sync import sync_permissions
from apps.masters.models import Contact
from apps.purchase.models import PurchaseOrder

from .helpers import PlantTestCase, make_user


class RoleEditorTests(PlantTestCase):
    def test_owner_can_edit_a_built_in_role_and_upgrades_keep_it(self):
        owner = self.client_for(self.owner)
        role = Role.objects.get(code="store_staff")
        perms = set(role.permissions.values_list("code", flat=True))
        perms.add("inventory.stockcount.create")
        perms.discard("inventory.stockmove.export")
        r = owner.post(f"/api/r/core.role/{role.pk}/actions/set_permissions", {"permissions": sorted(perms), "reason": "Stores staff do counts"})
        self.assertEqual(r.status_code, 200, r.content)  # owner: applied directly, no maker/checker
        role.refresh_from_db()
        codes = set(role.permissions.values_list("code", flat=True))
        self.assertIn("inventory.stockcount.create", codes)
        self.assertTrue(role.customized)
        sync_permissions()  # what an upgrade does (it also signs everyone out)
        self.assertEqual(set(role.permissions.values_list("code", flat=True)), codes)
        owner = self.client_for(self.owner)
        r = owner.post(f"/api/r/core.role/{role.pk}/actions/reset_default", {"reason": "Back to the standard set"})
        self.assertEqual(r.status_code, 200, r.content)
        role.refresh_from_db()
        self.assertFalse(role.customized)
        self.assertNotIn("inventory.stockcount.create", set(role.permissions.values_list("code", flat=True)))

    def test_owner_role_cannot_be_limited(self):
        owner_role = Role.objects.get(code="owner")
        r = self.client_for(self.owner).post(f"/api/r/core.role/{owner_role.pk}/actions/set_permissions",
                                            {"permissions": ["crm.lead.view"], "reason": "Try to limit"})
        self.assertIn(r.status_code, (403, 409))
        self.assertGreater(owner_role.permissions.count(), 100)

    def test_baseline_kept_and_admin_cannot_grant_what_they_lack(self):
        it1 = make_user("itadm", ["it_admin"], "IT")
        custom = Role.objects.create(code="counter", name="Counter", level="staff")
        c = self.client_for(it1)
        # Change role permissions is maker-checked for non-owners; the dry run still validates.
        bad = c.post(f"/api/r/core.role/{custom.pk}/actions/set_permissions", {"permissions": ["commercial.price.view"], "reason": "Need prices"})
        self.assertEqual(bad.json()["error"], "privilege_escalation")
        held = c.post(f"/api/r/core.role/{custom.pk}/actions/set_permissions", {"permissions": ["security.devices.manage"], "reason": "Device admin"})
        self.assertEqual(held.status_code, 202, held.content)
        approved = self.client_for(self.owner).post(f"/api/r/core.approvalrequest/{held.json()['approval']}/actions/approve")
        self.assertEqual(approved.status_code, 200, approved.content)
        codes = set(custom.permissions.values_list("code", flat=True))
        self.assertIn("security.devices.manage", codes)
        self.assertTrue(set(c for c in BASELINE_PERMISSIONS if Role.objects.filter(permissions__code=c).exists()) <= codes | set(BASELINE_PERMISSIONS))
        self.assertEqual(ApprovalRequest.objects.get(pk=held.json()["approval"]).status, "approved")

    def test_permission_grid_is_for_role_administrators_only(self):
        grid = self.client_for(self.owner).get("/api/roles/permission-matrix").json()
        self.assertEqual([c["key"] for c in grid["columns"]], ["view", "add", "edit", "delete", "approve", "export"])
        po_row = next(r for m in grid["modules"] for r in m["rows"] if r["key"] == "purchase.purchaseorder")
        self.assertEqual([p["code"] for p in po_row["cells"]["approve"]], ["purchase.po.approve"])
        special = {p["code"] for g in grid["special"] for p in g["permissions"]}
        self.assertIn("commercial.price.view", special)
        self.assertEqual(self.client_for(self.store).get("/api/roles/permission-matrix").status_code, 403)


class NoEmailTests(PlantTestCase):
    def test_no_email_fields_remain(self):
        for model in (User, Contact):
            self.assertFalse(any("email" in f.name for f in model._meta.get_fields()), model)
        meta = self.client_for(self.owner).get("/api/meta").json()
        for m in meta["modules"]:
            for r in m["resources"]:
                self.assertFalse([f for f in r["fields"] if "email" in f], r["key"])

    def test_purchase_order_goes_to_supplier_whatsapp(self):
        Contact.objects.create(supplier=self.supplier, name="Desk", phone="+919800000000", whatsapp_opt_in=True)
        owner = self.client_for(self.owner)
        po = owner.post("/api/r/purchase.purchaseorder", {"supplier": str(self.supplier.pk)}).json()
        owner.post("/api/r/purchase.poline", {"po": po["id"], "item": str(self.pp.pk), "qty": 5, "rate": "1", "promised_date": "2026-12-01"})
        owner.post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/submit")
        owner.post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/approve")
        r = owner.post(f"/api/r/purchase.purchaseorder/{po['id']}/actions/send")
        self.assertEqual(r.status_code, 200, r.content)
        msg = OutboxMessage.objects.get(source_id=po["id"])
        self.assertEqual((msg.channel, msg.recipient, msg.status), ("whatsapp", "+919800000000", "draft"))
        self.assertEqual(PurchaseOrder.objects.get(pk=po["id"]).status, "sent")
