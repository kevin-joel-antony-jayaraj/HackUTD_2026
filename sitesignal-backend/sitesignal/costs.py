"""Cost comparison: a preventive visit now vs. an emergency failure later.

Emergency cost = repair + revenue lost while the unit is down + spoiled stock (coolers at
sites with refrigerated inventory). Expected savings from acting now =
P(fail) x share of failures a visit prevents x emergency cost - visit cost.

Where the numbers come from, per machine type:
- Preventive visit and emergency repair cost: the median invoice cost in this portfolio's
  own work-order history (kind 'pm' and 'emergency'), once there are MIN_RECORDS of them.
  Medians, so one mistyped invoice can't swing the figure.
- Otherwise: published national figures (NATIONAL_AVERAGES, quotes and URLs in SOURCES).
- Downtime, share of revenue lost and spoiled stock have no published source: they are
  stated assumptions (lost revenue scales with each site's own daily revenue).
Every result says which basis it used.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

MIN_RECORDS = 5


@dataclass(frozen=True)
class CostProfile:
    preventive_visit_usd: float
    emergency_repair_usd: float      # after-hours call-out + parts
    downtime_days: float             # how long the unit is out after an emergency failure
    revenue_lost_share: float        # share of daily revenue lost while it's down
    inventory_loss_usd: float = 0.0  # only at sites with refrigerated inventory


HVAC_AFTER_HOURS_DISPATCH = 425  # midpoint of "$250-$600" after-hours dispatch fees (Oxmaint)

NATIONAL_AVERAGES = {
    # Compressor failure, low end of $8,000-$18,000, plus after-hours dispatch.
    "rtu": CostProfile(265, 8000 + HVAC_AFTER_HOURS_DISPATCH, 1.5, 0.5),
    # Compressor part $500-$1,500 + labor $300-$600 (midpoints), plus after-hours dispatch.
    "walk_in_cooler": CostProfile(250, 1000 + 450 + HVAC_AFTER_HOURS_DISPATCH, 1.0, 0.2, 6000),
    # Residential figures; no commercial source found. Replacement + urgent-call surcharge.
    "water_heater": CostProfile(175, 1700 + 150, 0.5, 0.0),
}
# Low end of FEMP's "Elimination of breakdowns: 70% to 75%" for predictive maintenance programs.
PREVENTION_RATE = 0.70

FEMP = ("U.S. DOE Federal Energy Management Program, O&M Best Practices Guide, Release 3.0 (Aug 2010), "
        "https://www.energy.gov/eere/femp/articles/operations-and-maintenance-best-practices-guide-achieving-operational-efficiency")
OXMAINT = ("Oxmaint, HVAC Maintenance Cost Breakdown for Commercial Buildings (Mar 2026), "
           "https://oxmaint.com/industries/facility-management/hvac-maintenance-cost-breakdown-for-commercial-buildings")
HPAC = ("HPAC Engineering, What Owners Need to Know About Rooftop-Unit Maintenance (Oct 2014), "
        "https://www.hpac.com/air-conditioning/what-owners-need-know-about-rooftop-unit-maintenance")
WALKIN = ("Same Day Appliance Repair, Commercial Walk-in Cooler Repair Cost (LA price list, undated), "
          "https://same-day-appliance-repair-03.pages.dev/price-list/commercial-walk-in-cooler-repair-cost")
FIXR = "Fixr, Water Heater Repair Cost (Aug 2025), https://www.fixr.com/costs/water-heater-repair"
INCH = "Inch Calculator, Water Heater Cost Guide (2026 prices), https://www.inchcalculator.com/water-heater-cost-guide/"

SOURCES = {
    "prevention_rate": {"quote": "Elimination of breakdowns: 70% to 75% (predictive maintenance, sec. 5.4); "
                                 "preventive maintenance: estimated 12% to 18% cost savings over reactive (sec. 5.3)",
                        "source": FEMP},
    "rtu": {
        "preventive_visit_usd": {"quote": "Rooftop Unit, Full PM, Quarterly: $180-$350 per unit (midpoint)",
                                 "source": OXMAINT,
                                 "cross_check": f"$50 to $75 per ton annually recommended for PM; {HPAC}"},
        "emergency_repair_usd": {"quote": "Compressor failure: $8,000-$18,000 (low end) + after-hours dispatch "
                                          "fees averaging $250-$600 (midpoint)", "source": OXMAINT},
    },
    "walk_in_cooler": {
        "preventive_visit_usd": {"quote": "Our flat fee for this comprehensive service is $250 for a walk-in cooler",
                                 "source": WALKIN},
        "emergency_repair_usd": {"quote": "Compressor part $500-$1,500 + compressor failure labor $300-$600 "
                                          "(midpoints) + HVAC after-hours dispatch (Oxmaint)", "source": WALKIN},
    },
    "water_heater": {
        "preventive_visit_usd": {"quote": "Flush/inspection: $100 to $250 (midpoint); residential figure", "source": FIXR},
        "emergency_repair_usd": {"quote": "Replacement around $1,700 national average + $100-$200 urgent-repair "
                                          "charge (midpoint); residential figures", "source": f"{INCH}; {FIXR}"},
    },
    "assumptions_without_source": ["downtime_days", "revenue_lost_share", "inventory_loss_usd"],
}

HISTORY_FIELDS = {"preventive_visit_usd": "pm", "emergency_repair_usd": "emergency"}


def cost_basis(db) -> dict[str, dict]:
    """Per machine type: the figures to use and where each came from."""
    rows = db.query(
        "SELECT a.asset_type, h.kind, count(*) AS n, "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY h.cost_usd) AS median_usd "
        "FROM work_order_history h JOIN assets a ON a.asset_id = h.asset_id "
        "WHERE h.cost_usd > 0 AND h.kind IN ('pm', 'emergency') GROUP BY a.asset_type, h.kind")
    history = {(r["asset_type"], r["kind"]): r for r in rows}
    basis = {}
    for asset_type, national in NATIONAL_AVERAGES.items():
        values = asdict(national)
        source = {k: "national_average" if k in HISTORY_FIELDS else "assumption" for k in values}
        records = {}
        for field, kind in HISTORY_FIELDS.items():
            h = history.get((asset_type, kind))
            records[field] = h["n"] if h else 0
            if h and h["n"] >= MIN_RECORDS:
                values[field] = round(float(h["median_usd"]))
                source[field] = "portfolio_history"
        basis[asset_type] = {"values": values, "source": source, "history_records": records}
    return basis


def compare(basis: dict, asset_type: str, prob: float | None, revenue_per_day_usd,
            has_refrigeration: bool) -> dict | None:
    b = basis.get(asset_type)
    if b is None or prob is None:
        return None
    v = b["values"]
    lost_revenue = float(revenue_per_day_usd or 0) * v["downtime_days"] * v["revenue_lost_share"]
    inventory = v["inventory_loss_usd"] if has_refrigeration else 0.0
    emergency = v["emergency_repair_usd"] + lost_revenue + inventory
    savings = prob * PREVENTION_RATE * emergency - v["preventive_visit_usd"]
    return {
        "preventive_cost_usd": round(v["preventive_visit_usd"]),
        "emergency_cost_usd": round(emergency),
        "emergency_breakdown": {
            "repair_usd": round(v["emergency_repair_usd"]),
            "lost_revenue_usd": round(lost_revenue),
            "inventory_loss_usd": round(inventory),
        },
        "expected_loss_if_ignored_usd": round(prob * emergency),
        "expected_savings_usd": round(savings),
        "worth_acting_now": savings > 0,
        "cost_source": {"preventive": b["source"]["preventive_visit_usd"],
                        "emergency_repair": b["source"]["emergency_repair_usd"]},
    }


def explain(basis: dict) -> dict:
    return {"prevention_rate": PREVENTION_RATE, "min_history_records": MIN_RECORDS,
            "by_asset_type": basis, "sources": SOURCES}
