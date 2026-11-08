"""All reads and writes the agent needs, as Postgres queries.

Tools, the inbound handler and the API call these methods; none of them write SQL.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from .risk_model import AssetFacts, RiskResult


def _d(x) -> date | None:
    if x is None or isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def _f(x):
    return float(x) if isinstance(x, Decimal) else x


def _json(obj) -> str:
    return json.dumps(obj, default=lambda o: float(o) if isinstance(o, Decimal) else str(o))


class Store:
    def __init__(self, db, today: date | None = None):
        self.db, self.today = db, today or date.today()

    # ---------------------------------------------------------------- reads
    def site(self, site_id: str) -> dict | None:
        rows = self.db.query("SELECT * FROM sites WHERE site_id = %s", (site_id,))
        return {k: _f(v) for k, v in rows[0].items()} if rows else None

    def asset_site(self, asset_id: str) -> str | None:
        rows = self.db.query("SELECT site_id FROM assets WHERE asset_id = %s", (asset_id,))
        return rows[0]["site_id"] if rows else None

    def upsert_phone_contact(self, kind, key, name, phone, space_id, agent_id) -> None:
        """Register a contact by phone; an already-open conversation for the same phone is kept."""
        self.db.query(
            "INSERT INTO contacts (kind, key, name, space_id, agent_id, phone) VALUES (%s, lower(%s), %s, %s, %s, %s) "
            "ON CONFLICT (kind, key) DO UPDATE SET name = EXCLUDED.name, phone = EXCLUDED.phone, "
            "space_id = CASE WHEN contacts.phone IS NOT DISTINCT FROM EXCLUDED.phone "
            "                AND contacts.space_id NOT LIKE 'pending:%%' THEN contacts.space_id ELSE EXCLUDED.space_id END, "
            "agent_id = CASE WHEN contacts.phone IS NOT DISTINCT FROM EXCLUDED.phone "
            "                AND contacts.agent_id <> '' THEN contacts.agent_id ELSE EXCLUDED.agent_id END",
            (kind, key, name, space_id, agent_id, phone))

    def set_space_for_phone(self, phone: str, space_id: str, agent_id: str) -> None:
        self.db.query("UPDATE contacts SET space_id = %s, agent_id = %s WHERE kind = 'region' AND phone = %s",
                      (space_id, agent_id, phone))

    def regions_for_space(self, space_id: str) -> list[str]:
        return [r["key"] for r in self.db.query(
            "SELECT key FROM contacts WHERE kind = 'region' AND space_id = %s ORDER BY key", (space_id,))]

    def region_sites(self, region_id: str) -> list[dict]:
        return self.db.query("SELECT site_id, name, city FROM sites WHERE region_id = lower(%s)", (region_id,))

    def site_assets(self, site_id: str) -> list[dict]:
        return self.db.query("SELECT asset_id, asset_type FROM assets WHERE site_id = %s ORDER BY asset_id",
                             (site_id,))

    def asset_ids(self) -> list[str]:
        return [r["asset_id"] for r in self.db.query("SELECT asset_id FROM assets ORDER BY asset_id")]

    def features(self, asset_id: str | None = None) -> dict[str, AssetFacts]:
        sql = "SELECT * FROM asset_features(%s::date)"
        params: list = [self.today.isoformat()]
        if asset_id:
            sql += " WHERE asset_id = %s"
            params.append(asset_id)
        out = {}
        for r in self.db.query(sql, params):
            out[r["asset_id"]] = AssetFacts(
                asset_id=r["asset_id"], site_id=r["site_id"], asset_type=r["asset_type"],
                install_date=_d(r["install_date"]), calls_90d=r["calls_90d"],
                refrigerant_recharges_180d=r["refrigerant_recharges_180d"],
                repeat_issue=bool(r["repeat_issue"]),
                days_since_last_repair=r["days_since_last_repair"],
                pm_overdue_months=float(r["pm_overdue_months"] or 0),
                forecast_max_temps_f=[float(t) for t in (r["forecast_max_temps_f"] or [])],
                field_report_negative=bool(r["field_report_negative"]))
        return out

    def recent_work_orders(self, asset_id: str, n: int = 5) -> list[dict]:
        rows = self.db.query(
            "SELECT opened_at, kind, issue_category, refrigerant_added, note FROM work_order_history "
            "WHERE asset_id = %s AND opened_at <= %s::date ORDER BY opened_at DESC LIMIT %s",
            (asset_id, self.today.isoformat(), n))
        return [{"date": str(_d(r["opened_at"])), "kind": r["kind"], "issue": r["issue_category"],
                 "refrigerant_added": r["refrigerant_added"], "note": r["note"]} for r in reversed(rows)]

    def forecast(self, site_id: str) -> list[dict]:
        rows = self.db.query(
            "SELECT forecast_date, max_temp_f FROM weather_forecasts "
            "WHERE site_id = %s AND forecast_date >= %s::date ORDER BY forecast_date LIMIT 7",
            (site_id, self.today.isoformat()))
        return [{"date": str(_d(r["forecast_date"])), "max_f": float(r["max_temp_f"])} for r in rows]

    def field_reports(self, site_id: str, limit: int = 10) -> list[dict]:
        rows = self.db.query(
            "SELECT asset_id, text, working, received_at FROM field_reports WHERE site_id = %s "
            "ORDER BY received_at DESC LIMIT %s", (site_id, limit))
        return [{**r, "received_at": str(r["received_at"])} for r in reversed(rows)]

    def open_wo_for(self, asset_id: str) -> dict | None:
        rows = self.db.query("SELECT * FROM work_orders WHERE asset_id = %s "
                             "AND status IN ('pending_approval','approved')", (asset_id,))
        return rows[0] if rows else None

    def get_work_order(self, wo_id: str) -> dict | None:
        rows = self.db.query("SELECT * FROM work_orders WHERE id = %s", (wo_id,))
        return rows[0] if rows else None

    def last_checkin(self, site_id: str) -> str | None:
        rows = self.db.query("SELECT text FROM messages WHERE kind = 'site' AND lower(to_key) = lower(%s) "
                             "ORDER BY id DESC LIMIT 1", (site_id,))
        return rows[0]["text"] if rows else None

    def message_exists(self, idempotency_key: str) -> bool:
        return bool(self.db.query("SELECT 1 FROM messages WHERE idempotency_key = %s", (idempotency_key,)))

    def count(self, table: str) -> int:
        assert table.isidentifier()
        return self.db.query(f"SELECT count(*) AS n FROM {table}")[0]["n"]

    # ---------------------------------------------------------------- contacts
    def contact(self, kind: str, key: str) -> dict | None:
        rows = self.db.query("SELECT * FROM contacts WHERE kind = %s AND key = lower(%s)", (kind, key))
        return rows[0] if rows else None

    def contact_by_space(self, space_id: str) -> dict | None:
        rows = self.db.query("SELECT * FROM contacts WHERE space_id = %s", (space_id,))
        return rows[0] if rows else None

    def upsert_contact(self, kind: str, key: str, name: str, space_id: str, agent_id: str) -> None:
        self.db.query(
            "INSERT INTO contacts (kind, key, name, space_id, agent_id) VALUES (%s, lower(%s), %s, %s, %s) "
            "ON CONFLICT (kind, key) DO UPDATE SET name = EXCLUDED.name, space_id = EXCLUDED.space_id, "
            "agent_id = EXCLUDED.agent_id, joined_at = now()", (kind, key, name, space_id, agent_id))

    # ---------------------------------------------------------------- writes
    def add_field_report(self, site_id: str, asset_id: str | None, text: str, working: bool | None) -> None:
        self.db.query("INSERT INTO field_reports (site_id, asset_id, text, working) VALUES (%s, %s, %s, %s)",
                      (site_id, asset_id, text, working))

    def create_work_order(self, *, asset_id, site_id, prob, tier, urgency, likely_issue, tasks,
                          do_by, reason) -> str:
        rows = self.db.query(
            "INSERT INTO work_orders (asset_id, site_id, prob, tier, urgency, likely_issue, tasks, do_by, reason) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::date, %s) "
            "ON CONFLICT (asset_id) WHERE status IN ('pending_approval','approved') DO NOTHING RETURNING id",
            (asset_id, site_id, prob, tier, urgency, likely_issue, _json(tasks), do_by, reason))
        if not rows:
            raise ValueError("work order already open for this asset")
        return rows[0]["id"]

    def escalate_work_order(self, wo_id: str, *, prob, tier, urgency, do_by, reason) -> None:
        """Raise an open work order to a higher tier; notified resets so the new urgency gets texted."""
        self.db.query("UPDATE work_orders SET prob = %s, tier = %s, urgency = %s, do_by = %s::date, reason = %s, "
                      "notified = false, updated_at = now() WHERE id = %s",
                      (prob, tier, urgency, do_by, reason, wo_id))

    def update_work_order(self, wo_id: str, status: str, note: str) -> None:
        self.db.query("UPDATE work_orders SET status = %s, status_note = %s, updated_at = now() WHERE id = %s",
                      (status, note, wo_id))

    def mark_notified(self, wo_id: str) -> None:
        self.db.query("UPDATE work_orders SET notified = true, updated_at = now() WHERE id = %s", (wo_id,))

    def add_dashboard(self, asset_id: str, status: str, note: str, prob: float, tier: str) -> None:
        self.db.query("INSERT INTO dashboard_entries (asset_id, status, note, prob, tier) "
                      "VALUES (%s, %s, %s, %s, %s)", (asset_id, status, note, prob, tier))

    def save_risk(self, r: RiskResult, tier: str) -> None:
        self.db.query("INSERT INTO risk_scores (asset_id, prob, base_prob, multiplier, tier, drivers) "
                      "VALUES (%s, %s, %s, %s, %s, %s::jsonb) ON CONFLICT DO NOTHING",
                      (r.asset_id, r.prob, r.base_prob, r.hazard_multiplier, tier, _json(r.drivers)))

    def log_message(self, *, kind, to_key, recipient_name, text, sent, dry_run, error, idempotency_key) -> None:
        self.db.query(
            "INSERT INTO messages (kind, to_key, recipient_name, text, sent, dry_run, error, idempotency_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING",
            (kind, to_key, recipient_name, text, sent, dry_run, error, idempotency_key))

    def call_exists(self, idempotency_key: str, dry_run: bool) -> bool:
        """A real call blocks repeats; in dry-run mode an earlier logged call does too."""
        return bool(self.db.query("SELECT 1 FROM calls WHERE idempotency_key = %s AND (called OR dry_run = %s)",
                                  (idempotency_key, dry_run)))

    def log_call(self, *, asset_id, work_order_id, region_id, recipient_name, script, called, dry_run,
                 call_sid, error, idempotency_key) -> None:
        self.db.query(
            "INSERT INTO calls (asset_id, work_order_id, region_id, recipient_name, script, called, dry_run, "
            "call_sid, error, idempotency_key) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (asset_id, work_order_id, region_id, recipient_name, script, called, dry_run, call_sid, error,
             idempotency_key))

    def set_contact_phone(self, kind: str, key: str, phone: str) -> bool:
        return bool(self.db.query("UPDATE contacts SET phone = %s WHERE kind = %s AND key = lower(%s) "
                                  "RETURNING id", (phone, kind, key)))

    def start_run(self, trigger: str) -> int:
        return self.db.query("INSERT INTO agent_runs (trigger) VALUES (%s) RETURNING run_id", (trigger,))[0]["run_id"]

    def finish_run(self, run_id: int, summary: str, steps: int) -> None:
        self.db.query("UPDATE agent_runs SET summary = %s, steps = %s, finished_at = now() WHERE run_id = %s",
                      (summary, steps, run_id))

    def audit(self, run_id: int | None, tool: str, args: dict, result: dict) -> None:
        self.db.query("INSERT INTO audit_log (run_id, tool, args, result) VALUES (%s, %s, %s::jsonb, %s::jsonb)",
                      (run_id, tool, _json(args), _json(result)))
