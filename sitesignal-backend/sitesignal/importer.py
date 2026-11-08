"""Data import: load real (messy) CSV or Excel exports into Postgres.

Three file kinds, matching the model's inputs (column names are matched loosely):
  sites        site_id, name, type, city, region, has_refrigeration, revenue_per_day, lat, lon
  assets       asset_id, site_id, asset_type, install_date, make, model, serial_number, refrigerant
  work_orders  asset_id (or asset name + site), date, kind, issue, refrigerant_added, cost, note

Every row is cleaned and validated. Rows that can't be trusted go to import_quarantine
with a reason instead of silently entering the model.
"""
from __future__ import annotations

import difflib
import io
import json
import re
from datetime import date, datetime

import pandas as pd

ALIASES = {
    "site_id": ["site_id", "site", "store_id", "store", "location_id", "site number", "store number"],
    "name": ["name", "site_name", "store_name", "location"],
    "type": ["type", "site_type", "category"],
    "city": ["city", "town"],
    "region": ["region", "region_id", "market", "area"],
    "has_refrigeration": ["has_refrigeration", "refrigeration", "refrigerated", "has_cooler"],
    "revenue_per_day": ["revenue_per_day", "revenue_per_day_usd", "daily_revenue", "revenue"],
    "lat": ["lat", "latitude"],
    "lon": ["lon", "lng", "long", "longitude"],
    "asset_id": ["asset_id", "asset", "asset_tag", "equipment_id", "tag", "unit"],
    "asset_type": ["asset_type", "equipment_type", "equipment", "unit_type", "type"],
    "install_date": ["install_date", "installed", "install", "installation_date", "in_service"],
    "make": ["make", "manufacturer", "brand"],
    "model": ["model", "model_number"],
    "serial_number": ["serial_number", "serial", "serial_no", "sn"],
    "refrigerant": ["refrigerant", "refrigerant_type"],
    "date": ["date", "opened_at", "opened", "created", "work_date", "service_date"],
    "kind": ["kind", "work_type", "wo_type", "priority", "category"],
    "issue": ["issue", "issue_category", "problem", "description", "summary"],
    "refrigerant_added": ["refrigerant_added", "recharge", "refrigerant_charge", "added_refrigerant"],
    "cost": ["cost", "cost_usd", "amount", "total", "invoice_total"],
    "note": ["note", "notes", "technician_note", "tech_notes", "comments", "resolution"],
}

ASSET_TYPES = {
    "rtu": ["rtu", "rooftop", "roof top", "packaged unit", "package unit", "ac", "a/c", "hvac"],
    "walk_in_cooler": ["walk-in", "walk in", "cooler", "freezer", "refrigeration"],
    "water_heater": ["water heater", "wh", "boiler", "hot water"],
}
KINDS = {
    "emergency": ["emergency", "urgent", "after hours", "p1", "critical"],
    "pm": ["pm", "preventive", "preventative", "maintenance", "filter", "scheduled"],
    "inspection": ["inspection", "inspect", "survey"],
    "repair": ["repair", "service", "corrective", "call", "breakdown"],
}
ISSUES = {
    "not_cooling": ["warm", "not cool", "no cool", "not cold", "blowing hot"],
    "leak": ["leak", "low charge", "low refrigerant"],
    "short_cycling": ["short cycl", "cycling"],
    "capacitor": ["capacitor"],
    "compressor": ["compressor"],
    "cooler_temp": ["not holding temp", "cooler temp", "temp high"],
    "noise": ["noise", "loud", "rattl", "squeal"],
    "water": ["water", "drain", "drip"],
}
RECHARGE_WORDS = re.compile(r"\b(added|topped off|top off|recharg\w*|charged)\b.*refrig|refrig\w*\s+(added|charge)", re.I)


# ---------------------------------------------------------------- helpers
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9/]+", " ", str(s).lower()).strip()


def _map_columns(df: pd.DataFrame, wanted: list[str]) -> dict[str, str]:
    cols = {_norm(c).replace(" ", "_"): c for c in df.columns}
    out = {}
    for field in wanted:
        for alias in ALIASES[field]:
            a = _norm(alias).replace(" ", "_")
            if a in cols and cols[a] not in out.values():
                out[field] = cols[a]
                break
    return out


def _lookup(text, table: dict[str, list[str]]) -> str | None:
    t = _norm(text or "")
    for key, words in table.items():
        if any(w in t for w in words):
            return key
    return None


def _date(v) -> date | None:
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return None
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if re.fullmatch(r"\d{4}", s):                       # year only -> mid-year, mark estimated
        return date(int(s), 7, 1)
    ts = pd.to_datetime(s, errors="coerce")
    return None if pd.isna(ts) else ts.date()


def _bool(v) -> bool | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return _norm(v) in ("y", "yes", "true", "1", "x", "t")


def _num(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(s)
    except ValueError:
        return None


def _val(row, cmap, field):
    col = cmap.get(field)
    if col is None:
        return None
    v = row[col]
    if (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and not v.strip()):
        return None
    return v


def read_table(data: bytes, filename: str) -> pd.DataFrame:
    if filename.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(data))  # needs openpyxl
    return pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)


def _quarantine(db, source, kind, i, row, reason):
    db.query("INSERT INTO import_quarantine (source, kind, row_number, raw, reason) "
             "VALUES (%s, %s, %s, %s::jsonb, %s)",
             (source, kind, i, json.dumps({str(k): str(v) for k, v in row.items()}), reason))


# ---------------------------------------------------------------- importers
def import_sites(db, df: pd.DataFrame, source: str) -> dict:
    cmap = _map_columns(df, ["site_id", "name", "type", "city", "region", "has_refrigeration",
                             "revenue_per_day", "lat", "lon"])
    ok = bad = 0
    for i, row in df.iterrows():
        sid, region = _val(row, cmap, "site_id"), _val(row, cmap, "region")
        if not sid or not region:
            _quarantine(db, source, "sites", i + 2, row, "missing site_id or region")
            bad += 1
            continue
        sid, region = str(sid).strip().upper(), _norm(region).replace(" ", "_")
        db.query("INSERT INTO regions (region_id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                 (region, region.replace("_", " ").title()))
        db.query(
            "INSERT INTO sites (site_id, name, type, city, region_id, has_refrigeration, revenue_per_day_usd, lat, lon) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (site_id) DO UPDATE SET "
            "name = EXCLUDED.name, type = EXCLUDED.type, city = EXCLUDED.city, region_id = EXCLUDED.region_id, "
            "has_refrigeration = EXCLUDED.has_refrigeration, revenue_per_day_usd = EXCLUDED.revenue_per_day_usd, "
            "lat = COALESCE(EXCLUDED.lat, sites.lat), lon = COALESCE(EXCLUDED.lon, sites.lon)",
            (sid, str(_val(row, cmap, "name") or sid), str(_val(row, cmap, "type") or "retail").lower(),
             _val(row, cmap, "city"), region, bool(_bool(_val(row, cmap, "has_refrigeration"))),
             _num(_val(row, cmap, "revenue_per_day")), _num(_val(row, cmap, "lat")), _num(_val(row, cmap, "lon"))))
        ok += 1
    return {"kind": "sites", "imported": ok, "quarantined": bad, "columns_matched": cmap}


def import_assets(db, df: pd.DataFrame, source: str) -> dict:
    cmap = _map_columns(df, ["asset_id", "site_id", "asset_type", "install_date", "make", "model",
                             "serial_number", "refrigerant"])
    sites = {r["site_id"] for r in db.query("SELECT site_id FROM sites")}
    ok = bad = 0
    for i, row in df.iterrows():
        aid, sid = _val(row, cmap, "asset_id"), _val(row, cmap, "site_id")
        typ = _lookup(_val(row, cmap, "asset_type"), ASSET_TYPES)
        sid = str(sid).strip().upper() if sid else None
        reason = ("missing asset_id" if not aid else "unknown site_id" if sid not in sites
                  else "unrecognised asset_type" if typ is None else None)
        if reason:
            _quarantine(db, source, "assets", i + 2, row, reason)
            bad += 1
            continue
        raw_install = _val(row, cmap, "install_date")
        install = _date(raw_install)
        estimated = bool(raw_install and re.fullmatch(r"\s*\d{4}\s*", str(raw_install)))
        db.query(
            "INSERT INTO assets (asset_id, site_id, asset_type, install_date, install_date_estimated, make, model, "
            "serial_number, refrigerant) VALUES (%s, %s, %s, %s::date, %s, %s, %s, %s, %s) "
            "ON CONFLICT (asset_id) DO UPDATE SET site_id = EXCLUDED.site_id, asset_type = EXCLUDED.asset_type, "
            "install_date = COALESCE(EXCLUDED.install_date, assets.install_date), "
            "install_date_estimated = EXCLUDED.install_date_estimated, make = EXCLUDED.make, "
            "model = EXCLUDED.model, serial_number = EXCLUDED.serial_number, refrigerant = EXCLUDED.refrigerant",
            (str(aid).strip().upper(), sid, typ, install.isoformat() if install else None, estimated,
             _val(row, cmap, "make"), _val(row, cmap, "model"), _val(row, cmap, "serial_number"),
             _val(row, cmap, "refrigerant")))
        ok += 1
    return {"kind": "assets", "imported": ok, "quarantined": bad, "columns_matched": cmap}


def _match_asset(raw: str, site_id: str | None, assets: dict[str, str]) -> tuple[str | None, float]:
    """Exact id first, then fuzzy match within the site (or all assets)."""
    key = str(raw).strip().upper()
    if key in assets:
        return key, 1.0
    squash = lambda x: re.sub(r"[^A-Z0-9]", "", x.upper())  # "D101 RTU 1" == "D101-RTU1"
    pool = [a for a, s in assets.items() if not site_id or s == site_id]
    for a in pool:
        if squash(a) == squash(key):
            return a, 0.99
    best = difflib.get_close_matches(key, pool, n=1, cutoff=0.0)
    if not best:
        return None, 0.0
    return best[0], difflib.SequenceMatcher(None, squash(key), squash(best[0])).ratio()


def import_work_orders(db, df: pd.DataFrame, source: str, min_match: float = 0.85) -> dict:
    cmap = _map_columns(df, ["asset_id", "site_id", "date", "kind", "issue", "refrigerant_added", "cost", "note"])
    assets = {r["asset_id"]: r["site_id"] for r in db.query("SELECT asset_id, site_id FROM assets")}
    ok = bad = fuzzy = 0
    for i, row in df.iterrows():
        raw_asset, opened = _val(row, cmap, "asset_id"), _date(_val(row, cmap, "date"))
        sid = _val(row, cmap, "site_id")
        sid = str(sid).strip().upper() if sid else None
        if not raw_asset or opened is None:
            _quarantine(db, source, "work_orders", i + 2, row, "missing asset or date")
            bad += 1
            continue
        if opened > date.today():
            _quarantine(db, source, "work_orders", i + 2, row, "date in the future")
            bad += 1
            continue
        aid, score = _match_asset(raw_asset, sid, assets)
        if aid is None or score < min_match:
            _quarantine(db, source, "work_orders", i + 2, row,
                        f"no confident asset match for '{raw_asset}' (best {aid}, score {score:.2f})")
            bad += 1
            continue
        fuzzy += score < 1.0
        note = str(_val(row, cmap, "note") or "")
        issue_text = f"{_val(row, cmap, 'issue') or ''} {note}"
        recharge = _bool(_val(row, cmap, "refrigerant_added"))
        if recharge is None:
            recharge = bool(RECHARGE_WORDS.search(note))
        kind = _lookup(_val(row, cmap, "kind"), KINDS) or _lookup(issue_text, KINDS) or "repair"
        cost = _num(_val(row, cmap, "cost"))
        if cost is not None and cost < 0:
            cost = None  # impossible value; keep the row, drop the field
        dup = db.query("SELECT 1 FROM work_order_history WHERE asset_id = %s AND opened_at = %s::date "
                       "AND kind = %s AND coalesce(note, '') = %s",
                       (aid, opened.isoformat(), kind, note[:2000]))
        if dup:
            continue  # same work order exported twice
        db.query("INSERT INTO work_order_history (asset_id, opened_at, kind, issue_category, refrigerant_added, "
                 "cost_usd, note, source) VALUES (%s, %s::date, %s, %s, %s, %s, %s, %s)",
                 (aid, opened.isoformat(), kind, _lookup(issue_text, ISSUES), recharge, cost, note[:2000], source))
        ok += 1
    return {"kind": "work_orders", "imported": ok, "quarantined": bad, "fuzzy_matched": fuzzy,
            "columns_matched": cmap}


IMPORTERS = {"sites": import_sites, "assets": import_assets, "work_orders": import_work_orders}


def import_file(db, kind: str, data: bytes, filename: str) -> dict:
    if kind not in IMPORTERS:
        raise ValueError(f"kind must be one of {list(IMPORTERS)}")
    return IMPORTERS[kind](db, read_table(data, filename), filename)
