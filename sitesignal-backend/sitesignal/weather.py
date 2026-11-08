"""Weather job: pull each site's 7-day max-temperature forecast from Open-Meteo
(free, no key) into weather_forecasts. One request per distinct rounded location.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

URL = "https://api.open-meteo.com/v1/forecast"


def fetch_forecast(lat: float, lon: float, days: int = 7) -> list[tuple[str, float]]:
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit", "timezone": "America/Chicago", "forecast_days": days})
    with urllib.request.urlopen(f"{URL}?{q}", timeout=20) as resp:
        return parse_forecast(json.loads(resp.read()))


def parse_forecast(payload: dict) -> list[tuple[str, float]]:
    daily = payload["daily"]
    return [(d, float(t)) for d, t in zip(daily["time"], daily["temperature_2m_max"]) if t is not None]


def refresh_all(db, fetch=fetch_forecast) -> dict:
    sites = db.query("SELECT site_id, lat, lon FROM sites WHERE lat IS NOT NULL AND lon IS NOT NULL")
    cache: dict[tuple, list] = {}
    updated, failed = 0, []
    for s in sites:
        key = (round(float(s["lat"]), 1), round(float(s["lon"]), 1))  # ~10 km grid, fewer calls
        try:
            if key not in cache:
                cache[key] = fetch(*key)
                time.sleep(0.2)  # be polite to the free API
            for day, tmax in cache[key]:
                db.query("INSERT INTO weather_forecasts (site_id, forecast_date, max_temp_f, fetched_at) "
                         "VALUES (%s, %s::date, %s, now()) ON CONFLICT (site_id, forecast_date) "
                         "DO UPDATE SET max_temp_f = EXCLUDED.max_temp_f, fetched_at = now()",
                         (s["site_id"], day, round(tmax, 1)))
            updated += 1
        except Exception as e:  # keep going; old forecast rows stay in place
            failed.append({"site_id": s["site_id"], "error": str(e)[:200]})
    return {"sites_updated": updated, "api_calls": len(cache), "failed": failed}
