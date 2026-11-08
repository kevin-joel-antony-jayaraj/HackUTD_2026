"""Load extracted data + Safety Index into Postgres. Idempotent: safe to run again on the same files.

    export DATABASE_URL=postgres://user:pass@host:5432/dbname
    python -m sitesignal.load_pg --bundle out/bundle.json --safety out/safety.json [--weather out/weather.json]

Everything happens in ONE transaction: either the whole load lands or nothing does.
Sites and assets are upserted, work orders and rejected rows are insert-if-new, and each
Safety Index file becomes a new `safety_runs` row (history is kept).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

import psycopg
from psycopg.types.json import Jsonb

SCHEMA = os.path.join(os.path.dirname(__file__), "..", "db", "safety_schema.sql")


def load(conn, bundle: dict, safety: dict | None, weather: dict | None) -> dict:
    n = {"sites": 0, "assets": 0, "work_orders_new": 0, "rejected_new": 0, "weather": 0, "scores": 0}
    with conn.cursor() as cur:
        cur.execute(open(SCHEMA).read())

        for s in bundle.get("sites", []):
            cur.execute("""INSERT INTO sites (site_id, name, type, city, region_id, has_refrigeration,
                                revenue_per_day_usd, lat, lon, flags)
                           VALUES (%(site_id)s, %(name)s, %(type)s, %(city)s, %(region_id)s, %(has_refrigeration)s,
                                %(revenue_per_day_usd)s, %(lat)s, %(lon)s, %(flags)s)
                           ON CONFLICT (site_id) DO UPDATE SET name = EXCLUDED.name, type = EXCLUDED.type,
                                city = EXCLUDED.city, region_id = EXCLUDED.region_id,
                                has_refrigeration = EXCLUDED.has_refrigeration,
                                revenue_per_day_usd = EXCLUDED.revenue_per_day_usd, lat = EXCLUDED.lat,
                                lon = EXCLUDED.lon, flags = EXCLUDED.flags, updated_at = now()""",
                        {**s, "flags": Jsonb(s.get("flags", []))})
            n["sites"] += 1

        for a in bundle.get("assets", []):
            cur.execute("""INSERT INTO assets (asset_id, site_id, asset_type, install_date, install_date_estimated,
                                make, model, serial_number, refrigerant, pm_interval_days, flags)
                           VALUES (%(asset_id)s, %(site_id)s, %(asset_type)s, %(install_date)s,
                                %(install_date_estimated)s, %(make)s, %(model)s, %(serial_number)s,
                                %(refrigerant)s, %(pm_interval_days)s, %(flags)s)
                           ON CONFLICT (asset_id) DO UPDATE SET site_id = EXCLUDED.site_id,
                                asset_type = EXCLUDED.asset_type, install_date = EXCLUDED.install_date,
                                install_date_estimated = EXCLUDED.install_date_estimated, make = EXCLUDED.make,
                                model = EXCLUDED.model, serial_number = EXCLUDED.serial_number,
                                refrigerant = EXCLUDED.refrigerant, pm_interval_days = EXCLUDED.pm_interval_days,
                                flags = EXCLUDED.flags, updated_at = now()""",
                        {**a, "flags": Jsonb(a.get("flags", []))})
            n["assets"] += 1

        for w in bundle.get("work_orders", []):
            cur.execute("""INSERT INTO work_order_history (asset_id, opened_at, kind, issue_category,
                                refrigerant_added, cost_usd, note, source, flags)
                           VALUES (%(asset_id)s, %(opened_at)s, %(kind)s, %(issue_category)s, %(refrigerant_added)s,
                                %(cost_usd)s, %(note)s, %(source)s, %(flags)s)
                           ON CONFLICT DO NOTHING""", {**w, "flags": Jsonb(w.get("flags", []))})
            n["work_orders_new"] += cur.rowcount

        for r in bundle.get("rejected", []):
            cur.execute("""INSERT INTO rejected_rows (source, raw, reason) VALUES (%s, %s, %s)
                           ON CONFLICT DO NOTHING""",
                        (r.get("source"), Jsonb(r.get("raw") if r.get("raw") is not None else {}), r["reason"]))
            n["rejected_new"] += cur.rowcount

        if weather:
            today = date.today()
            for site_id, temps in weather.items():
                for i, t in enumerate(temps):
                    cur.execute("""INSERT INTO weather_forecasts (site_id, forecast_date, max_temp_f)
                                   VALUES (%s, %s::date + %s, %s)
                                   ON CONFLICT (site_id, forecast_date) DO UPDATE
                                   SET max_temp_f = EXCLUDED.max_temp_f, fetched_at = now()""",
                                (site_id, today.isoformat(), i, t))
                    n["weather"] += 1

        if safety:
            m = safety["meta"]
            cur.execute("""INSERT INTO safety_runs (as_of, model_version, calibrated, meta)
                           VALUES (%s, %s, %s, %s) RETURNING run_id""",
                        (m["as_of"], m["model_version"], m["calibrated"], Jsonb(m)))
            run_id = cur.fetchone()[0]
            for a in safety["assets"]:
                cur.execute("""INSERT INTO asset_safety_scores (run_id, asset_id, safety_index, band, confidence,
                                    drivers, data_gaps, constraints, business_exposure, inputs, repair_spend_usd,
                                    credits_present, flags)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (run_id, a["asset_id"], a["safety_index"], a["band"], a["confidence"],
                             Jsonb(a["drivers"]), Jsonb(a["data_gaps"]), Jsonb(a["constraints"]),
                             Jsonb(a["business_exposure"]), Jsonb(a["inputs"]), a["repair_spend_usd"],
                             a["credits_present"], Jsonb(a["flags"])))
                n["scores"] += 1
            for s in safety["sites"]:
                cur.execute("""INSERT INTO site_safety_scores (run_id, site_id, safety_index, band, confidence,
                                    worst_asset_id, assets_without_score, constraints, business_exposure)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (run_id, s["site_id"], s["safety_index"], s["band"], s["confidence"],
                             s["worst_asset_id"], Jsonb(s["assets_without_score"]), Jsonb(s["constraints"]),
                             Jsonb(s["business_exposure"])))
            n["run_id"] = run_id
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--safety")
    ap.add_argument("--weather")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    a = ap.parse_args()
    if not a.dsn:
        sys.exit("Set DATABASE_URL (or pass --dsn).")
    j = lambda p: json.load(open(p)) if p else None
    with psycopg.connect(a.dsn) as conn:      # commits on success, rolls back on any error
        counts = load(conn, j(a.bundle), j(a.safety), j(a.weather))
    print("loaded:", counts, file=sys.stderr)


if __name__ == "__main__":
    main()
