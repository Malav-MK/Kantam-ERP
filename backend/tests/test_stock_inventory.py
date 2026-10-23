"""Stock inventory screen and opening stock entry for stock already on the shelf at go-live."""
from decimal import Decimal

from apps.core.models import ApprovalRequest
from apps.inventory.models import StockBalance, StockMove

from .helpers import PlantTestCase


class StockInventoryTests(PlantTestCase):
    def test_owner_posts_opening_stock_directly_and_sees_it(self):
        self.pp.min_stock = Decimal("1000")
        self.pp.save()
        owner = self.client_for(self.owner)
        r = owner.post("/api/r/inventory.stockbalance/actions/opening_stock",
                       {"item": str(self.pp.pk), "location": str(self.loc("RM").pk), "qty": "750", "supplier_lot": "B-12", "reason": "Go-live stock"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(StockBalance.objects.get(item=self.pp, location=self.loc("RM")).qty, Decimal("750"))
        self.assertEqual(StockMove.objects.get(item=self.pp).move_type, "opening")

        data = owner.get("/api/dash/inventory").json()
        row = next(x for x in data["rows"] if x["code"] == "PP")
        self.assertEqual((Decimal(row["on_hand"]), Decimal(row["available"]), row["status"]), (Decimal("750"), Decimal("750"), "low"))
        self.assertEqual(next(x for x in data["rows"] if x["code"] == "MBW")["status"], "none")
        self.assertEqual(data["totals"]["low"], 1)
        self.assertIsNotNone(data["totals"]["value"])

    def test_store_manager_opening_stock_waits_for_approval(self):
        mgr = self.client_for(self.store_mgr)
        r = mgr.post("/api/r/inventory.stockbalance/actions/opening_stock",
                     {"item": str(self.mb.pk), "location": str(self.loc("RM").pk), "qty": "20", "reason": "Physical stock at go-live"})
        self.assertEqual(r.status_code, 202, r.content)
        self.assertFalse(StockBalance.objects.filter(item=self.mb).exists())
        ok = self.client_for(self.owner).post(f"/api/r/core.approvalrequest/{r.json()['approval']}/actions/approve")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ApprovalRequest.objects.get(pk=r.json()["approval"]).status, "approved")
        self.assertEqual(StockBalance.objects.get(item=self.mb).qty, Decimal("20"))
        # Store managers see quantities but not valuation.
        self.assertIsNone(mgr.get("/api/dash/inventory").json()["totals"]["value"])

    def test_opening_stock_rules(self):
        owner = self.client_for(self.owner)
        url = "/api/r/inventory.stockbalance/actions/opening_stock"
        bad_loc = owner.post(url, {"item": str(self.pp.pk), "location": str(self.loc("VA").pk), "qty": "5", "reason": "Wrong place"})
        self.assertEqual(bad_loc.status_code, 409, bad_loc.content)
        zero = owner.post(url, {"item": str(self.pp.pk), "location": str(self.loc("RM").pk), "qty": "0", "reason": "Nothing"})
        self.assertEqual(zero.status_code, 409, zero.content)
        self.assertEqual(self.client_for(self.store).post(url, {"item": str(self.pp.pk), "location": str(self.loc("RM").pk), "qty": "5",
                                                               "reason": "Not allowed"}).status_code, 403)
        self.assertEqual(self.client_for(self.store).get("/api/dash/inventory").status_code, 200)
