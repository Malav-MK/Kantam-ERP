"""Calculation dictionary (brief p.11) and worked example (p.67)."""
import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from apps.core import calc
from apps.core.shifts import ShiftSlot, shift_for

IST = ZoneInfo("Asia/Kolkata")


class CalcTests(SimpleTestCase):
    def test_cycle_8_to_10_reduces_capacity_20pct_and_adds_25pct_hours(self):
        """Acceptance p.9: 8s -> 10s, same cavities/time."""
        r8 = calc.theoretical_rate(2, 8)
        r10 = calc.theoretical_rate(2, 10)
        self.assertEqual((r8 - r10) / r8, Decimal("0.2"))
        h8 = calc.run_hours(16000, 2, 8)
        h10 = calc.run_hours(16000, 2, 10)
        self.assertEqual(h8, Decimal("17.78"))
        self.assertEqual((h10 / Decimal("17.7777777778")).quantize(Decimal("0.01")), Decimal("1.25"))

    def test_worked_example_p67(self):
        self.assertEqual(calc.theoretical_rate(2, 8), 900)
        self.assertEqual(calc.material_mass_kg(16000, Decimal("38.5")), Decimal("616.000"))
        split = dict(calc.recipe_split(616, [("resin", 95), ("mb", 5)], "total_blend"))
        self.assertEqual(split["resin"], Decimal("585.200"))
        self.assertEqual(split["mb"], Decimal("30.800"))  # 5% not added again on top
        self.assertEqual(calc.matchable_sets({"container": 19500, "lid": 20000}), 19500)

    def test_recipe_must_sum_to_100(self):
        with self.assertRaises(calc.CalcError):
            calc.recipe_split(100, [("a", 95), ("b", 4)], "total_blend")

    def test_base_resin_basis(self):
        split = dict(calc.recipe_split(105, [("resin", 100), ("mb", 5)], "base_resin"))
        self.assertEqual(split["resin"], Decimal("100.000"))
        self.assertEqual(split["mb"], Decimal("5.000"))

    def test_gross_target_applies_yield_once(self):
        self.assertEqual(calc.gross_target(9800, 98), 10000)
        self.assertEqual(calc.gross_target(1000), 1000)

    def test_labels_non_iml_zero_and_allowance_separate(self):
        self.assertEqual(calc.label_requirement(1000, False)["total"], 0)
        lab = calc.label_requirement(1000, True, 1, 2)
        self.assertEqual((lab["base"], lab["allowance"], lab["total"]), (1000, 20, 1020))

    def test_packing_and_zero_denominator(self):
        self.assertEqual(calc.packing_requirement(1010, 25, 500), {"bags": 41, "cartons": 3, "partial_carton_pcs": 10})
        with self.assertRaises(calc.CalcError):
            calc.theoretical_rate(2, 0)

    def test_reconcile_exclusive_buckets(self):
        self.assertTrue(calc.reconcile_output(16000, 15500, 500, 0))
        self.assertFalse(calc.reconcile_output(16000, 15500, 400, 0))


class ShiftTests(SimpleTestCase):
    def test_night_shift_spans_midnight_with_start_date(self):
        two_am = datetime.datetime(2026, 10, 15, 2, 0, tzinfo=IST)
        self.assertEqual(shift_for(two_am), ShiftSlot(datetime.date(2026, 10, 14), "night"))
        self.assertEqual(shift_for(datetime.datetime(2026, 10, 15, 9, 0, tzinfo=IST)).shift, "day")
        self.assertEqual(shift_for(datetime.datetime(2026, 10, 15, 20, 59, tzinfo=IST)).shift, "day")
        self.assertEqual(shift_for(datetime.datetime(2026, 10, 15, 21, 0, tzinfo=IST)), ShiftSlot(datetime.date(2026, 10, 15), "night"))

    def test_slot_boundaries(self):
        n = ShiftSlot(datetime.date(2026, 10, 14), "night")
        self.assertEqual(n.start.hour, 21)
        self.assertEqual(n.end, datetime.datetime(2026, 10, 15, 9, 0, tzinfo=IST))
        self.assertEqual(n.next(), ShiftSlot(datetime.date(2026, 10, 15), "day"))
