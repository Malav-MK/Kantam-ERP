"""
End-to-end connected workflow (gate G3) and exception scenarios (G4):
SO -> demand -> plan -> shift PR -> issue -> production -> QC -> packing -> dispatch.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.core.models import ApprovalRequest, ShiftLock
from apps.core.shifts import shift_for
from apps.fg.models import Dispatch
from apps.inventory import services as inv
from apps.inventory.models import Lot, StockBalance
from apps.planning.models import MonthlyPlan, PlanLine, PlanRevision, ProductionRequest
from apps.planning import services as plan_svc
from apps.sales.models import ComponentDemand, SalesOrder, SOLine, SOVersion

from .helpers import PlantTestCase


class OrderToDispatchTests(PlantTestCase):
    def create_order(self, c):
        so = c.post("/api/r/sales.salesorder", {"customer": str(self.customer.pk), "customer_po_no": "PO-77"}).json()
        for fl in ("Mango", "Kesar", "Vanilla"):
            r = c.post("/api/r/sales.soline", {
                "so": so["id"], "product": str(self.product.pk), "scope": "set", "flavour": fl, "qty": 20000, "unit_price": "3.10",
                "container_artwork": str(self.arts[(fl, "container")].pk), "lid_artwork": str(self.arts[(fl, "lid")].pk)})
            self.assertEqual(r.status_code, 201, r.content)
        r = c.post(f"/api/r/sales.salesorder/{so['id']}/actions/confirm")
        self.assertEqual(r.status_code, 200, r.content)
        return SalesOrder.objects.get(pk=so["id"])

    def stock_materials(self):
        rm, lbl = self.loc("RM"), self.loc("LBL")
        inv.post_opening_balance(item=self.pp, qty=5000, location=rm, user=self.owner)
        inv.post_opening_balance(item=self.mb, qty=500, location=rm, user=self.owner)
        lots = {}
        for (fl, ctype), lab in self.labels.items():
            lots[(fl, ctype)] = inv.post_opening_balance(item=lab, qty=60000, location=lbl, user=self.owner, artwork=self.arts[(fl, ctype)])
        return lots

    def make_pr(self, so, flavour="Mango", component="container"):
        line = so.lines.get(flavour=flavour)
        demand = ComponentDemand.objects.get(so_line=line, component_type=component)
        plan = MonthlyPlan.objects.create(month=f"{timezone.localdate():%Y-%m}")
        pl = self.client_for(self.planner).post("/api/r/planning.planline", {
            "plan": str(plan.pk), "demand": str(demand.pk), "component": str(self.pc_tub.pk if component == "container" else self.pc_lid.pk),
            "machine": str(self.m1.pk), "mould": str((self.mould_tub if component == "container" else self.mould_lid).pk),
            "planned_qty": 2000, "sequence": 10})
        self.assertEqual(pl.status_code, 201, pl.content)
        plan_svc.approve_plan(plan, self.planner)
        slot = shift_for()
        planner = self.client_for(self.planner)
        r = planner.post(f"/api/r/planning.planline/{pl.json()['id']}/actions/generate_pr", {"shift_date": str(slot.shift_date), "shift": slot.shift, "planned_pcs": 2000})
        self.assertEqual(r.status_code, 200, r.content)
        return ProductionRequest.objects.get(pk=r.json()["id"]), PlanLine.objects.get(pk=pl.json()["id"])

    # ------------------------------------------------------------------
    def test_multi_flavour_order_creates_distinct_component_demand(self):
        so = self.create_order(self.client_for(self.sales))
        demands = ComponentDemand.objects.filter(so_line__so=so)
        self.assertEqual(demands.count(), 6)
        self.assertEqual({(d.so_line.flavour, d.component_type) for d in demands},
                         {(f, c) for f in ("Mango", "Kesar", "Vanilla") for c in ("container", "lid")})
        self.assertEqual(set(demands.values_list("required_qty", flat=True)), {20000})

    def test_incompatible_artwork_prevented(self):
        c = self.client_for(self.sales)
        so = c.post("/api/r/sales.salesorder", {"customer": str(self.customer.pk)}).json()
        r = c.post("/api/r/sales.soline", {"so": so["id"], "product": str(self.product.pk), "scope": "set", "flavour": "Mango", "qty": 10,
                                           "container_artwork": str(self.arts[("Kesar", "container")].pk)})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "incompatible_artwork")

    def test_24h_lock_and_amendment_with_approval_keeps_history(self):
        sales = self.client_for(self.sales)
        so = self.create_order(sales)
        line = so.lines.get(flavour="Mango")
        SalesOrder.objects.filter(pk=so.pk).update(locked_at=timezone.now() - timedelta(minutes=1))
        r = sales.patch(f"/api/r/sales.soline/{line.pk}", {"qty": 25000, "row_version": line.row_version})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "record_locked")
        r = sales.post(f"/api/r/sales.soline/{line.pk}/actions/request_amendment", {"qty": 25000, "reason": "Customer increased quantity"})
        self.assertEqual(r.status_code, 200, r.content)
        approval = ApprovalRequest.objects.get(pk=r.json()["approval"])
        self.assertEqual(SOLine.objects.get(pk=line.pk).qty, 20000)  # old version in force until approved
        mgr = self.client_for(self.sales_mgr)
        r = mgr.post(f"/api/r/core.approvalrequest/{approval.pk}/actions/approve", {"note": "ok"})
        self.assertEqual(r.status_code, 200, r.content)
        line.refresh_from_db()
        so.refresh_from_db()
        self.assertEqual(line.qty, 25000)
        self.assertEqual(so.version, 2)
        self.assertEqual(SOVersion.objects.filter(so=so).count(), 2)
        v1 = SOVersion.objects.get(so=so, version=1).snapshot
        self.assertEqual([l["qty"] for l in v1["lines"] if l["flavour"] == "Mango"], [20000])
        self.assertEqual(ComponentDemand.objects.get(so_line=line, component_type="lid").required_qty, 25000)

    def test_maker_cannot_approve_own_amendment(self):
        so = self.create_order(self.client_for(self.sales_mgr))
        SalesOrder.objects.filter(pk=so.pk).update(locked_at=timezone.now() - timedelta(minutes=1))
        line = so.lines.first()
        mgr = self.client_for(self.sales_mgr)
        aid = mgr.post(f"/api/r/sales.soline/{line.pk}/actions/request_amendment", {"qty": 1, "reason": "testing self approval"}).json()["approval"]
        r = mgr.post(f"/api/r/core.approvalrequest/{aid}/actions/approve", {})
        self.assertEqual(r.status_code, 409)

    def test_pr_release_issue_controls_and_wrong_label_blocked(self):
        so = self.create_order(self.client_for(self.sales))
        pr, _ = self.make_pr(so)
        planner = self.client_for(self.planner)
        # Release blocked while materials are short.
        r = planner.post(f"/api/r/planning.productionrequest/{pr.pk}/actions/release")
        self.assertEqual(r.status_code, 409)
        self.assertIn("Material shortage", r.json()["message"])
        lots = self.stock_materials()
        r = planner.post(f"/api/r/planning.productionrequest/{pr.pk}/actions/release")
        self.assertEqual(r.status_code, 200, r.content)
        pr.refresh_from_db()
        self.assertEqual(pr.snapshot["artwork"]["flavour"], "Mango")
        label_need = pr.materials.get(kind="label")
        self.assertEqual(label_need.required_qty, 2000)  # IML gross pcs x 1 label
        store = self.client_for(self.store)
        wrong = store.post("/api/r/inventory.stockmove/actions/issue", {
            "pr": str(pr.pk), "item": str(self.labels[("Mango", "container")].pk), "lot": str(lots[("Kesar", "container")].pk),
            "location": str(self.loc("LBL").pk), "qty": 100})
        self.assertEqual(wrong.status_code, 409)
        self.assertIn(wrong.json()["error"], {"wrong_artwork", "lot_mismatch"})
        ok = store.post("/api/r/inventory.stockmove/actions/issue", {
            "pr": str(pr.pk), "item": str(self.labels[("Mango", "container")].pk), "lot": str(lots[("Mango", "container")].pk),
            "location": str(self.loc("LBL").pk), "qty": 2000}, idem="issue-1")
        self.assertEqual(ok.status_code, 200, ok.content)
        replay = store.post("/api/r/inventory.stockmove/actions/issue", {
            "pr": str(pr.pk), "item": str(self.labels[("Mango", "container")].pk), "lot": str(lots[("Mango", "container")].pk),
            "location": str(self.loc("LBL").pk), "qty": 2000}, idem="issue-1")
        self.assertEqual(replay["Idempotent-Replay"], "true")
        self.assertEqual(pr.materials.get(kind="label").issued_qty, 2000)  # not doubled
        extra = store.post("/api/r/inventory.stockmove/actions/issue", {
            "pr": str(pr.pk), "item": str(self.labels[("Mango", "container")].pk), "lot": str(lots[("Mango", "container")].pk),
            "location": str(self.loc("LBL").pk), "qty": 50})
        self.assertEqual(extra.json()["error"], "additional_issue_requires_approval")

    def test_cancelled_pr_returns_against_old_pr_and_reissues_new(self):
        so = self.create_order(self.client_for(self.sales))
        pr, _ = self.make_pr(so)
        self.stock_materials()
        plan_svc.release_pr(pr, self.planner)
        pp_lot = Lot.objects.filter(item=self.pp).first()
        inv.issue_to_pr(pr=pr, item=self.pp, lot=pp_lot, qty=Decimal("50"), from_location=self.loc("RM"), user=self.store)
        planner = self.client_for(self.planner)
        r = planner.post(f"/api/r/planning.productionrequest/{pr.pk}/actions/cancel", {"reason": "Customer artwork hold", "reissue": True})
        self.assertEqual(r.status_code, 200, r.content)
        new_pr = ProductionRequest.objects.get(pk=r.json()["id"])
        self.assertEqual(new_pr.parent_pr_id, pr.pk)
        self.assertEqual(new_pr.so_line_id, pr.so_line_id)
        r = self.client_for(self.store).post("/api/r/inventory.stockmove/actions/return", {
            "pr": str(pr.pk), "item": str(self.pp.pk), "lot": str(pp_lot.pk), "location": str(self.loc("RM").pk), "qty": 50, "reason": "PR cancelled"})
        self.assertEqual(r.status_code, 200, r.content)
        mat = pr.materials.get(item=self.pp)
        self.assertEqual((mat.issued_qty, mat.returned_qty), (Decimal("50"), Decimal("50")))

    def test_production_report_reconciles_and_cycle_reforecasts_keeping_baseline(self):
        so = self.create_order(self.client_for(self.sales))
        pr, line = self.make_pr(so)
        self.stock_materials()
        plan_svc.release_pr(pr, self.planner)
        base_finish = PlanLine.objects.get(pk=line.pk).baseline_finish
        sup = self.client_for(self.supervisor)
        slot = shift_for()
        start = slot.start
        end = min(timezone.now(), slot.end) - timedelta(seconds=1)
        rep = sup.post("/api/r/production.productionreport", {"pr": str(pr.pk), "actual_start": start.isoformat(), "actual_end": end.isoformat(),
                                                               "actual_cycle_s": "10", "total_moulded": 1000, "good_qty": 900, "held_qty": 50, "rejected_qty": 40})
        self.assertEqual(rep.status_code, 201, rep.content)
        bad = sup.post(f"/api/r/production.productionreport/{rep.json()['id']}/actions/submit")
        self.assertEqual(bad.json()["error"], "output_not_reconciled")
        fix = sup.patch(f"/api/r/production.productionreport/{rep.json()['id']}", {"rejected_qty": 50, "row_version": rep.json()["row_version"]})
        self.assertEqual(fix.status_code, 200, fix.content)
        ok = sup.post(f"/api/r/production.productionreport/{rep.json()['id']}/actions/submit")
        self.assertEqual(ok.status_code, 200, ok.content)
        good_lot = Lot.objects.get(pr=pr, quality_state="pending")
        held_lot = Lot.objects.get(pr=pr, quality_state="held")
        self.assertEqual((inv.lot_on_hand(good_lot), inv.lot_on_hand(held_lot)), (900, 50))
        line.refresh_from_db()
        self.assertEqual(line.cycle_s, Decimal("10.00"))
        self.assertEqual(line.baseline_cycle_s, Decimal("8.00"))
        self.assertEqual(line.baseline_finish, base_finish)
        self.assertTrue(PlanRevision.objects.filter(plan_line=line, kind="cycle").exists())

    def test_closed_shift_blocks_normal_entries(self):
        so = self.create_order(self.client_for(self.sales))
        pr, _ = self.make_pr(so)
        self.stock_materials()
        plan_svc.release_pr(pr, self.planner)
        ShiftLock.objects.create(shift_date=pr.shift_date, shift=pr.shift)
        r = self.client_for(self.supervisor).post("/api/r/production.productionreport", {"pr": str(pr.pk), "actual_cycle_s": "8", "total_moulded": 10, "good_qty": 10})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"], "shift_closed")


class MatchingPackingDispatchTests(PlantTestCase):
    def comp_lot(self, item, ctype, qty, flavour="Mango", state="released"):
        lot = inv.post_opening_balance(item=item, qty=qty, location=self.loc("FG"), user=self.owner, quality_state=state,
                                       product=self.product, component_type=ctype, flavour=flavour)
        return lot

    def test_10000_containers_8000_lids_pack_5000_no_double_count(self):
        """Acceptance p.15."""
        self.comp_lot(self.tub, "container", 10000)
        self.comp_lot(self.lid, "lid", 8000)
        from apps.fg.services import matching_summary
        self.assertEqual(matching_summary(self.product)[0]["potential_sets"], 8000)
        c = self.client_for(self.dispatcher)
        r = c.post("/api/r/fg.packingrecord/actions/pack", {"product": str(self.product.pk), "flavour": "Mango", "sets": 5000, "location": str(self.loc("FG").pk)})
        self.assertEqual(r.status_code, 200, r.content)

        def on_hand(item):
            return sum(b.qty for b in StockBalance.objects.filter(item=item, location__kind="fg"))
        self.assertEqual((on_hand(self.tub), on_hand(self.lid), on_hand(self.set_item)), (5000, 3000, 5000))
        self.assertEqual(matching_summary(self.product)[0]["potential_sets"], 3000)

    def test_excess_lids_do_not_cover_container_shortage_or_other_flavour(self):
        self.comp_lot(self.tub, "container", 100, flavour="Mango")
        self.comp_lot(self.lid, "lid", 1000, flavour="Kesar")
        r = self.client_for(self.dispatcher).post("/api/r/fg.packingrecord/actions/pack", {"product": str(self.product.pk), "flavour": "Mango", "sets": 50, "location": str(self.loc("FG").pk)})
        self.assertEqual(r.json()["error"], "insufficient_components")

    def _order_with_packed(self, state="released"):
        so = SalesOrder.objects.create(customer=self.customer, salesperson=self.sales, status="confirmed")
        line = SOLine.objects.create(so=so, line_no=1, product=self.product, flavour="Mango", qty=1000, unit_price=1)
        lot = inv.post_opening_balance(item=self.set_item, qty=1000, location=self.loc("FG"), user=self.owner, quality_state=state, product=self.product, flavour="Mango")
        return so, line, lot

    def _dispatch(self, c, so, line, lot, qty):
        d = c.post("/api/r/fg.dispatch", {"so": str(so.pk), "vehicle_no": "GJ01AB1234"}).json()
        self.assertEqual(c.post("/api/r/fg.dispatchline", {"dispatch": d["id"], "so_line": str(line.pk), "lot": str(lot.pk), "qty": qty}).status_code, 201)
        return d

    def test_qc_hold_prevents_shipment(self):
        so, line, lot = self._order_with_packed(state="held")
        c = self.client_for(self.dispatcher)
        d = self._dispatch(c, so, line, lot, 500)
        c.post(f"/api/r/fg.dispatch/{d['id']}/actions/pick")
        r = c.post(f"/api/r/fg.dispatch/{d['id']}/actions/verify")
        self.assertEqual(r.status_code, 409)
        self.assertIn("QC hold prevents shipment", r.json()["message"])

    def test_dispatch_posts_once_and_updates_so_balance(self):
        so, line, lot = self._order_with_packed()
        c = self.client_for(self.dispatcher)
        d = self._dispatch(c, so, line, lot, 600)
        for step in ("pick", "verify", "approve"):
            self.assertEqual(c.post(f"/api/r/fg.dispatch/{d['id']}/actions/{step}").status_code, 200)
        r1 = c.post(f"/api/r/fg.dispatch/{d['id']}/actions/post", idem="dispatch-post-1")
        self.assertEqual(r1.status_code, 200, r1.content)
        r2 = c.post(f"/api/r/fg.dispatch/{d['id']}/actions/post", idem="dispatch-post-1")  # network retry
        self.assertEqual(r2["Idempotent-Replay"], "true")
        r3 = c.post(f"/api/r/fg.dispatch/{d['id']}/actions/post")  # second tap without key
        self.assertEqual(r3.status_code, 409)
        line.refresh_from_db()
        self.assertEqual((line.dispatched_qty, line.balance_qty), (600, 400))
        self.assertEqual(inv.lot_on_hand(lot), 400)

    def test_commercial_gate_blocks_until_advance_or_credit_release(self):
        so, line, lot = self._order_with_packed()
        SalesOrder.objects.filter(pk=so.pk).update(advance_required_pct=50)
        c = self.client_for(self.dispatcher)
        d = self._dispatch(c, so, line, lot, 100)
        c.post(f"/api/r/fg.dispatch/{d['id']}/actions/pick")
        self.assertIn("Advance", c.post(f"/api/r/fg.dispatch/{d['id']}/actions/verify").json()["message"])
        self.assertEqual(self.client_for(self.accounts).post(f"/api/r/sales.salesorder/{so.pk}/actions/credit_release", {"reason": "Approved credit customer"}).status_code, 200)
        self.assertEqual(c.post(f"/api/r/fg.dispatch/{d['id']}/actions/verify").status_code, 200)

    def test_two_users_cannot_allocate_same_stock(self):
        lot = self.comp_lot(self.tub, "container", 100)
        inv.reserve(item=self.tub, qty=80, user=self.sales, lot=lot)
        from apps.core.api import BusinessRuleError
        with self.assertRaises(BusinessRuleError):
            inv.reserve(item=self.tub, qty=30, user=self.sales2, lot=lot)
        self.assertEqual(inv.lot_free_qty(lot), 20)

    def test_negative_stock_blocked(self):
        lot = self.comp_lot(self.tub, "container", 10)
        from apps.core.api import BusinessRuleError
        with self.assertRaises(BusinessRuleError):
            inv.post_move("transfer", item=self.tub, lot=lot, qty=11, from_location=self.loc("FG"), to_location=self.loc("DSP"), user=self.owner)

    def test_fractional_pieces_rejected(self):
        lot = self.comp_lot(self.tub, "container", 10)
        from apps.core.api import BusinessRuleError
        with self.assertRaises(BusinessRuleError):
            inv.post_move("transfer", item=self.tub, lot=lot, qty=Decimal("1.5"), from_location=self.loc("FG"), to_location=self.loc("DSP"), user=self.owner)

    def test_complaint_traces_back_to_component_lots(self):
        tub_lot = self.comp_lot(self.tub, "container", 100)
        self.comp_lot(self.lid, "lid", 100)
        from apps.fg.services import pack_sets
        from apps.inventory.trace import trace_lot
        rec = pack_sets(product=self.product, sets=100, location=self.loc("FG"), user=self.owner, flavour="Mango")

        class R:
            user = self.owner
        tree = trace_lot(R, rec.packed_lot)["backward"]
        self.assertIn(tub_lot.doc_no, [p["lot"] for p in tree["made_from"]])
        fwd = trace_lot(R, tub_lot)["forward"]
        self.assertEqual(fwd["used_in"][0]["lot"], rec.packed_lot.doc_no)
