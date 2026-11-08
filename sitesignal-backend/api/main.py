"""PenguinRE API (FastAPI).

    uvicorn api.main:app --reload            # local
Docs at /docs once running.

Reads are open (it's a demo dashboard); anything that changes data or sends texts
needs `Authorization: Bearer $ADMIN_TOKEN` when ADMIN_TOKEN is set.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sitesignal import importer, scheduler, services
from sitesignal.db import Database
from sitesignal.photon_inbound import handle_webhook_event, verify_signature
from sitesignal.store import Store
from sitesignal.tools import Toolbox

db: Database | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global db
    db = Database()
    from sitesignal.contacts import apply_from_env
    apply_from_env(Store(db))
    task = asyncio.create_task(scheduler.loop(db)) if os.environ.get("SCHEDULER", "on") == "on" else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="PenguinRE API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("FRONTEND_ORIGIN", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])


def admin(authorization: str | None = Header(default=None)):
    token = os.environ.get("ADMIN_TOKEN")
    if token and authorization != f"Bearer {token}":
        raise HTTPException(401, "admin token required")


def toolbox() -> Toolbox:
    return Toolbox(Store(db), dry_run=os.environ.get("PHOTON_SEND", "0") != "1")


# ---------------------------------------------------------------- reads
@app.get("/health")
def health():
    db.query("SELECT 1")
    return {"ok": True}


@app.get("/portfolio")
def portfolio():
    return services.portfolio(db)


@app.get("/savings")
def savings():
    return services.savings(db)


@app.get("/assets")
def assets(tier: Literal["high", "medium", "low"] | None = None, limit: int = 100):
    return services.assets_list(db, tier, min(limit, 500))


@app.get("/assets/{asset_id}")
def asset(asset_id: str):
    d = services.asset_detail(db, asset_id)
    if d is None:
        raise HTTPException(404, "unknown asset")
    return d


@app.get("/work-orders")
def work_orders(status: str | None = None):
    return services.work_orders(db, status)


@app.get("/dashboard")
def dashboard(limit: int = 100):
    return services.recent(db, "dashboard_entries", limit)


@app.get("/messages")
def messages(limit: int = 100):
    return services.recent(db, "messages", limit)


@app.get("/calls")
def calls(limit: int = 100):
    return services.recent(db, "calls", limit)


@app.get("/inbound")
def inbound(limit: int = 100):
    """Texts received from managers and what PenguinRE did with each (result)."""
    return services.recent(db, "inbound_messages", limit)


@app.get("/field-reports")
def field_reports(limit: int = 100):
    return services.recent(db, "field_reports", limit)


@app.get("/runs")
def runs(limit: int = 20):
    return services.recent(db, "agent_runs", limit)


@app.get("/runs/{run_id}/audit")
def run_audit(run_id: int):
    return services.run_audit(db, run_id)


@app.get("/import/quarantine")
def quarantine(limit: int = 100):
    return services.recent(db, "import_quarantine", limit)


# ---------------------------------------------------------------- actions (admin)
class StatusBody(BaseModel):
    note: str = ""


@app.post("/work-orders/{wo_id}/approve", dependencies=[Depends(admin)])
def approve(wo_id: str, body: StatusBody):
    r = services.set_work_order_status(db, wo_id, "approved", body.note or "approved on dashboard")
    if r is None:
        raise HTTPException(404, "unknown work order")
    return r


@app.post("/work-orders/{wo_id}/hold", dependencies=[Depends(admin)])
def hold(wo_id: str, body: StatusBody):
    r = services.set_work_order_status(db, wo_id, "on_hold", body.note or "held on dashboard")
    if r is None:
        raise HTTPException(404, "unknown work order")
    return r


@app.post("/jobs/sweep", dependencies=[Depends(admin)])
def sweep(background: BackgroundTasks):
    background.add_task(services.run_sweep, db)
    return {"started": True, "check": "/runs"}


@app.post("/jobs/call-urgent", dependencies=[Depends(admin)])
def call_urgent_job():
    return services.call_urgent(db)


class PhoneBody(BaseModel):
    phone: str


@app.post("/contacts/worker/{region_id}/phone", dependencies=[Depends(admin)])
def set_worker_phone(region_id: str, body: PhoneBody):
    from sitesignal.photon_call import valid_phone
    if not valid_phone(body.phone):
        raise HTTPException(422, "phone must be E.164, e.g. +12145550123")
    if not Store(db).set_contact_phone("worker", region_id, body.phone):
        raise HTTPException(404, "no service worker for that region; they must text JOIN tech <region> first")
    return {"region": region_id, "phone_set": True}


@app.post("/jobs/weather", dependencies=[Depends(admin)])
def weather_job():
    return services.refresh_weather(db)


@app.post("/jobs/score", dependencies=[Depends(admin)])
def score_job():
    return {"scored": services.score_all(db)}


@app.post("/import/{kind}", dependencies=[Depends(admin)])
async def import_file(kind: Literal["sites", "assets", "work_orders"], file: UploadFile = File(...)):
    data = await file.read()
    result = await asyncio.to_thread(importer.import_file, db, kind, data, file.filename or "upload.csv")
    await asyncio.to_thread(services.score_all, db)  # new data -> fresh risk numbers
    return result


@app.post("/admin/reset-demo", dependencies=[Depends(admin)])
def reset_demo():
    import sys
    from datetime import date
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from seed import seed
    seed(db, date.today())
    return {"seeded": True, "scored": services.score_all(db)}


# ---------------------------------------------------------------- Photon inbound
@app.post("/photon/webhook")
async def photon_webhook(request: Request, background: BackgroundTasks):
    body = await request.body()
    secret = os.environ.get("PHOTON_WEBHOOK_SECRET")
    if secret:
        h = request.headers
        if not verify_signature(secret, h.get("webhook-id", ""), h.get("webhook-timestamp", ""),
                                body, h.get("webhook-signature", "")):
            raise HTTPException(401, "bad signature")
    elif os.environ.get("ALLOW_UNSIGNED_WEBHOOKS") != "1":
        raise HTTPException(503, "PHOTON_WEBHOOK_SECRET not configured")
    event = json.loads(body or b"{}")
    background.add_task(handle_webhook_event, toolbox(), event)  # reply fast; agent runs after
    return {"received": True}


class SimulatedText(BaseModel):
    text: str
    space_id: str | None = None  # default: the managers' conversation, as if texted from their phone
    agent_id: str = "demo-agent"


@app.post("/photon/simulate", dependencies=[Depends(admin)])
def simulate_text(msg: SimulatedText):
    """Pretend a text arrived (for demos without a phone). Same path as the webhook."""
    import uuid
    from sitesignal.photon_inbound import process_message
    space_id = msg.space_id
    if space_id is None:
        rows = db.query("SELECT space_id FROM contacts WHERE kind = 'region' ORDER BY key LIMIT 1")
        if not rows:
            raise HTTPException(409, "no manager registered yet; text the Photon number from MANAGER_PHONE first")
        space_id = rows[0]["space_id"]
    return process_message(toolbox(), {"id": f"sim-{uuid.uuid4()}", "text": msg.text,
                                       "space_id": space_id, "agent_id": msg.agent_id})
