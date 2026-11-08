"""The agent's tools. Gemini decides which to call; this code decides what is allowed.

Each tool = a Gemini function declaration + a Python implementation. Guardrails live
here, not in the prompt, so a confused or manipulated model still can't text a
low-risk site, spam a manager, or pick its own recipient.
"""
from __future__ import annotations

import os
from . import photon_notify
from .risk_model import score_asset, tier
from .snapshot import build_snapshot
from .store import Store

HIGH = float(os.environ.get("RISK_HIGH", 0.20))
MEDIUM = float(os.environ.get("RISK_MEDIUM", 0.08))
MAX_TEXTS_PER_RUN = int(os.environ.get("MAX_TEXTS_PER_RUN", 30))
TIER_RANK = {"unknown": -1, "low": 0, "medium": 1, "high": 2}

S, O, A, I, N, B = "STRING", "OBJECT", "ARRAY", "INTEGER", "NUMBER", "BOOLEAN"


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": O, "properties": props, "required": required}


DECLARATIONS = [
    {"name": "list_flagged_assets",
     "description": "Run the failure-risk model over every asset and return those at or above a tier, "
                    "riskiest first, with their top drivers and whether a work order is already open.",
     "parameters": _obj({"min_tier": {"type": S, "enum": ["medium", "high"]}}, ["min_tier"])},
    {"name": "get_risk_snapshot",
     "description": "Full snapshot for one asset: failure probability in the next 30 days, Weibull base risk, "
                    "drivers, site context, recent work orders and the 7-day forecast.",
     "parameters": _obj({"asset_id": {"type": S}}, ["asset_id"])},
    {"name": "get_field_reports",
     "description": "Reports texted in by the site manager for a site (newest last).",
     "parameters": _obj({"site_id": {"type": S}}, ["site_id"])},
    {"name": "text_site_manager",
     "description": "Text the site's manager one short yes/no question about the asset's current condition. "
                    "Use it to confirm a medium-risk prediction before spending money. Their reply arrives "
                    "later as a field report and re-triggers you.",
     "parameters": _obj({"site_id": {"type": S}, "asset_id": {"type": S},
                         "question": {"type": S, "description": "Under 240 characters, ends with 'Reply Y or N'."}},
                        ["site_id", "asset_id", "question"])},
    {"name": "create_work_order",
     "description": "Create a preventive work order for an asset. Status starts as pending_approval.",
     "parameters": _obj({"asset_id": {"type": S},
                         "urgency": {"type": S, "enum": ["today", "48h", "this_week"]},
                         "likely_issue": {"type": S},
                         "tasks": {"type": A, "items": {"type": S}},
                         "do_by": {"type": S, "description": "YYYY-MM-DD"},
                         "reason": {"type": S, "description": "Cite the snapshot's numbers."}},
                        ["asset_id", "urgency", "likely_issue", "tasks", "do_by", "reason"])},
    {"name": "notify_service_manager",
     "description": "Text a work order to the service manager for the site's region via Photon. "
                    "The recipient is chosen by the system from the work order's site.",
     "parameters": _obj({"work_order_id": {"type": S},
                         "message": {"type": S, "description": "Under 360 characters, plain words."}},
                        ["work_order_id", "message"])},
    {"name": "update_work_order",
     "description": "Change a work order's status after a manager replies.",
     "parameters": _obj({"work_order_id": {"type": S},
                         "status": {"type": S, "enum": ["approved", "on_hold", "cancelled"]},
                         "note": {"type": S}}, ["work_order_id", "status", "note"])},
    {"name": "add_to_dashboard",
     "description": "Record an asset on the dashboard without texting anyone.",
     "parameters": _obj({"asset_id": {"type": S},
                         "status": {"type": S, "enum": ["watch", "waiting_on_site", "work_order_pending", "ok"]},
                         "note": {"type": S}}, ["asset_id", "status", "note"])},
]


class Toolbox:
    def __init__(self, store: Store, dry_run: bool = True):
        self.s, self.dry_run = store, dry_run
        self.texts_sent = 0
        self.checked_in: set[str] = set()
        self.run_id: int | None = None

    # ---- helpers ---------------------------------------------------------
    def _risk(self, asset_id: str, facts=None):
        facts = facts or self.s.features(asset_id)[asset_id]
        r = score_asset(facts, self.s.today)
        return facts, r, ("unknown" if r.insufficient_data else tier(r.prob, HIGH, MEDIUM))

    def _text(self, kind: str, key: str, text: str, idem: str) -> dict:
        if self.texts_sent >= MAX_TEXTS_PER_RUN:
            return {"error": f"text limit of {MAX_TEXTS_PER_RUN} per run reached"}
        res = photon_notify.send(self.s, kind, key, text, idem, dry_run=self.dry_run)
        if res.get("sent") or res.get("dry_run"):
            self.texts_sent += 1
        return res

    # ---- tools -----------------------------------------------------------
    def list_flagged_assets(self, min_tier: str):
        rows = []
        for aid, facts in self.s.features().items():
            _, r, t = self._risk(aid, facts)
            if t != "unknown":
                self.s.save_risk(r, t)  # history for dashboard trend charts
            if TIER_RANK[t] >= TIER_RANK[min_tier]:
                wo = self.s.open_wo_for(aid)
                rows.append({"asset_id": aid, "site_id": facts.site_id,
                             "site_name": self.s.site(facts.site_id)["name"],
                             "type": facts.asset_type, "prob": r.prob, "tier": t,
                             "top_drivers": [d["factor"] for d in r.drivers[:3]],
                             "open_work_order": wo["id"] if wo else None})
        rows.sort(key=lambda x: -x["prob"])
        return {"count": len(rows), "assets": rows[:MAX_TEXTS_PER_RUN]}

    def get_risk_snapshot(self, asset_id: str):
        if not self.s.asset_site(asset_id):
            return {"error": "unknown asset_id"}
        facts, r, _ = self._risk(asset_id)
        site = self.s.site(facts.site_id)
        site["region"] = site.get("region_id")
        return build_snapshot(facts, r, site, self.s.recent_work_orders(asset_id),
                              self.s.forecast(facts.site_id), HIGH, MEDIUM)

    def get_field_reports(self, site_id: str):
        return {"reports": self.s.field_reports(site_id)}

    def text_site_manager(self, site_id: str, asset_id: str, question: str):
        if self.s.asset_site(asset_id) != site_id:
            return {"error": "asset does not belong to that site"}
        _, _, t = self._risk(asset_id)
        if TIER_RANK[t] < TIER_RANK["medium"]:
            return {"error": f"blocked: asset is {t} risk; check-ins are for medium or high"}
        if asset_id in self.checked_in:
            return {"error": "already checked in on this asset this run"}
        self.checked_in.add(asset_id)
        idem = f"checkin-{asset_id}-{self.s.today:%Y%m%d}"
        return self._text("site", site_id, f"[{asset_id}] {question[:240]}", idem)

    def create_work_order(self, asset_id: str, urgency: str, likely_issue: str,
                          tasks: list[str], do_by: str, reason: str):
        if not self.s.asset_site(asset_id):
            return {"error": "unknown asset_id"}
        facts, r, t = self._risk(asset_id)
        if TIER_RANK[t] < TIER_RANK["medium"]:
            return {"error": f"blocked: asset is {t} risk (p={r.prob}); add it to the dashboard instead"}
        try:
            wo_id = self.s.create_work_order(
                asset_id=asset_id, site_id=facts.site_id, prob=r.prob, tier=t, urgency=urgency,
                likely_issue=likely_issue[:120], tasks=tasks[:6], do_by=do_by, reason=reason[:500])
        except ValueError:
            return {"error": "work order already open", "work_order_id": self.s.open_wo_for(asset_id)["id"]}
        return {"work_order_id": wo_id, "asset_id": asset_id, "status": "pending_approval"}

    def notify_service_manager(self, work_order_id: str, message: str):
        wo = self.s.get_work_order(work_order_id)
        if wo is None:
            return {"error": "unknown work_order_id; create the work order first"}
        if wo["notified"]:
            return {"error": "service manager already notified for this work order"}
        region = self.s.site(wo["site_id"])["region_id"]
        text = f"[{work_order_id}] {message[:360]} Reply APPROVE {work_order_id} or HOLD {work_order_id}"
        res = self._text("region", region, text, idem=work_order_id)
        if res.get("sent") or res.get("dry_run"):
            self.s.mark_notified(work_order_id)
        return res

    def update_work_order(self, work_order_id: str, status: str, note: str):
        if self.s.get_work_order(work_order_id) is None:
            return {"error": "unknown work_order_id"}
        self.s.update_work_order(work_order_id, status, note)
        return {"work_order_id": work_order_id, "status": status}

    def add_to_dashboard(self, asset_id: str, status: str, note: str):
        if not self.s.asset_site(asset_id):
            return {"error": "unknown asset_id"}
        _, r, t = self._risk(asset_id)
        self.s.add_dashboard(asset_id, status, note, r.prob, t)
        return {"ok": True}

    # ---- dispatch ----------------------------------------------------------
    def call(self, name: str, args: dict):
        fn = getattr(self, name, None)
        if name not in {d["name"] for d in DECLARATIONS} or fn is None:
            result = {"error": f"unknown tool {name}"}
        else:
            try:
                result = fn(**args)
            except TypeError as e:
                result = {"error": f"bad arguments: {e}"}
            except Exception as e:  # database or network error: report, don't crash the run
                result = {"error": f"{type(e).__name__}: {e}"}
        self.s.audit(self.run_id, name, args, result)
        return result
