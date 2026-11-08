"""LLM brain: turns a risk-model output into human-readable text.

Stateless by design — no DB access, no side effects. Every function here
takes the risk model's JSON as input and returns text. This keeps the LLM
layer easy to test (pass in a dict, assert on a string) and swappable
(change model/provider without touching scoring or DB code).
"""

import os

from google import genai

_MODEL_NAME = "gemini-2.0-flash"


def _get_client() -> genai.Client:
    api_key = os.environ["GEMINI_API_KEY"]
    return genai.Client(api_key=api_key)


def generate_incident_summary(risk: dict) -> str:
    """Short natural-language summary of one equipment's risk, for the dashboard.

    risk: the risk-model output dict, e.g.
        {
            "equipment_id": 12,
            "equipment_name": "HVAC Compressor - Site 4",
            "failure_probability": 0.78,
            "age_years": 8.5,
            "expected_lifespan_years": 10,
            "recent_work_orders_6mo": 3,
            "utility_trend_pct": 22.0,
            "preventive_cost_estimate": 650,
            "emergency_cost_estimate": 2275,
        }
    """
    raise NotImplementedError


def generate_dispatch_message(risk: dict) -> str:
    """Concise, actionable text/call script for notifying the service manager.

    Same input shape as generate_incident_summary. Should be short enough
    for an SMS body and name the equipment, site, risk %, and recommended action.
    """
    raise NotImplementedError


def generate_brief(risk: dict) -> dict:
    """Convenience wrapper combining both outputs.

    Returns: {"summary": str, "dispatch_message": str}
    """
    return {
        "summary": generate_incident_summary(risk),
        "dispatch_message": generate_dispatch_message(risk),
    }
