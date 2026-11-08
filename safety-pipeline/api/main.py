"""SiteSignal Safety API: upload messy data, run the whole pipeline, read results, approve decisions.

    cd safety-pipeline
    uvicorn api.main:app --port 8000          # demo page at http://localhost:8000, docs at /docs

Needs DATABASE_URL and NVIDIA_API_KEY (both read from safety-pipeline/.env). The NVIDIA key stays on
this server: browsers never see it. Without the key, uploads cannot be extracted, but everything already
in the database can still be scored, and decisions fall back to the rule-based defaults.

Pipeline (POST /pipeline/run, runs in the background, poll GET /pipeline/jobs/{id}):
  1. extract   Nemotron reads each uploaded file -> validated rows (checked against what is already in the DB)
  2. load      new rows upserted into Postgres (idempotent; rejected rows kept with reasons)
  3. weather   7-day forecast per site from the National Weather Service
  4. score     Safety Index for the WHOLE portfolio (not only the new rows)
  5. decide    Nemotron proposes actions, code enforces rules; stored as pending_review
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from sitesignal import decide as decide_mod
from sitesignal import extract, fast_extract, load_pg, safety_index, weather_nws

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
KINDS = ("sites", "assets", "work_orders")

app = FastAPI(title="SiteSignal Safety API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("FRONTEND_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------ helpers
def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise HTTPException(500, "DATABASE_URL is not set on the server (put it in safety-pipeline/.env)")
    return url


def q(sql: str, args=()) -> list[dict]:
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [{k: _plain(v) for k, v in r.items()} for r in rows]


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def admin(authorization: str | None = Header(default=None)):
    """Write endpoints need `Authorization: Bearer <ADMIN_TOKEN>` only if ADMIN_TOKEN is set (open for local demos)."""
    token = os.environ.get("ADMIN_TOKEN")
    if token and authorization != f"Bearer {token}":
        raise HTTPException(401, "missing or wrong admin token")


_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False


def ensure_schema():
    """Apply the schema once per process. It recreates a view, so running it while other requests read
    that view deadlocks; never call it per request."""
    global _SCHEMA_READY
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        with psycopg.connect(dsn()) as conn:
            conn.execute((ROOT / "db" / "safety_schema.sql").read_text())
        _SCHEMA_READY = True


@app.on_event("startup")
def _startup():
    if os.environ.get("DATABASE_URL"):
        try:
            ensure_schema()
        except Exception as e:                      # the API still starts; /health reports the database error
            print(f"schema not applied at startup: {e}")


def bundle_from_db() -> dict:
    """The whole portfolio in the shape safety_index.build() expects."""
    sites = q("""SELECT site_id, name, type, city, region_id, has_refrigeration, revenue_per_day_usd, lat, lon, flags
                 FROM sites ORDER BY site_id""")
    assets = q("""SELECT asset_id, site_id, asset_type, install_date, install_date_estimated, make, model,
                         serial_number, refrigerant, pm_interval_days, flags FROM assets ORDER BY asset_id""")
    wos = q("""SELECT asset_id, opened_at, kind, issue_category, refrigerant_added, cost_usd, note, source, flags
               FROM work_order_history ORDER BY asset_id, opened_at""")
    return {"sites": sites, "assets": assets, "work_orders": wos}


# ------------------------------------------------------------------ pipeline jobs (one at a time)
JOBS: dict[str, dict] = {}
RUN_LOCK = threading.Lock()
STEPS = ["extract", "load", "weather", "score", "decide"]


def _step(job, name, **info):
    job["step"] = name
    job["steps"][name] = {"status": "running", "_t0": time.monotonic(), **info}


def _done(job, name, **info):
    t0 = job["steps"].get(name, {}).get("_t0", time.monotonic())
    job["steps"][name] = {"status": "done", "seconds": round(time.monotonic() - t0, 1), **info}


def run_pipeline(job: dict, files: dict[str, Path]):
    try:
        ensure_schema()
        new = {"sites": [], "assets": [], "work_orders": [], "rejected": [], "warnings": []}

        # 1. extract (only the kinds that were uploaded), validated against the DB as well as this upload.
        #    Tabular files: one Nemotron call per file maps the columns (all files in parallel), code converts
        #    every row. Free-text files: row-by-row reading (slower).
        _step(job, "extract", files=sorted(files))
        if files:
            if not os.environ.get("NVIDIA_API_KEY"):
                raise RuntimeError("NVIDIA_API_KEY is not set on the server, so uploaded files cannot be read.")
            db = bundle_from_db()
            tab = {k: p for k, p in files.items() if fast_extract.is_tabular(str(p))}
            with ThreadPoolExecutor(max_workers=3) as pool:
                plans = dict(zip(tab, pool.map(lambda kp: fast_extract.plan(kp[0], str(kp[1])), tab.items())))

            def read(kind, ctx=None):
                if kind in plans:
                    return fast_extract.apply(kind, str(files[kind]), plans[kind], ctx)
                return extract.extract_file(kind, str(files[kind]), ctx)

            if "sites" in files:
                r = read("sites")
                new["sites"], new["rejected"], new["warnings"] = r["rows"], r["rejected"], r["warnings"]
            known_sites = {s["site_id"] for s in db["sites"]} | {s["site_id"] for s in new["sites"]}
            if "assets" in files:
                r = read("assets")
                for a in r["rows"]:   # foreign key: an asset must belong to a known site
                    if a["site_id"] in known_sites:
                        new["assets"].append(a)
                    else:
                        new["rejected"].append({"source": files["assets"].name, "raw": a,
                                                "reason": f"unknown site_id {a['site_id']} (upload the sites file first)"})
                new["rejected"] += r["rejected"]; new["warnings"] += r["warnings"]
            if "work_orders" in files:
                known_assets = [a["asset_id"] for a in db["assets"]] + [a["asset_id"] for a in new["assets"]]
                if not known_assets:
                    raise RuntimeError("No assets known yet: upload the asset list before work orders.")
                r = read("work_orders", {"asset_ids": known_assets})
                new["work_orders"] = r["rows"]
                new["rejected"] += r["rejected"]; new["warnings"] += r["warnings"]
            job["column_mappings"] = {k: v.get("columns") for k, v in plans.items()}
        _done(job, "extract", **{k: len(v) for k, v in new.items()})

        # 2. load the new rows
        _step(job, "load")
        with psycopg.connect(dsn()) as conn:
            counts = load_pg.load(conn, new, None, None)
        _done(job, "load", **counts)

        # 3. weather for every site with coordinates (a failed site lowers confidence; it never stops the run)
        _step(job, "weather")
        portfolio = bundle_from_db()
        weather, failed = {}, []
        located = [s for s in portfolio["sites"] if s.get("lat") is not None and s.get("lon") is not None]
        fresh = q("""SELECT site_id, array_agg(max_temp_f ORDER BY forecast_date) AS temps FROM weather_forecasts
                     WHERE forecast_date >= current_date AND fetched_at > now() - interval '6 hours'
                     GROUP BY site_id HAVING count(*) >= 5""")
        for r in fresh:                                           # forecasts barely change within hours: reuse
            weather[r["site_id"]] = [float(t) for t in r["temps"]][:7]
        located = [s for s in located if s["site_id"] not in weather]

        def fetch(site):
            try:
                return site["site_id"], weather_nws.forecast_highs(site["lat"], site["lon"]), None
            except Exception as e:
                return site["site_id"], None, str(e)[:200]
        with ThreadPoolExecutor(max_workers=20) as pool:          # NWS is free and fast
            for sid, temps, err in pool.map(fetch, located):
                if err:
                    failed.append({"site_id": sid, "error": err})
                else:
                    weather[sid] = temps
        _done(job, "weather", sites=len(weather), reused=len(fresh), failed=failed)

        # 4. score the whole portfolio and store the run
        _step(job, "score")
        if not portfolio["assets"]:
            raise RuntimeError("No assets in the database yet: upload sites and assets first.")
        safety = safety_index.build(portfolio, weather)
        with psycopg.connect(dsn()) as conn:
            counts = load_pg.load(conn, {}, safety, weather)
        _done(job, "score", assets=len(safety["assets"]), sites=len(safety["sites"]), run_id=counts.get("run_id"))

        # 5. decisions (falls back to rule-based defaults if Nemotron is unavailable)
        _step(job, "decide")
        results = decide_mod.run(dsn(), None, False, 2)
        ds = [d for r in results for d in r["decisions"]]
        _done(job, "decide", decisions=len(ds), from_model=sum(d["source"] == "llm" for d in ds),
              by_rule=sum(d["source"] == "rule" for d in ds),
              rule_default=sum(d["source"] == "rule_default" for d in ds),
              runs_disagreed=sum(not d["agreement"] for d in ds))

        job.update(status="done", step=None, finished_at=datetime.now().isoformat(timespec="seconds"),
                   seconds_total=round(sum(v.get("seconds", 0) for v in job["steps"].values()), 1),
                   rejected=new["rejected"][:50], warnings=new["warnings"][:50])
    except Exception as e:
        job.update(status="failed", error=str(e), trace=traceback.format_exc()[-1500:],
                   finished_at=datetime.now().isoformat(timespec="seconds"))
        if job.get("step"):
            job["steps"][job["step"]]["status"] = "failed"
    finally:
        try:
            tmp = job.pop("_tmp", None)          # None for sample runs (nothing was uploaded)
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            RUN_LOCK.release()                    # always, or every later run would be refused


def start_job(files: dict[str, Path], tmp: str | None, label: str) -> dict:
    if not RUN_LOCK.acquire(blocking=False):
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(409, "A pipeline run is already in progress; poll /pipeline/jobs for it.")
    job_id = uuid.uuid4().hex[:10]
    job = {"id": job_id, "label": label, "status": "running", "step": None, "steps": {},
           "started_at": datetime.now().isoformat(timespec="seconds"), "_tmp": tmp}
    JOBS[job_id] = job
    threading.Thread(target=run_pipeline, args=(job, files), daemon=True).start()
    return {"job_id": job_id, "poll": f"/pipeline/jobs/{job_id}"}


# ------------------------------------------------------------------ pipeline endpoints
@app.post("/pipeline/run", dependencies=[Depends(admin)])
async def pipeline_run(sites: UploadFile | None = File(default=None), assets: UploadFile | None = File(default=None),
                       work_orders: UploadFile | None = File(default=None)):
    """Upload any of the three files (CSV, XLSX or text). Missing kinds are taken from the database.
    With no files at all, it re-scores and re-decides what is already stored."""
    tmp = tempfile.mkdtemp(prefix="sitesignal-upload-")
    files = {}
    for kind, up in (("sites", sites), ("assets", assets), ("work_orders", work_orders)):
        if up is not None and up.filename:
            safe = Path(up.filename).name or f"{kind}.csv"
            dest = Path(tmp) / f"{kind}__{safe}"
            dest.write_bytes(await up.read())
            files[kind] = dest
    return start_job(files, tmp, "upload: " + (", ".join(files) or "rescore only"))


@app.post("/pipeline/run-sample", dependencies=[Depends(admin)])
def pipeline_run_sample():
    """Run the full pipeline on the bundled 30-site demo portfolio (samples/demo; falls back to samples/)."""
    folder = SAMPLES / "demo" if (SAMPLES / "demo" / "sites.csv").exists() else SAMPLES
    return start_job({k: folder / f"{k}.csv" for k in KINDS}, None, f"sample data ({folder.name})")


@app.get("/pipeline/jobs/{job_id}")
def pipeline_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job (jobs are kept in memory and reset when the server restarts)")
    out = {k: v for k, v in job.items() if not k.startswith("_")}
    out["steps"] = {n: {k: v for k, v in st.items() if not k.startswith("_")} for n, st in job["steps"].items()}
    return out | {"step_order": STEPS}


@app.get("/pipeline/jobs")
def pipeline_jobs():
    return sorted(({k: v for k, v in j.items() if not k.startswith("_") and k not in ("trace", "steps")} for j in JOBS.values()),
                  key=lambda j: j["started_at"], reverse=True)[:20]


# ------------------------------------------------------------------ read endpoints
@app.get("/health")
def health():
    out = {"api": "ok", "nemotron_key_configured": bool(os.environ.get("NVIDIA_API_KEY")),
           "model": extract.MODEL, "database": "unknown"}
    try:
        q("SELECT 1")
        out["database"] = "ok"
    except Exception as e:
        out["database"] = f"error: {str(e)[:120]}"
    return out


@app.get("/portfolio")
def portfolio():
    ensure_schema()
    run = q("SELECT run_id, as_of, model_version, calibrated, created_at FROM latest_safety_run")
    bands = q("""SELECT band, count(*)::int AS n FROM asset_safety_scores
                 WHERE run_id = (SELECT run_id FROM latest_safety_run) GROUP BY band""")
    dec = q("""SELECT status, count(*)::int AS n FROM decisions
               WHERE run_id = (SELECT run_id FROM latest_safety_run) GROUP BY status""")
    counts = q("""SELECT (SELECT count(*) FROM sites)::int AS sites, (SELECT count(*) FROM assets)::int AS assets,
                         (SELECT count(*) FROM work_order_history)::int AS work_orders,
                         (SELECT count(*) FROM rejected_rows)::int AS rejected_rows""")[0]
    exposure = q("""SELECT coalesce(sum(st.revenue_per_day_usd), 0) AS revenue_at_risk_per_day
                    FROM site_safety_scores ss JOIN sites st USING (site_id)
                    WHERE ss.run_id = (SELECT run_id FROM latest_safety_run) AND ss.band IN ('critical','poor')""")[0]
    return {**counts, "latest_run": run[0] if run else None,
            "assets_by_band": {r["band"]: r["n"] for r in bands},
            "decisions_by_status": {r["status"]: r["n"] for r in dec},
            "revenue_per_day_at_poor_or_critical_sites_usd": exposure["revenue_at_risk_per_day"]}


@app.get("/assets")
def assets(band: str | None = None):
    return q("""SELECT w.*, s.name AS site_name, s.type AS site_type, s.lat, s.lon, s.city,
                       a.install_date, a.make, a.model, a.refrigerant,
                       (SELECT max(opened_at) FROM work_order_history h WHERE h.asset_id = w.asset_id) AS last_service_date,
                       (SELECT coalesce(sum(cost_usd), 0) FROM work_order_history h
                         WHERE h.asset_id = w.asset_id AND cost_usd > 0) AS service_spend_usd,
                       d.id AS decision_id, d.action AS decision_action, d.urgency AS decision_urgency,
                       d.status AS decision_status
                FROM asset_worklist w JOIN sites s USING (site_id) JOIN assets a USING (asset_id)
                LEFT JOIN latest_decisions d ON d.asset_id = w.asset_id""" + (" WHERE w.band = %s" if band else ""),
             (band,) if band else ())


@app.get("/service-records")
def service_records(limit: int = 500):
    """Work-order history across the portfolio, newest first (the UI's work-order screen)."""
    return q("""SELECT h.id, h.asset_id, a.site_id, a.asset_type, s.name AS site_name, s.city, h.opened_at, h.kind,
                       h.issue_category, h.refrigerant_added, h.cost_usd, h.note, h.flags
                FROM work_order_history h JOIN assets a USING (asset_id) JOIN sites s USING (site_id)
                ORDER BY h.opened_at DESC, h.id DESC LIMIT %s""", (limit,))


@app.get("/assets/{asset_id}")
def asset_detail(asset_id: str):
    a = q("SELECT a.*, s.name AS site_name FROM assets a JOIN sites s USING (site_id) WHERE asset_id = %s", (asset_id,))
    if not a:
        raise HTTPException(404, "unknown asset")
    score = q("""SELECT * FROM asset_safety_scores WHERE asset_id = %s
                 AND run_id = (SELECT run_id FROM latest_safety_run)""", (asset_id,))
    history = q("""SELECT r.created_at AS scored_at, x.safety_index, x.band, x.confidence
                   FROM asset_safety_scores x JOIN safety_runs r USING (run_id)
                   WHERE x.asset_id = %s ORDER BY r.created_at""", (asset_id,))
    wos = q("""SELECT opened_at, kind, issue_category, refrigerant_added, cost_usd, note, flags
               FROM work_order_history WHERE asset_id = %s ORDER BY opened_at DESC""", (asset_id,))
    fc = q("""SELECT forecast_date, max_temp_f FROM weather_forecasts
              WHERE site_id = %s AND forecast_date >= current_date ORDER BY forecast_date""", (a[0]["site_id"],))
    dec = q("SELECT * FROM latest_decisions WHERE asset_id = %s", (asset_id,))
    return {"asset": a[0], "score": score[0] if score else None, "score_history": history,
            "work_orders": wos, "forecast": fc, "decision": dec[0] if dec else None}


@app.get("/sites")
def sites():
    return q("""SELECT ss.*, s.name, s.type, s.city, s.lat, s.lon FROM site_safety_scores ss JOIN sites s USING (site_id)
                WHERE ss.run_id = (SELECT run_id FROM latest_safety_run) ORDER BY ss.safety_index NULLS FIRST""")


@app.get("/sites/{site_id}")
def site_detail(site_id: str):
    ctx = q("SELECT context FROM llm_site_context WHERE site_id = %s", (site_id,))
    if not ctx:
        raise HTTPException(404, "unknown site or not scored yet")
    summ = q("""SELECT summary, questions FROM decision_summaries WHERE site_id = %s
                AND run_id = (SELECT run_id FROM latest_safety_run)""", (site_id,))
    dec = q("SELECT * FROM latest_decisions WHERE site_id = %s", (site_id,))
    return {"context": ctx[0]["context"], "summary": summ[0] if summ else None, "decisions": dec}


@app.get("/decisions")
def decisions(status: str | None = None):
    return q("SELECT * FROM latest_decisions" + (" WHERE status = %s" if status else ""), (status,) if status else ())


@app.get("/rejected")
def rejected():
    return q("SELECT id, source, raw, reason, loaded_at FROM rejected_rows ORDER BY id DESC LIMIT 200")


# ------------------------------------------------------------------ actions
def _set_status(decision_id: int, status: str, allowed_from: tuple[str, ...]):
    with psycopg.connect(dsn(), row_factory=dict_row) as conn:
        row = conn.execute("SELECT status FROM decisions WHERE id = %s", (decision_id,)).fetchone()
        if not row:
            raise HTTPException(404, "unknown decision")
        if row["status"] not in allowed_from:
            raise HTTPException(409, f"decision is '{row['status']}', cannot change to '{status}'")
        conn.execute("UPDATE decisions SET status = %s WHERE id = %s", (status, decision_id))
    return {"id": decision_id, "status": status}


@app.post("/decisions/{decision_id}/approve", dependencies=[Depends(admin)])
def approve(decision_id: int):
    return _set_status(decision_id, "approved", ("pending_review", "rejected"))


@app.post("/decisions/{decision_id}/reject", dependencies=[Depends(admin)])
def reject(decision_id: int):
    return _set_status(decision_id, "rejected", ("pending_review", "approved"))


@app.post("/decisions/{decision_id}/done", dependencies=[Depends(admin)])
def mark_done(decision_id: int):
    return _set_status(decision_id, "done", ("approved",))


@app.post("/admin/reset", dependencies=[Depends(admin)])
def reset():
    """Empty every table (for demos). The schema stays."""
    if RUN_LOCK.locked():
        raise HTTPException(409, "a pipeline run is in progress")
    ensure_schema()
    with psycopg.connect(dsn()) as conn:
        conn.execute("""TRUNCATE decisions, decision_summaries, asset_safety_scores, site_safety_scores, safety_runs,
                        weather_forecasts, work_order_history, rejected_rows, assets, sites RESTART IDENTITY CASCADE""")
    return {"reset": True}


# ------------------------------------------------------------------ demo page
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")
