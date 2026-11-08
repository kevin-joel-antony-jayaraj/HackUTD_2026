"""Cost comparison: a preventive visit now vs. an emergency failure later.

Emergency cost = repair + revenue lost while the unit is down + spoiled stock (coolers at
sites with refrigerated inventory). Expected savings from acting now =
P(fail) x share of failures a visit prevents x emergency cost - visit cost.

Every dollar figure here is a demo ASSUMPTION (typical small-commercial ranges, not
sourced). They are returned with the results so the dashboard can say so.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CostProfile:
    preventive_visit_usd: float
    emergency_repair_usd: float      # after-hours call-out + parts
    downtime_days: float             # how long the unit is out after an emergency failure
    revenue_lost_share: float        # share of daily revenue lost while it's down
    inventory_loss_usd: float = 0.0  # only at sites with refrigerated inventory


PROFILES = {
    "rtu": CostProfile(450, 4200, 1.5, 0.5),
    "walk_in_cooler": CostProfile(400, 3500, 1.0, 0.2, 6000),
    "water_heater": CostProfile(250, 1800, 0.5, 0.0),
}
PREVENTION_RATE = 0.8  # share of failures a preventive visit heads off

ASSUMPTIONS = {
    "note": "Demo assumptions, not sourced figures",
    "prevention_rate": PREVENTION_RATE,
    "profiles": {k: asdict(v) for k, v in PROFILES.items()},
}


def compare(asset_type: str, prob: float | None, revenue_per_day_usd, has_refrigeration: bool) -> dict | None:
    p = PROFILES.get(asset_type)
    if p is None or prob is None:
        return None
    lost_revenue = float(revenue_per_day_usd or 0) * p.downtime_days * p.revenue_lost_share
    inventory = p.inventory_loss_usd if has_refrigeration else 0.0
    emergency = p.emergency_repair_usd + lost_revenue + inventory
    savings = prob * PREVENTION_RATE * emergency - p.preventive_visit_usd
    return {
        "preventive_cost_usd": round(p.preventive_visit_usd),
        "emergency_cost_usd": round(emergency),
        "emergency_breakdown": {
            "repair_usd": round(p.emergency_repair_usd),
            "lost_revenue_usd": round(lost_revenue),
            "inventory_loss_usd": round(inventory),
        },
        "expected_loss_if_ignored_usd": round(prob * emergency),
        "expected_savings_usd": round(savings),
        "worth_acting_now": savings > 0,
    }
