"""Read queries for the API and dashboard, plus the jobs the scheduler runs."""
from __future__ import annotations

import os
import threading
from datetime import date

from .agent import run_agent
from .risk_model import score_asset, tier
from .store import Store
from .tools import HIGH, MEDIUM, Toolbox
from . import costs, photon_call, weather

_sweep_lock = threading.Lock()

LATEST_RISK = """SELECT DISTINCT ON (asset_id) asset_id, prob, base_prob, multiplier, tier, drivers, scored_at
                 FROM risk_scores ORDER BY asset_id, scored_at DESC"""


def _clean(rows):
    from decimal import Decimal
    return [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in rows]


# ---------------------------------------------------------------- jobs
def score_all(db, today: date | None = None) -> int:
    """Score every asset without the agent (cheap; keeps the dashboard current)."""
    store = Store(db, today)
    n = 0
    for aid, facts in store.features().items():
        r = score_asset(facts, store.today)
        if not r.insufficient_data:
            store.save_risk(r, tier(r.prob, HIGH, MEDIUM))
            n += 1
    return n


def run_sweep(db, dry_run: bool | None = None, brain=None) -> dict:
    if not _sweep_lock.acquire(blocking=False):
        return {"skipped": "a sweep is already running"}
    try:
        if dry_run is None:
            dry_run = os.environ.get("PHOTON_SEND", "0") != "1"
        tb = Toolbox(Store(db), dry_run=dry_run)
        result = run_agent("Run today's risk sweep for the portfolio.", tb, brain)
        # after the agent, so the call can name its work order; a dry-run sweep never dials
        result["calls"] = call_urgent(db, dry_run=True if dry_run else None)
        return result
    finally:
        _sweep_lock.release()


def call_urgent(db, dry_run: bool | None = None) -> list[dict]:
    return photon_call.call_urgent(Store(db), dry_run)


def refresh_weather(db) -> dict:
    return weather.refresh_all(db)


# ---------------------------------------------------------------- reads
def portfolio(db) -> dict:
    tiers = db.query(f"SELECT tier, count(*) AS n FROM ({LATEST_RISK}) r GROUP BY tier")
    wo = db.query("SELECT status, count(*) AS n FROM work_orders GROUP BY status")
    last = db.query("SELECT run_id, trigger, summary, finished_at FROM agent_runs ORDER BY run_id DESC LIMIT 1")
    return {
        "sites": db.query("SELECT count(*) AS n FROM sites")[0]["n"],
        "square_feet": db.query("SELECT coalesce(sum(square_feet), 0) AS n FROM sites")[0]["n"],
        "assets": db.query("SELECT count(*) AS n FROM assets")[0]["n"],
        "assets_by_tier": {r["tier"]: r["n"] for r in tiers},
        "work_orders_by_status": {r["status"]: r["n"] for r in wo},
        "texts_today": db.query("SELECT count(*) AS n FROM messages WHERE created_at::date = current_date")[0]["n"],
        "quarantined_rows": db.query("SELECT count(*) AS n FROM import_quarantine")[0]["n"],
        "last_run": last[0] if last else None,
        "thresholds": {"high": HIGH, "medium": MEDIUM},
        "savings": savings(db, include_basis=False),
    }


def _with_cost(db, rows: list[dict], basis: dict | None = None) -> list[dict]:
    basis = basis or costs.cost_basis(db)
    for r in rows:
        r["cost"] = costs.compare(basis, r["asset_type"], r.get("prob"), r.pop("revenue_per_day_usd", None),
                                  r.pop("has_refrigeration", False))
    return rows


def savings(db, include_basis: bool = True) -> dict:
    """The savings counter. Secured = approved or done work orders; pending = awaiting approval.
    Exposure = expected emergency cost over the next 30 days across every scored machine
    if nothing were done; unaddressed = the part with no open work order."""
    basis = costs.cost_basis(db)
    wos = _with_cost(db, _clean(db.query(
        "SELECT w.status, w.prob, a.asset_type, s.revenue_per_day_usd, s.has_refrigeration "
        "FROM work_orders w JOIN assets a ON a.asset_id = w.asset_id JOIN sites s ON s.site_id = w.site_id")), basis)
    out = {}
    for name, statuses in (("secured", ("approved", "done")), ("pending", ("pending_approval",))):
        picked = [w["cost"] for w in wos if w["status"] in statuses and w["cost"]]
        out[f"savings_{name}_usd"] = sum(c["expected_savings_usd"] for c in picked)
        out[f"work_orders_{name}"] = len(picked)
        out[f"preventive_spend_{name}_usd"] = sum(c["preventive_cost_usd"] for c in picked)
    assets = _with_cost(db, _clean(db.query(
        f"SELECT a.asset_type, r.prob, s.revenue_per_day_usd, s.has_refrigeration, "
        f"EXISTS (SELECT 1 FROM work_orders w WHERE w.asset_id = a.asset_id "
        f"        AND w.status IN ('pending_approval','approved')) AS covered "
        f"FROM assets a JOIN sites s USING (site_id) JOIN ({LATEST_RISK}) r USING (asset_id)")), basis)
    out["exposure_usd"] = sum(a["cost"]["expected_loss_if_ignored_usd"] for a in assets if a["cost"])
    out["exposure_unaddressed_usd"] = sum(a["cost"]["expected_loss_if_ignored_usd"]
                                          for a in assets if a["cost"] and not a["covered"])
    if include_basis:
        out["cost_basis"] = costs.explain(basis)
    return out


def assets_list(db, tier_filter: str | None = None, limit: int = 100) -> list[dict]:
    sql = (f"SELECT a.asset_id, a.asset_type, a.site_id, s.name AS site_name, s.region_id, s.lat, s.lon, "
           f"s.revenue_per_day_usd, s.has_refrigeration, s.type AS site_type, s.city, s.square_feet, "
           f"r.prob, r.tier, r.drivers, r.scored_at, w.id AS open_work_order "
           f"FROM assets a JOIN sites s USING (site_id) LEFT JOIN ({LATEST_RISK}) r USING (asset_id) "
           f"LEFT JOIN work_orders w ON w.asset_id = a.asset_id AND w.status IN ('pending_approval','approved') ")
    params: list = []
    if tier_filter:
        sql += "WHERE r.tier = %s "
        params.append(tier_filter)
    sql += "ORDER BY r.prob DESC NULLS LAST LIMIT %s"
    params.append(limit)
    return _with_cost(db, _clean(db.query(sql, params)))


def asset_detail(db, asset_id: str) -> dict | None:
    store = Store(db)
    a = db.query("SELECT a.*, s.name AS site_name, s.region_id, s.type AS site_type, s.city, s.square_feet, "
                 "s.open_hours, s.revenue_per_day_usd, s.has_refrigeration "
                 "FROM assets a JOIN sites s USING (site_id) "
                 "WHERE asset_id = %s", (asset_id,))
    if not a:
        return None
    facts = store.features(asset_id)[asset_id]
    r = score_asset(facts, store.today)
    asset = _clean(a)[0]
    site_money = (asset.pop("revenue_per_day_usd"), asset.pop("has_refrigeration"))
    return {
        "asset": asset,
        "risk_now": {"prob": r.prob, "base_prob": r.base_prob, "multiplier": r.hazard_multiplier,
                     "tier": "unknown" if r.insufficient_data else tier(r.prob, HIGH, MEDIUM),
                     "drivers": r.drivers, "age_years": r.age_years},
        "cost": None if r.insufficient_data else costs.compare(costs.cost_basis(db), asset["asset_type"], r.prob, *site_money),
        "risk_history": _clean(db.query("SELECT scored_at, prob, tier FROM risk_scores WHERE asset_id = %s "
                                        "ORDER BY scored_at DESC LIMIT 60", (asset_id,))),
        "work_order_history": store.recent_work_orders(asset_id, n=20),
        "forecast": store.forecast(facts.site_id),
        "field_reports": [f for f in store.field_reports(facts.site_id, 20) if f["asset_id"] in (asset_id, None)],
        "work_orders": _clean(db.query("SELECT * FROM work_orders WHERE asset_id = %s ORDER BY created_at DESC",
                                       (asset_id,))),
    }


def work_orders(db, status: str | None = None) -> list[dict]:
    sql = ("SELECT w.*, s.name AS site_name, s.region_id, s.city, a.asset_type, s.revenue_per_day_usd, "
           "s.has_refrigeration FROM work_orders w JOIN sites s USING (site_id) "
           "JOIN assets a ON a.asset_id = w.asset_id ")
    params: list = []
    if status:
        sql += "WHERE w.status = %s "
        params.append(status)
    return _with_cost(db, _clean(db.query(sql + "ORDER BY w.created_at DESC LIMIT 200", params)))


def set_work_order_status(db, wo_id: str, status: str, note: str) -> dict | None:
    store = Store(db)
    if store.get_work_order(wo_id) is None:
        return None
    store.update_work_order(wo_id, status, note)
    store.audit(None, "dashboard.update_work_order", {"id": wo_id, "status": status}, {"ok": True})
    return {"id": wo_id, "status": status}


def recent(db, table: str, limit: int = 100) -> list[dict]:
    order = {"messages": "id", "calls": "id", "dashboard_entries": "id", "agent_runs": "run_id",
             "import_quarantine": "id", "field_reports": "id"}[table]
    return _clean(db.query(f"SELECT * FROM {table} ORDER BY {order} DESC LIMIT %s", (limit,)))


def run_audit(db, run_id: int) -> list[dict]:
    return db.query("SELECT tool, args, result, created_at FROM audit_log WHERE run_id = %s ORDER BY id", (run_id,))
