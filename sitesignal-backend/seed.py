"""Load the demo portfolio into Postgres: 12 fictional sites in real Dallas, Houston and
Austin neighborhoods, their equipment, two years of maintenance history, a placeholder
forecast (the weather job replaces it with Open-Meteo data) and 30 days of back-scored risk.

Sizes are real averages: mean square feet per building and weekly operating hours by
building type, U.S. EIA CBECS 2018, Table B15
(https://www.eia.gov/consumption/commercial/data/2018/bc/html/b15.php).
Assumptions, not sourced: daily revenue per site type, and HVAC sizing of about one ton
of cooling per 350 sq ft served by 10-ton rooftop units.

Four machines are deliberately troubled (TROUBLED); everything else is healthy and on its
maintenance schedule. Repair invoices carry no cost, so the cost model uses its cited
national figures.

    python seed.py            # wipes the demo tables, then loads the portfolio
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta, timezone

# People's registrations (contacts), handled inbound texts and the Photon poll cursor survive a
# reset: on Photon's shared line a dropped conversation can't be reopened from our side.
WIPE = """TRUNCATE regions, sites, assets, work_order_history, weather_forecasts,
field_reports, work_orders, dashboard_entries, risk_scores, messages, agent_runs, audit_log,
import_quarantine, calls RESTART IDENTITY CASCADE;
DELETE FROM job_state WHERE name <> 'photon_cursor';
ALTER SEQUENCE work_order_seq RESTART WITH 1;"""

# CBECS 2018 Table B15: mean sq ft per building, mean operating hours per week.
# Food sales 6,200 sq ft / 110 h; outpatient health care 13,700 / 52; retail (other than
# mall) 14,800 / 66; bank or other financial 13,400 / 50.
SITE_TYPES = {
    "grocery": {"sq_ft": 6200, "hours": "7am-10pm daily", "revenue_per_day": 14000, "refrigeration": True},
    "clinic": {"sq_ft": 13700, "hours": "8am-6pm Mon-Fri", "revenue_per_day": 11000, "refrigeration": False},
    "retail": {"sq_ft": 14800, "hours": "10am-9pm daily", "revenue_per_day": 9000, "refrigeration": False},
    "bank branch": {"sq_ft": 13400, "hours": "9am-5pm Mon-Fri", "revenue_per_day": 4000, "refrigeration": False},
}

REGIONS = {"dallas": "Dallas", "houston": "Houston", "austin": "Austin"}

# (site_id, name, type, city, region, lat, lon, size vs. the CBECS mean)
SITES = [
    ("S001", "Uptown Bank Branch", "bank branch", "Dallas", "dallas", 32.7990, -96.8020, 1.05),
    ("S002", "Legacy Family Clinic", "clinic", "Plano", "dallas", 33.0770, -96.8220, 0.95),
    ("S003", "Richardson Market", "grocery", "Richardson", "dallas", 32.9480, -96.7300, 1.10),
    ("S004", "Frisco Retail Store", "retail", "Frisco", "dallas", 33.1500, -96.8240, 1.00),
    ("S005", "Montrose Market", "grocery", "Houston", "houston", 29.7450, -95.3900, 0.90),
    ("S006", "Katy Family Clinic", "clinic", "Katy", "houston", 29.7860, -95.8240, 1.10),
    ("S007", "Galleria Bank Branch", "bank branch", "Houston", "houston", 29.7390, -95.4630, 0.90),
    ("S008", "Sugar Land Retail Store", "retail", "Sugar Land", "houston", 29.6190, -95.6350, 1.15),
    ("S009", "South Congress Market", "grocery", "Austin", "austin", 30.2500, -97.7490, 1.00),
    ("S010", "Round Rock Clinic", "clinic", "Round Rock", "austin", 30.5080, -97.6790, 0.90),
    ("S011", "Domain Retail Store", "retail", "Austin", "austin", 30.4020, -97.7250, 0.85),
    ("S012", "Las Colinas Bank Branch", "bank branch", "Irving", "dallas", 32.8690, -96.9400, 1.00),
]

SQ_FT_PER_TON = 350
RTU_TONS = 10

# The four problem machines. days = days before today; recharge = refrigerant added.
TROUBLED = {
    "S005-WALK_IN_COOLER1": {
        "story": "Leaking walk-in cooler at a grocery: two refrigerant recharges, keeps losing temperature",
        "age_years": 16, "last_pm_days": 130,
        "repairs": [(150, "repair", "leak", True, "low on refrigerant, recharged"),
                    (60, "repair", "cooler_temp", True, "cooler not holding temp, added refrigerant"),
                    (25, "emergency", "cooler_temp", False, "cooler at 48F overnight, product moved"),
                    (10, "repair", "cooler_temp", False, "cooler not holding temp again, reset controls")],
    },
    "S001-RTU2": {
        "story": "21-year-old R-22 rooftop unit at a bank branch, short cycling, maintenance 7 months overdue",
        "age_years": 21, "last_pm_days": 300,
        "repairs": [(70, "repair", "short_cycling", False, "unit short cycling, replaced contactor"),
                    (45, "repair", "short_cycling", False, "short cycling, replaced thermostat"),
                    (20, "emergency", "short_cycling", False, "short cycling again, lobby at 84F")],
    },
    "S006-WATER_HEATER1": {
        "story": "12-year-old water heater at a clinic, past its expected life, repaired last month",
        "age_years": 12, "last_pm_days": 45,
        "repairs": [(80, "repair", "no_hot_water", False, "lukewarm water, adjusted thermostat"),
                    (50, "repair", "no_hot_water", False, "no hot water in exam rooms, relit pilot"),
                    (18, "repair", "element", False, "replaced heating element")],
    },
    "S004-RTU1": {
        "story": "15-year-old rooftop unit at a retail store with a slow refrigerant leak, maintenance 2 months overdue",
        "age_years": 15, "last_pm_days": 160,
        "repairs": [(100, "repair", "leak", True, "AC blowing warm, added refrigerant"),
                    (40, "repair", "not_cooling", False, "not cooling at peak, cleaned coil"),
                    (15, "repair", "not_cooling", False, "warm air from vents, cleaned coil again")],
    },
}


def _assets_for(site_id: str, site_type: str, sq_ft: int) -> list[tuple[str, str]]:
    rtus = max(1, round(sq_ft / SQ_FT_PER_TON / RTU_TONS))
    out = [(f"{site_id}-RTU{i + 1}", "rtu") for i in range(rtus)]
    if site_type == "grocery":
        out.append((f"{site_id}-WALK_IN_COOLER1", "walk_in_cooler"))
    if site_type in ("grocery", "clinic"):
        out.append((f"{site_id}-WATER_HEATER1", "water_heater"))
    return out


HEALTHY_AGE = {"rtu": (2, 11), "walk_in_cooler": (3, 10), "water_heater": (1, 7)}
OLD_REPAIRS = [("capacitor", "replaced run capacitor"), ("filter", "clogged filter, replaced"),
               ("belt", "replaced worn belt"), ("drain", "cleared condensate drain")]


def seed(db, today: date, seed_value: int = 7) -> None:
    rng = random.Random(seed_value)
    db.query(WIPE)
    for region_id, name in REGIONS.items():
        db.query("INSERT INTO regions VALUES (%s, %s)", (region_id, name))

    for sid, name, typ, city, region, lat, lon, size in SITES:
        t = SITE_TYPES[typ]
        sq_ft = int(round(t["sq_ft"] * size, -2))
        db.query("INSERT INTO sites (site_id, name, type, city, region_id, has_refrigeration, revenue_per_day_usd, "
                 "open_hours, lat, lon, square_feet) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                 (sid, name, typ, city, region, t["refrigeration"], round(t["revenue_per_day"] * size, -2),
                  t["hours"], lat, lon, sq_ft))
        # placeholder forecast without heat; `python -m sitesignal.jobs weather` loads the real one
        for d in range(7):
            db.query("INSERT INTO weather_forecasts (site_id, forecast_date, max_temp_f) VALUES (%s, %s::date, %s)",
                     (sid, (today + timedelta(days=d)).isoformat(), round(rng.uniform(70, 85), 1)))

        for aid, typ_a in _assets_for(sid, typ, sq_ft):
            trouble = TROUBLED.get(aid)
            age = trouble["age_years"] if trouble else rng.uniform(*HEALTHY_AGE[typ_a])
            install = today - timedelta(days=int(age * 365.25))
            db.query("INSERT INTO assets (asset_id, site_id, asset_type, install_date, refrigerant, pm_interval_days) "
                     "VALUES (%s, %s, %s, %s::date, %s, 90)",
                     (aid, sid, typ_a, install.isoformat(),
                      None if typ_a == "water_heater" else ("R-22" if age > 14 else "R-410A")))

            # preventive maintenance every 90 days for two years, never before install
            last_pm = trouble["last_pm_days"] if trouble else rng.randint(5, 80)
            for days in range(last_pm, 730, 90):
                if today - timedelta(days=days) > install:
                    _history(db, aid, today, days, "pm", None, False, "scheduled preventive maintenance")

            if trouble:
                for days, kind, issue, recharge, note in trouble["repairs"]:
                    _history(db, aid, today, days, kind, issue, recharge, note)
            elif rng.random() < 0.4:  # an old, unrelated repair
                issue, note = rng.choice(OLD_REPAIRS)
                _history(db, aid, today, rng.randint(200, 700), "repair", issue, False, note)

    _backfill_risk(db, today)
    from sitesignal.contacts import apply_from_env
    from sitesignal.store import Store
    apply_from_env(Store(db))


def _history(db, asset_id, today, days_ago, kind, issue, recharge, note) -> None:
    db.query("INSERT INTO work_order_history (asset_id, opened_at, kind, issue_category, refrigerant_added, "
             "note, source) VALUES (%s, %s::date, %s, %s, %s, %s, 'seed')",
             (asset_id, (today - timedelta(days=days_ago)).isoformat(), kind, issue, recharge, note))


def _backfill_risk(db, today: date, days: int = 30) -> None:
    """Score every machine as of each of the last 30 days, so risk has a history to chart."""
    from sitesignal.risk_model import score_asset, tier
    from sitesignal.store import Store, _json
    from sitesignal.tools import HIGH, MEDIUM

    for back in range(days, 0, -1):
        day = today - timedelta(days=back)
        at = datetime.combine(day, time(12), tzinfo=timezone.utc)
        for facts in Store(db, day).features().values():
            r = score_asset(facts, day)
            if not r.insufficient_data:
                db.query("INSERT INTO risk_scores (asset_id, scored_at, prob, base_prob, multiplier, tier, drivers) "
                         "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb) ON CONFLICT DO NOTHING",
                         (r.asset_id, at, r.prob, r.base_prob, r.hazard_multiplier,
                          tier(r.prob, HIGH, MEDIUM), _json(r.drivers)))


if __name__ == "__main__":
    from sitesignal.db import Database
    seed(Database(), date.today())
    print("seeded")
