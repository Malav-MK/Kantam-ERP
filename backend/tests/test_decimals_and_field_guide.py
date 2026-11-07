"""Decimals in entered numbers, several component types at once, and the "?" reference on every input."""
from datetime import timedelta
from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core import calc
from apps.core.field_guide import GUIDE, field_guide
from apps.core.fields import QtyField, parse_qty
from apps.core.api import BusinessRuleError
from apps.core.registry import REGISTRY
from apps.core.models import ApprovalRequest
from apps.core.shifts import shift_for
from apps.crm.models import Quotation, QuotationLine
from apps.inventory import services as inv
from apps.inventory.models import Lot
from apps.masters.models import Item, PackingRule, Product, ProductComponent
from apps.planning.models import MonthlyPlan, PlanLine, ProductionRequest
from apps.planning import services as plan_svc
from apps.sales.models import ComponentDemand, SalesOrder, SOLine

from . import test_workflows as wf
from .helpers import PlantTestCase


def _inputs():
    for key, res in REGISTRY.items():
        for name in dict.fromkeys(list(res.create_only_fields) + list(res.form_fields)):
            yield key, name, f"{key}.{name}"
        for action in res.actions:
            for f in action.fields:
                yield key, f["name"], f"{key}!{action.name}.{f['name']}"


class FieldGuideTests(PlantTestCase):
    def test_every_form_and_action_input_has_a_reference(self):
        missing = [where for key, name, where in _inputs() if not field_guide(key, name)]
        self.assertEqual(missing, [])

    def test_meta_sends_the_reference_for_fields_and_actions(self):
        owner = self.client_for(self.owner)
        meta = owner.get("/api/r/masters.packingrule/meta").json()
        self.assertIn("0.2", meta["fields"]["cartons_per_tape_roll"]["help"])
        self.assertTrue(meta["fields"]["cartons_per_tape_roll"]["help"].endswith("Decimals are allowed."))
        action = owner.get("/api/r/fg.packingrecord/meta").json()["actions"]
        self.assertTrue(all(f["help"] for a in action for f in a["fields"]))

    def test_guide_has_no_blank_entries(self):
        self.assertEqual([k for k, v in GUIDE.items() if not v.strip()], [])


class DecimalEntryTests(PlantTestCase):
    def setUp(self):
        self.owner_api = self.client_for(self.owner)

    def _rule(self, **kw):
        body = {"product": str(self.product.pk), "revision": 9, "pcs_per_bag": 50, "pcs_per_carton": 500,
                "cartons_per_tape_roll": "0.2", "effective_from": "2026-11-01", **kw}
        return self.owner_api.post("/api/r/masters.packingrule", body)

    def test_reported_error_is_fixed_for_cartons_per_tape_roll(self):
        r = self._rule()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["cartons_per_tape_roll"], 0.2)
        rule = PackingRule.objects.get(pk=r.json()["id"])
        self.assertEqual(rule.cartons_per_tape_roll, Decimal("0.2"))
        self.assertEqual(calc.tape_rolls(4, rule.cartons_per_tape_roll), Decimal("20.00"))

    def test_whole_numbers_still_come_back_as_plain_ints(self):
        r = self._rule(cartons_per_tape_roll=40)
        self.assertIs(type(r.json()["cartons_per_tape_roll"]), int)
        self.assertIs(type(PackingRule.objects.get(pk=r.json()["id"]).cartons_per_tape_roll), int)

    def test_negative_and_over_precise_numbers_are_refused(self):
        self.assertEqual(self._rule(cartons_per_tape_roll="-1").status_code, 400)
        self.assertEqual(self._rule(cartons_per_tape_roll="0.1234").status_code, 400)
        for bad in ("-1", "abc", "NaN", "Infinity", "0.1234"):
            with self.assertRaises(BusinessRuleError, msg=bad):
                parse_qty(bad)
        self.assertEqual(parse_qty("2.50"), Decimal("2.5"))
        self.assertIs(type(parse_qty("3.000")), int)

    def test_every_number_a_person_types_is_a_decimal_column(self):
        whole = []
        for key, res in REGISTRY.items():
            for name in dict.fromkeys(list(res.create_only_fields) + list(res.form_fields)):
                try:
                    f = res.model._meta.get_field(name)
                except Exception:
                    continue
                if isinstance(f, models.IntegerField):
                    whole.append(f"{key}.{name}")
        self.assertEqual(whole, [])

    def test_fractional_values_survive_calculations(self):
        self.assertEqual(calc.gross_target(Decimal("0.5")), 1)
        self.assertEqual(calc.label_requirement(3, True, Decimal("0.5"))["base"], 2)
        self.assertTrue(calc.reconcile_output(Decimal("10.5"), Decimal("10"), Decimal("0.5"), 0))
        self.assertFalse(calc.reconcile_output(Decimal("10.5"), 10, 0, 0))
        pack = calc.packing_requirement(Decimal("7.5"), Decimal("2.5"), 5)
        self.assertEqual((pack["bags"], pack["cartons"], pack["partial_carton_pcs"]), (3, 2, Decimal("2.5")))

    def test_whole_unit_items_still_refuse_fractions_in_stock(self):
        self.assertTrue(self.tub.whole_units)
        with self.assertRaises(BusinessRuleError) as e:
            inv.post_opening_balance(item=self.tub, qty=Decimal("10.5"), location=self.loc("FG"), user=self.owner, product=self.product, component_type="container")
        self.assertEqual(e.exception.detail["error"], "whole_units")

    def test_qty_columns_store_fractions_and_filter_correctly(self):
        self.assertIsInstance(ProductComponent._meta.get_field("qty_per_set"), QtyField)
        ProductComponent.objects.filter(pk=self.pc_lid.pk).update(qty_per_set=Decimal("1.5"))
        self.pc_lid.refresh_from_db()
        self.assertEqual(self.pc_lid.qty_per_set, Decimal("1.5"))
        self.assertTrue(ProductComponent.objects.filter(qty_per_set__gt=1).exists())


class MultipleComponentTypeTests(PlantTestCase):
    def setUp(self):
        self.api = self.client_for(self.owner)
        self.new = Product.objects.create(code="R750", name="Round 750", created_by=self.owner)

    def _create(self, types):
        return self.api.post("/api/r/masters.productcomponent", {
            "product": str(self.new.pk), "component_type": types, "item": str(self.tub.pk), "weight_g": "22.5",
        })

    def test_one_record_per_selected_type(self):
        r = self._create(["container", "lid"])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["_created_count"], 2)
        rows = ProductComponent.objects.filter(product=self.new)
        self.assertEqual(sorted(rows.values_list("component_type", flat=True)), ["container", "lid"])
        self.assertTrue(all(row.weight_g == Decimal("22.5") for row in rows))

    def test_a_single_value_still_works(self):
        r = self._create("lid")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["_created_count"], 1)

    def test_a_repeated_type_saves_nothing(self):
        self.assertEqual(self._create(["lid"]).status_code, 201)
        r = self._create(["container", "lid"])
        self.assertGreaterEqual(r.status_code, 400)
        self.assertEqual(list(ProductComponent.objects.filter(product=self.new).values_list("component_type", flat=True)), ["lid"])

    def test_empty_selection_is_refused(self):
        self.assertGreaterEqual(self._create([]).status_code, 400)
        self.assertFalse(ProductComponent.objects.filter(product=self.new).exists())

    def test_meta_flags_the_field_for_the_create_form(self):
        meta = self.api.get("/api/r/masters.productcomponent/meta").json()
        self.assertTrue(meta["fields"]["component_type"]["multi_on_create"])
        self.assertNotIn("multi_on_create", meta["fields"]["item"])


class FractionalWorkflowTests(PlantTestCase):
    """Real fractions through order, plan, shift PR, production, packing and dispatch."""

    stock_materials = wf.OrderToDispatchTests.stock_materials

    def setUp(self):
        Item.objects.update(whole_units=False)
        for item in (self.tub, self.lid, self.set_item, self.pp, self.mb, *self.labels.values()):
            item.refresh_from_db()

    def _order(self, qty="20000.5"):
        c = self.client_for(self.sales)
        so = c.post("/api/r/sales.salesorder", {"customer": str(self.customer.pk), "customer_po_no": "PO-77"}).json()
        r = c.post("/api/r/sales.soline", {
            "so": so["id"], "product": str(self.product.pk), "scope": "set", "flavour": "Mango", "qty": qty, "unit_price": "3.10",
            "container_artwork": str(self.arts[("Mango", "container")].pk), "lid_artwork": str(self.arts[("Mango", "lid")].pk)})
        self.assertEqual(r.status_code, 201, r.content)
        r = c.post(f"/api/r/sales.salesorder/{so['id']}/actions/confirm")
        self.assertEqual(r.status_code, 200, r.content)
        return SOLine.objects.get(so_id=so["id"])

    def _plan_and_pr(self, line):
        demand = ComponentDemand.objects.get(so_line=line, component_type="container")
        plan = MonthlyPlan.objects.create(month=f"{timezone.localdate():%Y-%m}")
        planner = self.client_for(self.planner)
        pl = planner.post("/api/r/planning.planline", {
            "plan": str(plan.pk), "demand": str(demand.pk), "component": str(self.pc_tub.pk), "machine": str(self.m1.pk), "mould": str(self.mould_tub.pk),
            "planned_qty": "2000.5", "sequence": "10.5", "setup_minutes": "12.5"})
        self.assertEqual(pl.status_code, 201, pl.content)
        self.assertEqual(pl.json()["planned_qty"], 2000.5)
        plan_svc.approve_plan(plan, self.planner)
        plan.refresh_from_db()
        self.assertEqual(plan.baseline["lines"][0]["qty"], "2000.5")
        r = planner.post(f"/api/r/planning.planline/{pl.json()['id']}/actions/revise", {
            "planned_qty": "2100.25", "active_cavities": "1.5", "kind": "other", "reason": "Fractional revise check"})
        self.assertEqual(r.status_code, 200, r.content)
        slot = shift_for()
        r = planner.post(f"/api/r/planning.planline/{pl.json()['id']}/actions/generate_pr", {"shift_date": str(slot.shift_date), "shift": slot.shift, "planned_pcs": "1500.5"})
        self.assertEqual(r.status_code, 200, r.content)
        return ProductionRequest.objects.get(pk=r.json()["id"]), PlanLine.objects.get(pk=pl.json()["id"])

    def test_order_plan_pr_report_correct_and_dashboards(self):
        line = self._order()
        self.assertEqual(line.qty, Decimal("20000.5"))
        pr, plan_line = self._plan_and_pr(line)
        self.assertEqual(pr.planned_pcs, Decimal("1500.5"))
        self.assertEqual(plan_line.active_cavities, Decimal("1.5"))
        self.stock_materials()
        planner = self.client_for(self.planner)
        r = planner.post(f"/api/r/planning.productionrequest/{pr.pk}/actions/release")
        self.assertEqual(r.status_code, 200, r.content)
        pr.refresh_from_db()
        self.assertEqual(pr.snapshot["cavities"], "1.5")

        sup = self.client_for(self.supervisor)
        slot = shift_for()
        end = min(timezone.now(), slot.end) - timedelta(seconds=1)
        rep = sup.post("/api/r/production.productionreport", {
            "pr": str(pr.pk), "actual_start": slot.start.isoformat(), "actual_end": end.isoformat(), "actual_cycle_s": "10",
            "total_moulded": "1000.5", "good_qty": "900.25", "held_qty": "50.25", "rejected_qty": "50", "startup_loss_pcs": "2.5"})
        self.assertEqual(rep.status_code, 201, rep.content)
        ok = sup.post(f"/api/r/production.productionreport/{rep.json()['id']}/actions/submit")
        self.assertEqual(ok.status_code, 200, ok.content)
        good = Lot.objects.get(pr=pr, quality_state="pending")
        self.assertEqual(inv.lot_on_hand(good), Decimal("900.25"))

        for path in ("/api/dash/owner", "/api/dash/planning"):
            r = self.client_for(self.owner).get(path)
            self.assertEqual(r.status_code, 200, f"{path}: {r.content[:200]}")
        self.assertEqual(self.client_for(self.owner).get(f"/api/r/planning.planline/{plan_line.pk}").status_code, 200)
        self.assertEqual(self.client_for(self.owner).get(f"/api/r/sales.soline/{line.pk}").status_code, 200)

    def test_pack_and_dispatch_fractions(self):
        rm = self.loc("FG")
        for item, ctype in ((self.tub, "container"), (self.lid, "lid")):
            inv.post_opening_balance(item=item, qty=Decimal("100.5"), location=rm, user=self.owner, quality_state="released",
                                     product=self.product, component_type=ctype, flavour="Mango")
        c = self.client_for(self.dispatcher)
        r = c.post("/api/r/fg.packingrecord/actions/pack", {"product": str(self.product.pk), "flavour": "Mango", "sets": "2.5", "location": str(rm.pk)})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["sets_packed"], 2.5)
        self.assertEqual(inv.lot_on_hand(Lot.objects.get(item=self.tub)), Decimal("98"))

        so = SalesOrder.objects.create(customer=self.customer, salesperson=self.sales, status="confirmed")
        line = SOLine.objects.create(so=so, line_no=1, product=self.product, flavour="Mango", qty=Decimal("10.5"), unit_price=1)
        lot = inv.post_opening_balance(item=self.set_item, qty=Decimal("10.5"), location=rm, user=self.owner, quality_state="released", product=self.product, flavour="Mango")
        d = c.post("/api/r/fg.dispatch", {"so": str(so.pk), "vehicle_no": "GJ01AB1234"}).json()
        r = c.post("/api/r/fg.dispatchline", {"dispatch": d["id"], "so_line": str(line.pk), "lot": str(lot.pk), "qty": "1.5", "cartons": "0.5"})
        self.assertEqual(r.status_code, 201, r.content)
        for step in ("pick", "verify", "approve", "post"):
            r = c.post(f"/api/r/fg.dispatch/{d['id']}/actions/{step}")
            self.assertEqual(r.status_code, 200, f"{step}: {r.content[:300]}")
        line.refresh_from_db()
        self.assertEqual((line.dispatched_qty, line.balance_qty), (Decimal("1.5"), 9))
        r = self.client_for(self.owner).post(f"/api/r/fg.dispatch/{d['id']}/actions/exception", {"kind": "short", "qty": "0.5", "action": "none", "description": "Half carton short"})
        self.assertEqual(r.status_code, 200, r.content)

    def test_report_correction_with_fractions(self):
        line = self._order()
        pr, _ = self._plan_and_pr(line)
        self.stock_materials()
        plan_svc.release_pr(pr, self.planner)
        sup = self.client_for(self.supervisor)
        slot = shift_for()
        end = min(timezone.now(), slot.end) - timedelta(seconds=1)
        rep = sup.post("/api/r/production.productionreport", {
            "pr": str(pr.pk), "actual_start": slot.start.isoformat(), "actual_end": end.isoformat(), "actual_cycle_s": "8",
            "total_moulded": "100.5", "good_qty": "90.5", "held_qty": "5", "rejected_qty": "5"}).json()
        self.assertEqual(sup.post(f"/api/r/production.productionreport/{rep['id']}/actions/submit").status_code, 200)
        r = self.client_for(self.owner).post(f"/api/r/production.productionreport/{rep['id']}/actions/correct", {
            "total_moulded": "100.5", "good_qty": "90.25", "held_qty": "5.25", "rejected_qty": "5", "reason": "Recount at shift end"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(inv.lot_on_hand(Lot.objects.get(pr=pr, quality_state="pending")), Decimal("90.25"))

    def test_amendment_to_a_fractional_quantity(self):
        line = self._order("100")
        c = self.client_for(self.sales)
        r = c.post(f"/api/r/sales.soline/{line.pk}/actions/request_amendment", {"qty": "150.5", "reason": "Customer increased quantity"})
        self.assertEqual(r.status_code, 200, r.content)
        approval = ApprovalRequest.objects.get(pk=r.json()["approval"])
        r = self.client_for(self.owner).post(f"/api/r/core.approvalrequest/{approval.pk}/actions/approve", {"note": "OK"})
        self.assertEqual(r.status_code, 200, r.content)
        line.refresh_from_db()
        self.assertEqual(line.qty, Decimal("150.5"))
        self.assertEqual(ComponentDemand.objects.get(so_line=line, component_type="lid").required_qty, Decimal("150.5"))

    def test_quotation_snapshot_accepts_fractions(self):
        quote = Quotation.objects.create(customer=self.customer, delivery_lead_days=Decimal("10.5"), valid_until=timezone.localdate(), created_by=self.owner)
        QuotationLine.objects.create(quotation=quote, product=self.product, qty=Decimal("1000.5"), moq=Decimal("0.5"), unit_price=Decimal("3.1"))
        send = next(a for a in REGISTRY["crm.quotation"].actions if a.name == "send")
        send.handler(None, quote, {})
        quote.refresh_from_db()
        self.assertEqual(quote.snapshot["lines"][0]["qty"], "1000.5")
        self.assertEqual(quote.snapshot["header"]["delivery_lead_days"], "10.5")
