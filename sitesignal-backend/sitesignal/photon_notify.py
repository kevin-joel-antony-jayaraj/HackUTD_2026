"""Photon 2.0 messaging: the agent's only way to reach people.

Photon's Beta docs show replying into an existing conversation ("space") with
`photon.projects.send_message(...)`, using an incoming message's `space.id` and
`receivedBy.id`. Starting a brand-new conversation isn't documented, so people
register first by texting the Photon number:
    "JOIN dallas"      -> manager for the dallas region (texts, approvals, reports)
    "JOIN tech dallas" -> service worker for the dallas region (gets the urgent calls)
    "JOIN site S010"   -> site manager for site S010
Their conversation ids are stored in the `contacts` table (no phone numbers).

VERIFY the send_message argument names against Photon's Beta SDK reference
before the demo. They are isolated in `_send_text`.
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid

from .contacts import PENDING


def _send_text(space_id: str, agent_id: str, text: str, idempotency_key: str) -> None:
    from photon_api import Photon  # pip install photonhq-api
    from photon_api.models import SendMessageHeader, SendTextContent  # verify import path

    photon = Photon(api_key=os.environ["PHOTON_API_KEY"])  # verify constructor
    photon.projects.send_message(
        os.environ["PHOTON_PROJECT_ID"],
        spaceId=space_id,
        senderId=agent_id,
        content=SendTextContent(type="text", format="plain", text=text),
        headers=SendMessageHeader.model_validate({"idempotency-key": idempotency_key}),
    )


def _photon_post(path: str, body: dict) -> dict:
    url = f"https://api.photon.codes/v1/projects/{os.environ['PHOTON_PROJECT_ID']}{path}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {os.environ['PHOTON_API_KEY']}", "Content-Type": "application/json",
        "idempotency-key": str(uuid.uuid4())})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _open_conversation(phone: str) -> tuple[str, str]:
    """Start a conversation with a phone number that hasn't texted us (MANAGER_PHONE).
    Shapes from Photon's 2.0 launch blog example; VERIFY against the API reference.
    Fallback if it fails: that phone texts "JOIN all" to the Photon number once."""
    platform = os.environ.get("PHOTON_PLATFORM", "sms")
    agent_id = os.environ["PHOTON_AGENT_USER_ID"]  # your agent's User on this platform (Photon dashboard)
    user = _photon_post("/users", {"platform": platform, "handle": phone})
    space = _photon_post("/spaces", {"platform": platform, "participantIds": [agent_id, user["id"]]})
    return space["id"], agent_id


def send(store, kind: str, key: str, text: str, idempotency_key: str, dry_run: bool = True) -> dict:
    """kind = 'region' (manager) or 'site' (site contact)."""
    if store.message_exists(idempotency_key):
        return {"sent": False, "reason": "duplicate: this message was already sent"}
    contact = store.contact(kind, key or "")
    if contact is None:
        res = {"sent": False, "reason": f"no registered {kind} contact for '{key}'"}
    elif dry_run:
        res = {"sent": False, "dry_run": True, "to": contact["name"], "text": text}
    else:
        try:
            if contact["space_id"].startswith(PENDING):
                space_id, agent_id = _open_conversation(contact["phone"])
                store.set_space_for_phone(contact["phone"], space_id, agent_id)
                contact = {**contact, "space_id": space_id, "agent_id": agent_id}
            _send_text(contact["space_id"], contact["agent_id"], text, idempotency_key)
            res = {"sent": True, "to": contact["name"]}
        except Exception as e:  # log and let the agent see the failure
            res = {"sent": False, "to": contact["name"], "reason": f"send failed: {e}"}
    store.log_message(kind=kind, to_key=key, recipient_name=res.get("to"), text=text,
                      sent=res.get("sent", False), dry_run=res.get("dry_run", False),
                      error=res.get("reason"), idempotency_key=idempotency_key)
    return res
