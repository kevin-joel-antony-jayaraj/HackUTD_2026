"""Hardcoded demo contacts from .env: one manager phone and one service worker phone for every region.

    MANAGER_PHONE=+1...         gets every work-order text, approves, reports problems
    SERVICE_WORKER_PHONE=+1...  gets the urgent calls

Applied at API startup and after seeding. The manager's Photon conversation is opened on the
first real text (photon_notify), so no one has to text JOIN first; until then the contact's
space_id is a "pending:" placeholder.
"""
from __future__ import annotations

import os
import re

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
PENDING = "pending:"


def apply_from_env(store) -> dict:
    regions = [r["region_id"] for r in store.db.query("SELECT region_id FROM regions")]
    out = {}
    for env, kind, label in (("MANAGER_PHONE", "region", "manager"),
                             ("SERVICE_WORKER_PHONE", "worker", "service worker")):
        phone = os.environ.get(env, "").strip()
        if not phone:
            continue
        if not E164.match(phone):
            print(f"{env} ignored: must be E.164, e.g. +12145550123")
            continue
        space = PENDING + phone if kind == "region" else "call-only"
        for region in regions:
            store.upsert_phone_contact(kind, region, f"{region.title()} {label}", phone, space,
                                       os.environ.get("PHOTON_AGENT_USER_ID", ""))
        out[kind] = len(regions)
    return out
