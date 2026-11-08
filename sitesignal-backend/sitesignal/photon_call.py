"""Phone call to the region's manager about every high-risk asset.

Medium-risk assets get a texted work order; high-risk ones also get a call, at most once
per asset per day and MAX_CALLS_PER_RUN per run, riskiest first.

How the call is placed (Photon Beta voice cookbook for Twilio): our code asks Twilio's
REST API to dial; Twilio routes the call over SIP through Photon's voice number, which
rings the manager's phone. Twilio's TwiML <Say> speaks the alert. Needs a Twilio account,
a Photon plan with phone calls, and a Photon Voice profile with outbound enabled
(Digest algorithm "MD5 (legacy)").

PHOTON_CALL=0 (default) logs the call script without dialing.

VERIFY against the cookbook before the demo: the SIP URI format in `_sip_uri` and the
Twilio form fields in `_place_call`.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse
import urllib.request
from html import escape

from .risk_model import MEDIAN_LIFE_YEARS, score_asset, tier
from .tools import HIGH, MEDIUM

TWILIO_CALLS = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
MAX_CALLS_PER_RUN = int(os.environ.get("MAX_CALLS_PER_RUN", 3))
E164 = re.compile(r"^\+[1-9]\d{7,14}$")

ASSET_NAMES = {"rtu": "rooftop A C unit", "walk_in_cooler": "walk-in cooler", "water_heater": "water heater"}
DRIVER_PHRASES = {
    "age": "it is near the end of its service life",
    "calls_90d": "repeated service calls in the last 90 days",
    "refrigerant_recharges_180d": "refrigerant recharges, which suggest a leak",
    "repeat_issue": "the same problem has come back",
    "recent_repair": "it was repaired very recently",
    "pm_overdue_months": "preventive maintenance is overdue",
    "hot_days_next_7": "hot days in this week's forecast",
    "peak_100f": "a forecast peak above 100 degrees",
    "field_report_negative": "the site manager reported it is not working right",
}


def urgent_assets(store) -> list[dict]:
    """High-tier assets, scored fresh, riskiest first."""
    out = []
    for facts in store.features().values():
        r = score_asset(facts, store.today)
        if r.insufficient_data or tier(r.prob, HIGH, MEDIUM) != "high":
            continue
        old_age = r.age_years >= 0.8 * MEDIAN_LIFE_YEARS[facts.asset_type]
        drivers = [d["factor"] for d in r.drivers if d["factor"] != "age" or old_age]
        out.append({"asset_id": r.asset_id, "site_id": r.site_id, "asset_type": facts.asset_type,
                    "prob": r.prob, "drivers": drivers})
    return sorted(out, key=lambda a: -a["prob"])


def build_script(asset: dict, site: dict, work_order_id: str | None) -> str:
    pct = round(asset["prob"] * 100)
    unit = ASSET_NAMES.get(asset["asset_type"], asset["asset_type"].replace("_", " "))
    reasons = [DRIVER_PHRASES[f] for f in asset["drivers"] if f in DRIVER_PHRASES][:2]
    where = site["name"] + (f" in {site['city']}" if site.get("city") else "")
    script = (f"This is PenguinRE with an urgent maintenance alert. "
              f"The {unit} at {where} has an estimated {pct} percent chance of failing in the next 30 days")
    script += f", because {' and '.join(reasons)}. " if reasons else ". "
    if work_order_id:
        script += (f"Work order {work_order_id.replace('-', ' ')} is waiting for you. "
                   f"Reply APPROVE or HOLD to the text we sent.")
    else:
        script += "Please schedule a preventive visit as soon as possible."
    return script


def _sip_uri(phone: str) -> str:
    return f"sip:{phone}@{os.environ['PHOTON_SIP_HOST']};transport=tls;secure=true"


def _place_call(phone: str, script: str) -> str:
    """Returns Twilio's call sid."""
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    said = escape(script)
    twiml = f'<Response><Say voice="alice">{said}</Say><Pause length="1"/><Say voice="alice">{said}</Say><Hangup/></Response>'
    form = urllib.parse.urlencode({
        "To": _sip_uri(phone),
        "From": os.environ["PHOTON_VOICE_NUMBER"],
        "SipAuthUsername": os.environ["PHOTON_SIP_USERNAME"],
        "SipAuthPassword": os.environ["PHOTON_SIP_PASSWORD"],
        "Twiml": twiml,
    }).encode()
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req = urllib.request.Request(TWILIO_CALLS.format(sid=sid), data=form,
                                 headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["sid"]


def call_urgent(store, dry_run: bool | None = None) -> list[dict]:
    """Call about each high-risk asset not yet called about today. Returns one result per call."""
    if dry_run is None:
        dry_run = os.environ.get("PHOTON_CALL", "0") != "1"
    results = []
    for asset in urgent_assets(store):
        if len(results) >= MAX_CALLS_PER_RUN:
            break
        idem = f"call-{asset['asset_id']}-{store.today:%Y%m%d}"
        if not store.call_exists(idem, dry_run):
            results.append(_call(store, asset, idem, dry_run))
    return results


def _call(store, asset: dict, idem: str, dry_run: bool) -> dict:
    site = store.site(asset["site_id"])
    region = site["region_id"]
    wo = store.open_wo_for(asset["asset_id"])
    script = build_script(asset, site, wo["id"] if wo else None)
    contact = store.contact("region", region)

    res = {"called": False, "asset_id": asset["asset_id"], "region": region, "script": script}
    if contact is None or not contact.get("phone"):
        res["reason"] = f"no service manager phone on file for region '{region}'"
    else:
        res["to"] = contact["name"]
        if dry_run:
            res["dry_run"] = True
        else:
            try:
                res["call_sid"] = _place_call(contact["phone"], script)
                res["called"] = True
            except Exception as e:  # log it; a failed call must not break the sweep
                res["reason"] = f"call failed: {e}"

    store.log_call(asset_id=asset["asset_id"], work_order_id=wo["id"] if wo else None, region_id=region,
                   recipient_name=res.get("to"), script=script, called=res["called"],
                   dry_run=res.get("dry_run", False), call_sid=res.get("call_sid"),
                   error=res.get("reason"), idempotency_key=idem)
    return res


def valid_phone(phone: str) -> bool:
    return bool(E164.match(phone))
