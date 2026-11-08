"""Inbound Photon messages -> database updates -> agent events.

Wire `handle_inbound` to Photon's WebSocket listener or webhook, passing the message
text plus the ids Photon gives you (space.id, receivedBy.id).
"""
from __future__ import annotations

import re

from . import photon_call
from .agent import run_agent
from .risk_model import score_asset, tier
from .tools import HIGH, MEDIUM, Toolbox

TYPE_WORDS = {  # most specific first: "the cooler isn't cooling" is about the cooler
    "walk_in_cooler": re.compile(r"\b(cooler|walk-?in|fridge|refrigerat\w*)\b", re.I),
    "water_heater": re.compile(r"\b(water heater|hot water|heater)\b", re.I),
    "rtu": re.compile(r"\b(ac|a/c|air|rooftop|rtu|hvac|cooling)\b", re.I),
}
GENERIC_NAME_WORDS = {"bank", "branch", "clinic", "family", "market", "retail", "store", "the"}
NEGATIVE = re.compile(r"\b(n|no|not|\w+n['’]t|isnt|wont|cant|doesnt|didnt|arent|wasnt|warm|broken|leak\w*|nois\w*|loud|dead|off|down|fail\w*)\b", re.I)


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

    # 3. A report about equipment: from the region's manager (who names the site) or a site contact
    if contact["kind"] == "region":
        site_id = _which_site(s, contact["key"], t)
        if site_id is None:
            return {"ignored": "couldn't tell which site the message is about; name the site or its area"}
    else:
        site_id = contact["key"].upper()
    asset_id = _which_asset(s, site_id, t)
    working = True if t.upper() in ("Y", "YES") else not NEGATIVE.search(t)
    s.add_field_report(site_id, asset_id, t, working)
    if asset_id is None:
        return {"recorded": "field report", "site_id": site_id, "asset_id": None, "working": working}
    facts = s.features(asset_id)[asset_id]
    r = score_asset(facts, s.today)
    if not r.insufficient_data:
        s.save_risk(r, tier(r.prob, HIGH, MEDIUM))  # dashboard shows the new risk right away
    if working:
        return {"recorded": "field report", "site_id": site_id, "asset_id": asset_id, "working": True}
    result = run_agent(f"EVENT field report: site {site_id} asset {asset_id} reported not working: {t!r}",
                       tb, brain)
    result["calls"] = photon_call.call_urgent(s, dry_run=True if tb.dry_run else None)
    return result


def _which_site(s, region_id: str, text: str) -> str | None:
    """The site a manager's text is about: its ID, or the most specific match on its name or city.
    Ambiguous (e.g. just "Houston" with two Houston sites) -> None."""
    low = text.lower()
    best, best_len = [], 0
    for site in s.region_sites(region_id):
        if re.search(rf"\b{site['site_id'].lower()}\b", low):
            return site["site_id"]
        words = [w for w in site["name"].lower().split() if w not in GENERIC_NAME_WORDS]
        phrases = [" ".join(words), *words, (site["city"] or "").lower()]
        n = max((len(p) for p in phrases if p and re.search(rf"\b{re.escape(p)}\b", low)), default=0)
        if n > best_len:
            best, best_len = [site["site_id"]], n
        elif n and n == best_len:
            best.append(site["site_id"])
    return best[0] if len(best) == 1 else None


def _which_asset(s, site_id: str, text: str) -> str | None:
    """The machine a text is about: one named by ID, else the one we last asked about, else the
    riskiest machine of the type mentioned, else the site's only machine."""
    assets = s.site_assets(site_id)
    for a in assets:
        if a["asset_id"].lower() in text.lower():
            return a["asset_id"]
    if m := re.match(r"\[(\S+)\]", s.last_checkin(site_id) or ""):
        return m.group(1)
    for asset_type, words in TYPE_WORDS.items():
        if words.search(text):
            of_type = [a["asset_id"] for a in assets if a["asset_type"] == asset_type]
            if of_type:  # several of that type: the riskiest one is the likeliest culprit
                return max(of_type, key=lambda aid: score_asset(s.features(aid)[aid], s.today).prob)
    if len(assets) == 1:
        return assets[0]["asset_id"]
    return None
