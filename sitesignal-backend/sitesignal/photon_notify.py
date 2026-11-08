"""Photon 2.0 messaging: the agent's only way to reach people.

Photon's Beta docs show replying into an existing conversation ("space") with
`photon.projects.send_message(...)`, using an incoming message's `space.id` and
`receivedBy.id`. Starting a brand-new conversation isn't documented, so people
register first by texting the Photon number:
    "JOIN dallas"      -> manager for the dallas region (texts, approvals, reports)
    "JOIN tech dallas" -> service worker for the dallas region (gets the urgent calls)
    "JOIN site S010"   -> site manager for site S010
Their conversation ids are stored in the `contacts` table (no phone numbers).

Uses the photonhq-api SDK (0.6): projects.send_message / create_or_get_user /
create_or_get_space, each taking one validated input model.
"""
from __future__ import annotations

import os
import uuid

from .contacts import PENDING


def client():
    from photon_api import Photon  # pip install photonhq-api
    return Photon(headers={"Authorization": f"Bearer {os.environ['PHOTON_API_KEY']}"})


def platform() -> str:
    return os.environ.get("PHOTON_PLATFORM", "imessage")


def _send_text(space_id: str, agent_id: str, text: str) -> None:
    from photon_api import rpc_generated as R
    client().projects.send_message(R.SendMessageInput.model_validate({
        "path": {"id": os.environ["PHOTON_PROJECT_ID"]},
        # a fresh key per send: our messages table already dedupes, and reused work-order ids after a
        # demo reset must not be swallowed by Photon's idempotency cache
        "headers": {"idempotency-key": str(uuid.uuid4())},
        "body": {"spaceId": space_id, "senderId": agent_id,
                 "content": {"type": "text", "format": "plain", "text": text}},
    }))


def _id(result) -> str:
    return result.model_dump(mode="json", by_alias=True)["id"]  # results are nested root models


def _open_conversation(phone: str) -> tuple[str, str]:
    """Start a conversation with a phone number that hasn't texted us (MANAGER_PHONE).
    Fallback if it fails: that phone texts "JOIN all" to the Photon number once."""
    from photon_api import rpc_generated as R
    projects, pid = client().projects, os.environ["PHOTON_PROJECT_ID"]
    agent_id = os.environ["PHOTON_AGENT_USER_ID"]  # the agent User for your line (Photon dashboard / list_users)
    user = projects.create_or_get_user(R.CreateOrGetUserInput.model_validate(
        {"path": {"id": pid}, "body": {"handle": phone, "platform": platform()}}))
    space = projects.create_or_get_space(R.CreateOrGetSpaceInput.model_validate(
        {"path": {"id": pid}, "headers": {"idempotency-key": str(uuid.uuid4())},
         "body": {"participantIds": [agent_id, _id(user)], "platform": platform()}}))
    return _id(space), agent_id


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
            _send_text(contact["space_id"], contact["agent_id"], text)
            res = {"sent": True, "to": contact["name"]}
        except Exception as e:  # log and let the agent see the failure
            reason = f"send failed: {e}"
            if "SPACE_REQUIRES_OWNED_AGENT" in str(e):
                reason = ("Photon's shared line can't start a conversation: text anything from "
                          f"{contact['phone']} to the Photon number first")
            res = {"sent": False, "to": contact["name"], "reason": reason}
    store.log_message(kind=kind, to_key=key, recipient_name=res.get("to"), text=text,
                      sent=res.get("sent", False), dry_run=res.get("dry_run", False),
                      error=res.get("reason"), idempotency_key=idempotency_key)
    return res
