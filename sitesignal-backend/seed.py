"""Load a synthetic portfolio into Postgres: regions, sites, assets, raw work-order
history and a 7-day forecast. Features are derived in SQL (asset_features), not stored.

    python seed.py            # wipes the demo tables, then loads 40 sites
"""
from __future__ import annotations

import random
from datetime import date, timedelta

WIPE = """TRUNCATE regions, sites, assets, work_order_history, weather_forecasts, contacts,
field_reports, work_orders, dashboard_entries, risk_scores, messages, agent_runs, audit_log,
import_quarantine, inbound_messages, job_state, calls RESTART IDENTITY CASCADE;
ALTER SEQUENCE work_order_seq RESTART WITH 1;"""

CENTERS = {"dallas": (32.78, -96.80), "houston": (29.76, -95.37), "austin": (30.27, -97.74)}

NOTES = {
    "not_cooling": "AC blowing warm, added refrigerant",
    "short_cycling": "unit short cycling, replaced contactor",
    "capacitor": "replaced run capacitor",
    "cooler_temp": "cooler not holding temp",
}


def seed(db, today: date, n_sites: int = 40, seed_value: int = 7) -> None:
    rng = random.Random(seed_value)
    db.query(WIPE)
    for region in ("dallas", "houston", "austin"):
        db.query("INSERT INTO regions VALUES (%s, %s)", (region, region.title()))

    for i in range(n_sites):
        sid = f"S{i+1:03d}"
        region = rng.choice(["dallas", "houston", "austin"])
        refrig = rng.random() < 0.35
        lat, lon = CENTERS[region]
        db.query("INSERT INTO sites (site_id, name, type, city, region_id, has_refrigeration, "
                 "revenue_per_day_usd, open_hours, lat, lon) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                 (sid, f"Store {i+1:03d}", "grocery" if refrig else rng.choice(["retail", "bank branch", "clinic"]),
                  region.title(), region, refrig, rng.choice([3000, 6000, 12000]), "8-21",
                  round(lat + rng.uniform(-0.25, 0.25), 4), round(lon + rng.uniform(-0.25, 0.25), 4)))
        heat = rng.random() < 0.5
        for d in range(7):
            temp = rng.uniform(96, 104) if heat and d > 1 else rng.uniform(78, 92)
            db.query("INSERT INTO weather_forecasts (site_id, forecast_date, max_temp_f) VALUES (%s, %s::date, %s)",
                     (sid, (today + timedelta(days=d)).isoformat(), round(temp, 1)))

        for j, typ in enumerate(["rtu", "rtu"] + (["walk_in_cooler"] if refrig else [])):
            aid = f"{sid}-{typ.upper()}{j+1}"
            age = rng.uniform(2, 19)
            install = None if rng.random() < 0.03 else (today - timedelta(days=int(age * 365.25))).isoformat()
            db.query("INSERT INTO assets (asset_id, site_id, asset_type, install_date, refrigerant, pm_interval_days) "
                     "VALUES (%s, %s, %s, %s::date, %s, 90)",
                     (aid, sid, typ, install, "R-22" if age > 14 else "R-410A"))

            troubled = rng.random() < 0.25
            calls = rng.randint(2, 4) if troubled else rng.choice([0, 0, 1])
            recharges = rng.randint(1, 2) if troubled else 0
            issue = rng.choice(list(NOTES))
            for k in range(calls):
                db.query("INSERT INTO work_order_history (asset_id, opened_at, kind, issue_category, "
                         "refrigerant_added, cost_usd, note, source) VALUES (%s, %s::date, %s, %s, %s, %s, %s, 'seed')",
                         (aid, (today - timedelta(days=rng.randint(3, 85))).isoformat(),
                          "emergency" if rng.random() < 0.3 else "repair",
                          issue if troubled else rng.choice(list(NOTES)),
                          k < recharges, None, NOTES[issue]))  # no costs: demo uses national averages
            # last preventive maintenance: recent for most, overdue for some
            last_pm = rng.choice([20, 45, 80, 150, 240])
            db.query("INSERT INTO work_order_history (asset_id, opened_at, kind, note, source) "
                     "VALUES (%s, %s::date, 'pm', 'filter + coil service', 'seed')",
                     (aid, (today - timedelta(days=last_pm)).isoformat()))


if __name__ == "__main__":
    from sitesignal.db import Database
    seed(Database(), date.today())
    print("seeded")
