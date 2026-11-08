"""Scheduler: does the routine work so nobody has to press buttons.

Every day at SWEEP_TIME (Central time, default 06:00) it:
  1. refreshes the weather forecast for every site (Open-Meteo)
  2. runs the agent sweep (score all assets -> work orders -> Photon texts)
Plus every SCORE_EVERY_MIN minutes it rescores all assets (no agent, no texts) so the
dashboard's risk numbers stay current as new work orders and field reports arrive.

Runs inside the API process (SCHEDULER=on). Uses job_state so a restart doesn't
re-run today's sweep.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from . import services

TZ = ZoneInfo(os.environ.get("SCHEDULER_TZ", "America/Chicago"))
SWEEP_TIME = os.environ.get("SWEEP_TIME", "06:00")
SCORE_EVERY_MIN = int(os.environ.get("SCORE_EVERY_MIN", 30))


def _state(db, name):
    r = db.query("SELECT value FROM job_state WHERE name = %s", (name,))
    return r[0]["value"] if r else None


def _set_state(db, name, value):
    db.query("INSERT INTO job_state (name, value) VALUES (%s, %s) ON CONFLICT (name) "
             "DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (name, value))


def tick(db, now: datetime | None = None) -> list[str]:
    """One scheduler check. Returns the jobs it ran (testable without waiting)."""
    now = now or datetime.now(TZ)
    ran = []
    today = now.date().isoformat()
    if now.strftime("%H:%M") >= SWEEP_TIME and _state(db, "last_sweep_date") != today:
        _set_state(db, "last_sweep_date", today)  # set first: never double-send on a crash loop
        services.refresh_weather(db)
        services.run_sweep(db)
        ran += ["weather", "sweep"]
    last_score = _state(db, "last_score_at")
    if not last_score or (now - datetime.fromisoformat(last_score)).total_seconds() >= SCORE_EVERY_MIN * 60:
        services.score_all(db)
        _set_state(db, "last_score_at", now.isoformat())
        ran.append("score")
    return ran


async def loop(db, every_s: int = 60):
    while True:
        try:
            ran = await asyncio.to_thread(tick, db)
            if ran:
                print("scheduler ran:", ran)
        except Exception as e:
            print("scheduler error:", e)
        await asyncio.sleep(every_s)
