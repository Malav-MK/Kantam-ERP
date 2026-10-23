"""Owner / admin can permanently delete existing and default entries; records still in use stay protected."""
from decimal import Decimal

from apps.core.models import AuditLog, Department, User
from apps.inventory import services as inv
from apps.inventory.models import Lot, StockBalance, StockMove
from apps.masters.models import Location, Machine
from apps.sales.models import SalesOrder, SOLine

from .helpers import PlantTestCase, make_user


class OwnerDeleteTests(PlantTestCase):
    def test_owner_deletes_an_unused_default_entry_and_it_is_audited(self):
        owner = self.client_for(self.owner)
        meta = owner.get("/api/r/masters.machine/meta").json()
        self.assertTrue(meta["can_delete"])
        self.assertTrue(owner.get(f"/api/r/masters.machine/{self.m2.pk}").json()["_meta"]["deletable"])
        no_reason = owner.delete(f"/api/r/masters.machine/{self.m2.pk}", {"reason": ""})
        self.assertEqual(no_reason.json()["error"], "reason_required")
        r = owner.delete(f"/api/r/masters.machine/{self.m2.pk}", {"reason": "Machine sold"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Machine.objects.filter(pk=self.m2.pk).exists())
        entry = AuditLog.objects.get(action="delete", resource="masters.machine", object_id=str(self.m2.pk))
        self.assertEqual((entry.reason, entry.changes["code"]), ("Machine sold", "M02"))

    def test_admin_can_delete_too(self):
        admin = self.client_for(make_user("itadm", ["it_admin"], "IT"))
        self.assertTrue(admin.get("/api/r/core.department/meta").json()["can_delete"])
        hr = Department.objects.get(code="HR")
        self.assertEqual(admin.delete(f"/api/r/core.department/{hr.pk}", {"reason": "No HR team here"}).status_code, 200)
        self.assertEqual(admin.delete(f"/api/r/core.user/{self.owner.pk}", {"reason": "Cleanup"}).json()["error"], "delete_not_allowed")

    def test_only_the_owner_can_delete(self):
        mgr = self.client_for(self.store_mgr)
        self.assertFalse(mgr.get("/api/r/masters.location/meta").json()["can_delete"])
        r = mgr.delete(f"/api/r/masters.location/{self.loc('SCR').pk}", {"reason": "Not needed"})
        self.assertIn(r.status_code, (403, 409))
        self.assertTrue(Location.objects.filter(code="SCR").exists())

    def test_record_in_use_is_kept_with_a_plain_message(self):
        inv.post_opening_balance(item=self.pp, qty=100, location=self.loc("RM"), user=self.owner)
        r = self.client_for(self.owner).delete(f"/api/r/masters.item/{self.pp.pk}", {"reason": "Wrong item"})
        self.assertEqual(r.json()["error"], "record_in_use", r.content)
        self.assertIn("still used by", r.json()["message"])
        self.assertTrue(type(self.pp).objects.filter(pk=self.pp.pk).exists())
        self.assertFalse(AuditLog.objects.filter(action="delete", object_id=str(self.pp.pk)).exists())  # rolled back with it

    def test_order_lines_go_with_their_order(self):
        owner = self.client_for(self.owner)
        so = owner.post("/api/r/sales.salesorder", {"customer": str(self.customer.pk)}).json()
        line = owner.post("/api/r/sales.soline", {
            "so": so["id"], "product": str(self.product.pk), "scope": "set", "flavour": "Mango", "qty": 1000, "unit_price": "3.10",
            "container_artwork": str(self.arts[("Mango", "container")].pk), "lid_artwork": str(self.arts[("Mango", "lid")].pk)})
        self.assertEqual(line.status_code, 201, line.content)
        r = owner.delete(f"/api/r/sales.salesorder/{so['id']}", {"reason": "Entered by mistake"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(SalesOrder.objects.filter(pk=so["id"]).exists())
        self.assertFalse(SOLine.objects.filter(so_id=so["id"]).exists())

    def test_deleting_a_wrong_opening_entry_takes_it_off_the_stock(self):
        owner = self.client_for(self.owner)
        lot = inv.post_opening_balance(item=self.mb, qty=40, location=self.loc("RM"), user=self.owner)
        move = StockMove.objects.get(lot=lot)
        self.assertFalse(owner.get("/api/r/inventory.stockbalance/meta").json()["can_delete"])
        r = owner.delete(f"/api/r/inventory.stockmove/{move.pk}", {"reason": "Counted twice"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(StockBalance.objects.get(lot=lot).qty, Decimal("0"))
        r = owner.delete(f"/api/r/inventory.lot/{lot.pk}", {"reason": "Counted twice"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Lot.objects.filter(pk=lot.pk).exists())

    def test_movement_whose_stock_was_used_cannot_be_deleted(self):
        rm, fg = self.loc("RM"), self.loc("FG")
        lot = inv.post_opening_balance(item=self.mb, qty=40, location=rm, user=self.owner)
        inv.post_move("transfer", item=self.mb, lot=lot, qty=30, from_location=rm, to_location=fg, user=self.owner)
        opening = StockMove.objects.get(lot=lot, move_type="opening")
        r = self.client_for(self.owner).delete(f"/api/r/inventory.stockmove/{opening.pk}", {"reason": "Counted twice"})
        self.assertEqual(r.json()["error"], "negative_stock", r.content)
        self.assertEqual(StockBalance.objects.get(lot=lot, location=rm).qty, Decimal("10"))

    def test_owner_accounts_and_self_are_protected(self):
        owner = self.client_for(self.owner)
        other_owner = make_user("owner2", ["owner"])
        self.assertEqual(owner.delete(f"/api/r/core.user/{other_owner.pk}", {"reason": "Cleanup"}).json()["error"], "delete_not_allowed")
        self.assertEqual(owner.delete(f"/api/r/core.user/{self.owner.pk}", {"reason": "Cleanup"}).json()["error"], "delete_not_allowed")
        self.assertEqual(owner.delete(f"/api/r/core.userlookup/{other_owner.pk}", {"reason": "Cleanup"}).json()["error"], "delete_not_allowed")
        person = make_user("temp0", ["store_staff"], "STORE")
        self.assertEqual(owner.delete(f"/api/r/core.userlookup/{person.pk}", {"reason": "Created by mistake"}).status_code, 200)
        fresh = make_user("temp1", ["store_staff"], "STORE")
        r = owner.delete(f"/api/r/core.user/{fresh.pk}", {"reason": "Created by mistake"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(User.objects.filter(pk=fresh.pk).exists())

    def test_default_department_can_be_deleted_once_empty(self):
        owner = self.client_for(self.owner)
        hr = Department.objects.get(code="HR")
        r = owner.delete(f"/api/r/core.department/{hr.pk}", {"reason": "No HR team here"})
        self.assertEqual(r.status_code, 200, r.content)
        store = Department.objects.get(code="STORE")  # people still belong to it
        self.assertEqual(owner.delete(f"/api/r/core.department/{store.pk}", {"reason": "Try it"}).json()["error"], "record_in_use")
