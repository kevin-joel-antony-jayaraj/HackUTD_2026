"""End-to-end demo on Postgres: seed -> agent sweep -> Photon (dry run) -> replies -> agent.

    psql "$DATABASE_URL" -f db/schema.sql      # once
    BRAIN=mock DATABASE_URL=... python demo.py # offline brain
    GEMINI_API_KEY=... DATABASE_URL=... python demo.py
    ... --send                                  # real Photon texts
"""
from __future__ import annotations

import argparse
import os
from datetime import date

from seed import seed
from sitesignal.agent import run_agent
from sitesignal.inbound import handle_inbound
from sitesignal.store import Store
from sitesignal.tools import HIGH, MEDIUM, Toolbox


def get_db():
    if os.environ.get("DB_ADAPTER") == "psql":  # test-only fallback, see tests/psql_db.py
        from tests.psql_db import PsqlDatabase
        return PsqlDatabase(os.environ["PSQL_ARGS"].split())
    from sitesignal.db import Database
    return Database()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="really send texts through Photon")
    args = ap.parse_args()

    db, today = get_db(), date.today()
    seed(db, today)
    store = Store(db, today)
    tb = Toolbox(store, dry_run=not args.send)

    # People register by texting the Photon number once.
    for region in ("dallas", "houston", "austin"):
        handle_inbound(f"JOIN {region}", f"space-{region}", "agent-1", tb)
    for aid in store.asset_ids():
        site = aid.split("-")[0]
        handle_inbound(f"JOIN site {site}", f"space-{site}", "agent-1", tb)

    print(f"Brain: {'mock' if os.environ.get('BRAIN') == 'mock' else 'Gemini'}  "
          f"thresholds high={HIGH} medium={MEDIUM}\n")

    res = run_agent("Run today's risk sweep for the portfolio.", tb)
    print("SWEEP:", res["summary"][:500], "\n")
    print(f"  run={res['run_id']} steps={res['steps']} safety-net misses={res['safety_net']}")
    for t in ("work_orders", "messages", "dashboard_entries", "risk_scores", "audit_log"):
        print(f"  {t}: {store.count(t)}")
    print()
    for m in db.query("SELECT recipient_name, text FROM messages ORDER BY id LIMIT 3"):
        print(f"  -> {m['recipient_name']}: {m['text']}")
    print()

    checkin = db.query("SELECT to_key FROM messages WHERE kind = 'site' ORDER BY id LIMIT 1")
    if checkin:
        r = handle_inbound("N, it's blowing warm air", f"space-{checkin[0]['to_key'].upper()}", "agent-1", tb)
        print("SITE REPLY 'N':", r.get("summary", r), "\n")

    first = db.query("SELECT w.id, s.region_id FROM work_orders w JOIN sites s USING (site_id) ORDER BY w.id LIMIT 1")
    if first:
        r = handle_inbound(f"APPROVE {first[0]['id']}", f"space-{first[0]['region_id']}", "agent-1", tb)
        print("MANAGER REPLY:", r.get("summary", r), "\n")

    print("Work orders now:")
    for w in db.query("SELECT id, asset_id, tier, status, notified FROM work_orders ORDER BY id"):
        print(f"  {w['id']} {w['asset_id']:<24} {w['tier']:<6} {w['status']:<17} notified={w['notified']}")


if __name__ == "__main__":
    main()
