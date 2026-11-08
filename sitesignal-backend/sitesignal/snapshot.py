"""Build the 'snapshot' the LLM sees: one asset's situation at one moment.

The snapshot carries only what the decision needs. Phone numbers and other
contact details are deliberately left out: code adds the recipient after the
LLM decides, so the model can never leak or change who gets messaged.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .risk_model import AssetFacts, RiskResult, tier


def build_snapshot(facts: AssetFacts, risk: RiskResult, site: dict,
                   recent_work_orders: list[dict], forecast: list[dict],
                   high: float, medium: float) -> dict:
    return {
        "snapshot_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "horizon_days": risk.horizon_days,
        "site": {
            "site_id": site["site_id"],
            "name": site.get("name"),
            "type": site.get("type"),              # retail, bank branch, clinic...
            "city": site.get("city"),
            "has_refrigerated_inventory": site.get("has_refrigeration", False),
            "revenue_per_day_usd": site.get("revenue_per_day_usd"),
            "open_hours": site.get("open_hours"),
            "region": site.get("region"),
        },
        "asset": {
            "asset_id": facts.asset_id,
            "type": facts.asset_type,
            "age_years": risk.age_years,
            "install_date_estimated": site.get("install_date_estimated", False),
        },
        "risk": {
            "probability_fail_in_horizon": risk.prob,
            "weibull_base_probability": risk.base_prob,
            "hazard_multiplier": risk.hazard_multiplier,
            "tier": tier(risk.prob, high, medium),
            "thresholds": {"high": high, "medium": medium},
            "drivers": risk.drivers,
            "insufficient_data": risk.insufficient_data,
        },
        "recent_work_orders": recent_work_orders[-5:],  # newest last, short notes
        "forecast_next_7_days": forecast[:7],           # [{date, max_f}]
        "field_report_negative": facts.field_report_negative,
    }
