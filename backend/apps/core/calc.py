"""
Central calculation dictionary (brief p.11).

Every planning, stores and packing screen calls these functions so formulas
exist in exactly one place. Each returns its inputs alongside the result so the
UI can show the calculation basis ("clicking a figure must reveal the basis").
"""
import math
from decimal import ROUND_HALF_UP, Decimal


class CalcError(ValueError):
    pass


def D(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _positive(name, value):
    if value is None or D(value) <= 0:
        raise CalcError(f"{name} must be greater than zero")
    return D(value)


def theoretical_rate(active_cavities, cycle_seconds):
    """pcs/hour = active cavities x 3600 / cycle seconds."""
    cav = _positive("active cavities", active_cavities)
    cyc = _positive("cycle seconds", cycle_seconds)
    return cav * 3600 / cyc


def planned_moulded_pieces(run_hours, active_cavities, cycle_seconds):
    """floor(run hours x theoretical rate)."""
    rate = theoretical_rate(active_cavities, cycle_seconds)
    return int(math.floor(D(run_hours) * rate))


def gross_target(required_good, expected_yield_pct=None):
    """ceil(required good / yield) - the loss is applied once only."""
    if not expected_yield_pct:
        return int(D(required_good))
    y = _positive("yield %", expected_yield_pct) / 100
    if y > 1:
        raise CalcError("yield % cannot exceed 100")
    return int(math.ceil(D(required_good) / y))


def run_hours(gross_pieces, active_cavities, cycle_seconds):
    """gross pcs x cycle / active cavities / 3600."""
    cav = _positive("active cavities", active_cavities)
    cyc = _positive("cycle seconds", cycle_seconds)
    return (D(gross_pieces) * cyc / cav / 3600).quantize(Decimal("0.01"), ROUND_HALF_UP)


def material_mass_kg(gross_pieces, shot_grams_per_piece, allowance_pct=0):
    """gross pcs x grams / 1000, plus runner/startup/purge allowance applied once."""
    base = D(gross_pieces) * D(shot_grams_per_piece) / 1000
    total = base * (1 + D(allowance_pct or 0) / 100)
    return total.quantize(Decimal("0.001"), ROUND_HALF_UP)


def recipe_split(total_kg, lines, basis="total_blend"):
    """
    lines: iterable of (item_id, percent).
    total_blend: fractions sum to 100%; each kg = total x fraction.
    base_resin: additives are % of base resin; base resin = total / (1 + sum(additive%)).
    The first line is treated as base resin for base_resin basis.
    """
    lines = [(item, D(pct)) for item, pct in lines]
    if not lines:
        return []
    total = D(total_kg)
    if basis == "total_blend":
        s = sum(p for _, p in lines)
        if s != 100:
            raise CalcError(f"total-blend recipe must sum to 100% (got {s}%)")
        return [(item, (total * p / 100).quantize(Decimal("0.001"), ROUND_HALF_UP)) for item, p in lines]
    if basis == "base_resin":
        base_item, base_pct = lines[0]
        if base_pct != 100:
            raise CalcError("base-resin recipe: first line must be the base resin at 100%")
        additive = sum(p for _, p in lines[1:])
        base_kg = total / (1 + additive / 100)
        out = [(base_item, base_kg.quantize(Decimal("0.001"), ROUND_HALF_UP))]
        out += [(item, (base_kg * p / 100).quantize(Decimal("0.001"), ROUND_HALF_UP)) for item, p in lines[1:]]
        return out
    raise CalcError(f"unknown recipe basis {basis}")


def label_requirement(gross_pieces, iml, labels_per_piece=1, allowance_pct=0):
    """IML gross pcs x labels/pc + separately approved allowance. Non-IML = 0."""
    if not iml:
        return {"base": 0, "allowance": 0, "total": 0}
    base = int(D(gross_pieces) * D(labels_per_piece))
    allowance = int(math.ceil(base * D(allowance_pct or 0) / 100))
    return {"base": base, "allowance": allowance, "total": base + allowance}


def packing_requirement(packed_pieces, pcs_per_bag, pcs_per_carton):
    bags = int(math.ceil(D(packed_pieces) / _positive("pcs per bag", pcs_per_bag)))
    cartons = int(math.ceil(D(packed_pieces) / _positive("pcs per carton", pcs_per_carton)))
    partial = int(D(packed_pieces) % D(pcs_per_carton))
    return {"bags": bags, "cartons": cartons, "partial_carton_pcs": partial}


def tape_rolls(cartons, cartons_per_roll):
    if not cartons_per_roll:
        return Decimal("0")
    return (D(cartons) / D(cartons_per_roll)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def reject_pct(rejected, total_moulded):
    if not total_moulded:
        return Decimal("0")
    return (D(rejected) / D(total_moulded) * 100).quantize(Decimal("0.01"), ROUND_HALF_UP)


def available_to_allocate(usable_on_hand, active_reservations):
    return D(usable_on_hand) - D(active_reservations)


def matchable_sets(component_balances, qty_per_set=None):
    """Potential set quantity = min over components of (balance / qty per set)."""
    if not component_balances:
        return 0
    qty_per_set = qty_per_set or {}
    return int(min(math.floor(D(bal) / D(qty_per_set.get(k, 1))) for k, bal in component_balances.items()))


def reconcile_output(total_moulded, accepted, held, rejected):
    """Exclusive buckets: total moulded = accepted + held + rejected (p.10)."""
    return int(total_moulded) == int(accepted) + int(held) + int(rejected)


def oee(availability, performance, quality):
    return (D(availability) * D(performance) * D(quality)).quantize(Decimal("0.0001"))
