"""Photon inbound listener, two ways in (use one):

1. Webhook (recommended when deployed): Photon POSTs each event to /photon/webhook.
   Verified with Standard Webhooks: HMAC-SHA256 over "webhook-id.webhook-timestamp.body"
   with the base64-decoded `whsec_...` secret (PHOTON_WEBHOOK_SECRET).
2. Poller (works anywhere, even on a laptop): `python -m sitesignal.jobs poll` calls
   Photon's documented list_messages(direction="received") every few seconds.

Either way, each message is deduplicated by id in inbound_messages, then passed to
inbound.handle_inbound(). Photon's docs don't print the inbound event's JSON fields, so
`extract_message` reads them tolerantly (names seen in Photon's message objects:
id, space.id, receivedBy.id, sentBy.kind, content.text). Log one real event and adjust.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone

from .inbound import handle_inbound
from .tools import Toolbox


# ---------------------------------------------------------------- webhook signature
def verify_signature(secret: str, msg_id: str, timestamp: str, body: bytes, signature_header: str,
                     tolerance_s: int = 300) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts) > tolerance_s:
        return False
    key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    signed = f"{msg_id}.{timestamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in (signature_header or "").split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return True
    return False


# ---------------------------------------------------------------- message parsing
def _get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def extract_message(event: dict) -> dict | None:
    """Return {id, text, space_id, agent_id} for a human-sent text message, else None."""
    msg = event
    for key in ("data", "message", "object", "payload"):
        if isinstance(msg.get(key), dict):
            msg = msg[key]
            break
    if (_get(msg, "sentBy", "kind") or "human") != "human":
        return None  # our own outbound messages echo back as events
    if msg.get("direction") not in (None, "received", "inbound"):
        return None
    text = _get(msg, "content", "text") or msg.get("text")
    space_id = _get(msg, "space", "id") or msg.get("spaceId")
    agent_id = _get(msg, "receivedBy", "id") or msg.get("receivedById")
    msg_id = msg.get("id") or event.get("id")
    if not (text and space_id and msg_id):
        return None
    return {"id": str(msg_id), "text": str(text), "space_id": str(space_id), "agent_id": str(agent_id or "")}


def process_message(tb: Toolbox, m: dict, brain=None) -> dict:
    db = tb.s.db
    claimed = db.query("INSERT INTO inbound_messages (message_id, space_id, text) VALUES (%s, %s, %s) "
                       "ON CONFLICT DO NOTHING RETURNING message_id", (m["id"], m["space_id"], m["text"]))
    if not claimed:
        return {"skipped": "already processed"}
    result = handle_inbound(m["text"], m["space_id"], m["agent_id"], tb, brain)
    db.query("UPDATE inbound_messages SET result = %s::jsonb WHERE message_id = %s",
             (json.dumps(result, default=str), m["id"]))
    return result


def handle_webhook_event(tb: Toolbox, event: dict, brain=None) -> dict:
    m = extract_message(event)
    if m is None:
        return {"ignored": "not an inbound human text"}
    return process_message(tb, m, brain)


# ---------------------------------------------------------------- poller
def _list_received(since_iso: str, page_token: str | None):
    """VERIFY argument names against Photon's Beta SDK (documented query params:
    createdSince, direction, order, pageSize, platform, nextPageToken)."""
    from photon_api import Photon  # pip install photonhq-api

    photon = Photon(api_key=os.environ["PHOTON_API_KEY"])
    kwargs = dict(createdSince=since_iso, direction="received", order="asc", pageSize=100,
                  platform=os.environ.get("PHOTON_PLATFORM", "imessage"))
    if page_token:
        kwargs["pageToken"] = page_token
    page = photon.projects.list_messages(os.environ["PHOTON_PROJECT_ID"], **kwargs)
    data = page.model_dump() if hasattr(page, "model_dump") else page
    return data.get("messages") or data.get("data") or [], data.get("nextPageToken")


def poll_forever(tb: Toolbox, interval_s: float = 2.0, brain=None) -> None:
    db = tb.s.db
    row = db.query("SELECT value FROM job_state WHERE name = 'photon_cursor'")
    since = row[0]["value"] if row else datetime.now(timezone.utc).isoformat()
    print(f"Polling Photon for messages since {since} ...")
    while True:
        try:
            token = None
            while True:
                messages, token = _list_received(since, token)
                for raw in messages:
                    m = extract_message(raw)
                    if m:
                        print(m["text"][:80], "->", process_message(tb, m, brain))
                    created = raw.get("createdAt")
                    if created and str(created) > since:
                        since = str(created)
                if not token:
                    break
            db.query("INSERT INTO job_state (name, value) VALUES ('photon_cursor', %s) "
                     "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (since,))
        except Exception as e:
            print("poll error:", e)
        time.sleep(interval_s)
