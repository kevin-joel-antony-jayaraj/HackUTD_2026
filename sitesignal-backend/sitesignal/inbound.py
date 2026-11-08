"""Inbound Photon messages -> database updates -> agent events.

Wire `handle_inbound` to Photon's WebSocket listener or webhook, passing the message
text plus the ids Photon gives you (space.id, receivedBy.id).
"""
from __future__ import annotations

import re

from .agent import run_agent
from .tools import Toolbox

TYPE_WORDS = {
    "rtu": re.compile(r"\b(ac|a/c|air|rooftop|rtu|hvac|cooling)\b", re.I),
    "walk_in_cooler": re.compile(r"\b(cooler|walk-?in|fridge|refrigerat\w*)\b", re.I),
    "water_heater": re.compile(r"\b(water heater|hot water|heater)\b", re.I),
}
NEGATIVE = re.compile(r"\b(n|no|not|warm|hot|broken|leak|leaking|noise|loud|dead|off)\b", re.I)


def handle_inbound(text: str, space_id: str, agent_id: str, tb: Toolbox, brain=None) -> dict:
    s, t = tb.s, text.strip()

    # 1. Registration: "JOIN dallas" or "JOIN site S010"
    if m := re.fullmatch(r"JOIN\s+site\s+(\S+)", t, re.I):
        if not s.site(m.group(1).upper()):
            return {"ignored": "unknown site"}
        s.upsert_contact("site", m.group(1), f"Site manager {m.group(1).upper()}", space_id, agent_id)
        return {"registered": "site", "key": m.group(1)}
    if m := re.fullmatch(r"JOIN\s+(\w+)", t, re.I):
        s.upsert_contact("region", m.group(1), f"{m.group(1).title()} service manager", space_id, agent_id)
        return {"registered": "region", "key": m.group(1)}

    contact = s.contact_by_space(space_id)
    if contact is None:
        return {"ignored": "sender not registered"}

    # 2. Service manager approving or holding a work order
    if contact["kind"] == "region" and (m := re.search(r"\b(APPROVE|HOLD)\s+(WO-\d+)", t, re.I)):
        return run_agent(f"EVENT service manager replied: {m.group(1).upper()} {m.group(2).upper()}", tb, brain)

    # 3. Site manager reporting on equipment (reply to a check-in, or unprompted)
    if contact["kind"] == "site":
        site_id = contact["key"].upper()
        asset_id = _which_asset(s, site_id, t)
        working = True if t.upper() in ("Y", "YES") else not NEGATIVE.search(t)
        s.add_field_report(site_id, asset_id, t, working)
        if not working and asset_id:
            return run_agent(f"EVENT field report: site {site_id} asset {asset_id} "
                             f"reported not working: {t!r}", tb, brain)
        return {"recorded": "field report", "asset_id": asset_id, "working": working}

    return {"ignored": "no action matched"}


def _which_asset(s, site_id: str, text: str) -> str | None:
    """The machine a site manager's text is about: one named by ID, else the one we last
    asked them about, else the site's only machine of the type they mention."""
    assets = s.site_assets(site_id)
    for a in assets:
        if a["asset_id"].lower() in text.lower():
            return a["asset_id"]
    if m := re.match(r"\[(\S+)\]", s.last_checkin(site_id) or ""):
        return m.group(1)
    for asset_type, words in TYPE_WORDS.items():
        if words.search(text):
            of_type = [a["asset_id"] for a in assets if a["asset_type"] == asset_type]
            if len(of_type) == 1:
                return of_type[0]
    if len(assets) == 1:
        return assets[0]["asset_id"]
    return None
