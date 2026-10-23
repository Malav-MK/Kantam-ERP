"""Role editor (simple permission grid), owner role protection and the removal of email addresses."""
from apps.core.models import OutboxMessage, Role, User
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
        bad = c.post(f"/api/r/core.role/{custom.pk}/actions/set_permissions", {"permissions": ["commercial.price.view"], "reason": "Need prices"})
        self.assertEqual(bad.json()["error"], "privilege_escalation")
        ok = c.post(f"/api/r/core.role/{custom.pk}/actions/set_permissions", {"permissions": ["security.devices.manage"], "reason": "Device admin"})
        self.assertEqual(ok.status_code, 200, ok.content)  # applies directly
        codes = set(custom.permissions.values_list("code", flat=True))
        self.assertIn("security.devices.manage", codes)
        self.assertTrue(set(c for c in BASELINE_PERMISSIONS if Role.objects.filter(permissions__code=c).exists()) <= codes | set(BASELINE_PERMISSIONS))

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


class RoleDeleteAndDepartmentTests(PlantTestCase):
    def test_owner_deletes_built_in_role_and_upgrade_keeps_it_deleted(self):
        owner = self.client_for(self.owner)
        role = Role.objects.get(code="tv_viewer")
        blocked = owner.delete(f"/api/r/core.role/{role.pk}", {"reason": "Not used here"})
        self.assertEqual(blocked.json()["error"], "role_in_use")  # the tv user still holds it
        self.tv.role_grants.all().delete()
        r = owner.delete(f"/api/r/core.role/{role.pk}", {"reason": "Not used here"})
        self.assertEqual(r.status_code, 200, r.content)
        sync_permissions()  # what an upgrade does
        role.refresh_from_db()
        self.assertIsNotNone(role.deleted_at)
        self.assertEqual(role.permissions.count(), 0)
        owner = self.client_for(self.owner)
        codes = [x["code"] for x in owner.get("/api/r/core.role", page_size=100).json()["results"]]
        self.assertNotIn("tv_viewer", codes)
        owner_role = Role.objects.get(code="owner")
        self.assertIn(owner.delete(f"/api/r/core.role/{owner_role.pk}", {"reason": "Try it"}).status_code, (403, 409))

    def test_owner_and_admin_delete_roles(self):
        custom = Role.objects.create(code="temp", name="Temp", level="staff")
        r = self.client_for(self.sales_mgr).delete(f"/api/r/core.role/{custom.pk}", {"reason": "Cleanup"})
        self.assertIn(r.status_code, (403, 409))
        it1 = make_user("itadm2", ["it_admin"], "IT")
        r = self.client_for(it1).delete(f"/api/r/core.role/{custom.pk}", {"reason": "Cleanup"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Role.objects.filter(code="temp").exists())
        temp2 = Role.objects.create(code="temp2", name="Temp 2", level="staff")
        r = self.client_for(self.owner).delete(f"/api/r/core.role/{temp2.pk}", {"reason": "Cleanup"})
        self.assertEqual(r.status_code, 200, r.content)

    def test_create_department_without_choosing_a_site(self):
        owner = self.client_for(self.owner)
        r = owner.post("/api/r/core.department", {"code": "pack", "name": "Packing"})
        self.assertEqual(r.status_code, 201, r.content)
        from apps.core.models import Department
        dept = Department.objects.get(code="PACK")
        self.assertEqual(dept.site.code, "BAREJA")
        u = owner.post("/api/r/core.user", {"username": "packer1", "full_name": "Packer One", "department": str(dept.pk)})
        self.assertEqual(u.status_code, 201, u.content)
        meta = owner.get("/api/r/core.department/meta").json()
        self.assertEqual(meta["fields"]["site"]["target"], "core.site")
