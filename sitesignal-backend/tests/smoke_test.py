"""Smoke test of everything except the HTTP layer and real network calls.

    DB_ADAPTER=psql PSQL_ARGS="-h /tmp -p 5544 -U postgres -d sitesignal" BRAIN=mock python -m tests.smoke_test
(or set DATABASE_URL and drop DB_ADAPTER to use psycopg)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from seed import seed
from sitesignal import importer, scheduler, services, weather
from sitesignal.photon_inbound import extract_message, handle_webhook_event, verify_signature
from sitesignal.store import Store
from sitesignal.tools import Toolbox

ROOT = Path(__file__).resolve().parent.parent


def get_db():
    if os.environ.get("DB_ADAPTER") == "psql":
        from tests.psql_db import PsqlDatabase
        return PsqlDatabase(os.environ["PSQL_ARGS"].split())
    from sitesignal.db import Database
    return Database()


def check(label, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + label + (f"  ({detail})" if detail else ""))
    assert cond, label


def main():
    db = get_db()
    seed(db, date.today())

    # --- data import (messy sample files) -----------------------------------
    r1 = importer.import_file(db, "sites", (ROOT / "samples/sites.csv").read_bytes(), "sites.csv")
    check("import sites", r1["imported"] == 3 and r1["quarantined"] == 1, r1)
    r2 = importer.import_file(db, "assets", (ROOT / "samples/assets.csv").read_bytes(), "assets.csv")
    check("import assets", r2["imported"] == 5 and r2["quarantined"] == 2, r2)
    est = db.query("SELECT install_date_estimated FROM assets WHERE asset_id = 'D101-COOLER1'")[0]
    check("year-only install date marked estimated", est["install_date_estimated"] is True)
    r3 = importer.import_file(db, "work_orders", (ROOT / "samples/work_orders.csv").read_bytes(), "wo.csv")
    check("import work orders", r3["imported"] == 5 and r3["quarantined"] == 3, r3)
    check("fuzzy matched 'D101 RTU 1'", r3["fuzzy_matched"] >= 1)
    rech = db.query("SELECT count(*) AS n FROM work_order_history WHERE asset_id = 'D101-RTU1' AND refrigerant_added")[0]
    check("recharge detected from note text", rech["n"] == 2, rech)
    neg = db.query("SELECT cost_usd FROM work_order_history WHERE asset_id = 'H201-RTU1'")[0]
    check("negative cost dropped", neg["cost_usd"] is None)

    # --- weather job (fake fetch; real one calls Open-Meteo) -----------------
    sample = {"daily": {"time": [f"{date.today().isoformat()}"] + [f"2099-01-0{i}" for i in range(1, 7)],
                        "temperature_2m_max": [88.1, 99.4, 101.2, 97.0, 90.3, None, 85.0]}}
    check("parse Open-Meteo payload", len(weather.parse_forecast(sample)) == 6)
    w = weather.refresh_all(db, fetch=lambda lat, lon: weather.parse_forecast(sample))
    check("weather refresh", w["sites_updated"] >= 40 and not w["failed"], {k: w[k] for k in ("sites_updated", "api_calls")})

    # --- scoring + scheduler --------------------------------------------------
    check("score_all", services.score_all(db) > 80)
    tz = ZoneInfo("America/Chicago")
    ran = scheduler.tick(db, datetime.now(tz).replace(hour=5, minute=0))
    check("scheduler before 06:00 only rescores", "sweep" not in ran, ran)
    os.environ["PHOTON_SEND"] = "0"
    scheduler.services.refresh_weather = lambda d: {"skipped": "offline test"}
    ran = scheduler.tick(db, datetime.now(tz).replace(hour=6, minute=5))
    check("scheduler at 06:05 runs weather + sweep", ran[:2] == ["weather", "sweep"], ran)
    ran = scheduler.tick(db, datetime.now(tz).replace(hour=6, minute=6))
    check("scheduler does not repeat today's sweep", "sweep" not in ran, ran)

    # --- API read services -------------------------------------------------------
    p = services.portfolio(db)
    check("portfolio summary", p["assets"] > 80 and p["work_orders_by_status"], p["work_orders_by_status"])
    top = services.assets_list(db, "high", 5)
    check("assets list sorted by risk", top and top[0]["prob"] >= top[-1]["prob"])
    d = services.asset_detail(db, "D101-RTU1")
    check("asset detail for imported asset", d and d["risk_now"]["prob"] > 0, d["risk_now"]["tier"])

    # --- Photon inbound: webhook signature, parsing, dedupe ---------------------------
    secret = "whsec_" + base64.b64encode(b"test-secret-key-123").decode()
    body = json.dumps({"id": "evt_1", "data": {"id": "msg_1", "sentBy": {"kind": "human"},
                       "receivedBy": {"id": "agent-1"}, "space": {"id": "space-dallas"},
                       "content": {"text": "JOIN dallas"}}}).encode()
    ts = str(int(time.time()))
    sig = base64.b64encode(hmac.new(b"test-secret-key-123", f"evt_1.{ts}.".encode() + body,
                                    hashlib.sha256).digest()).decode()
    check("valid signature accepted", verify_signature(secret, "evt_1", ts, body, f"v1,{sig}"))
    check("tampered body rejected", not verify_signature(secret, "evt_1", ts, body + b" ", f"v1,{sig}"))
    check("old timestamp rejected", not verify_signature(secret, "evt_1", str(int(time.time()) - 900), body, f"v1,{sig}"))
    check("agent echo ignored", extract_message({"sentBy": {"kind": "agent"}, "id": "x",
                                                 "space": {"id": "s"}, "content": {"text": "hi"}}) is None)
    tb = Toolbox(Store(db), dry_run=True)
    r = handle_webhook_event(tb, json.loads(body))
    check("webhook JOIN registers service manager", r.get("registered") == "region", r)
    r = handle_webhook_event(tb, json.loads(body))
    check("duplicate delivery skipped", r.get("skipped") == "already processed", r)

    wo = db.query("SELECT w.id FROM work_orders w JOIN sites s USING (site_id) WHERE s.region_id = 'dallas' LIMIT 1")
    if wo:
        evt = {"data": {"id": "msg_2", "sentBy": {"kind": "human"}, "receivedBy": {"id": "agent-1"},
                        "space": {"id": "space-dallas"}, "content": {"text": f"approve {wo[0]['id']}"}}}
        r = handle_webhook_event(tb, evt)
        st = db.query("SELECT status FROM work_orders WHERE id = %s", (wo[0]["id"],))[0]["status"]
        check("text APPROVE updates work order", st == "approved", r.get("summary"))

    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
