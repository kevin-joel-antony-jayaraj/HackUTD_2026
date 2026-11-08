"""Weather input: 7-day forecast highs (F) per site from the US National Weather Service (free, no key).

    python -m sitesignal.weather_nws out/bundle.json -o out/weather.json

Output: {"SITE_ID": [max_temp_f x up to 7 days]}. Sites with no coordinates, or outside the US,
are left out (the Safety Index then lowers confidence for them instead of guessing).
Open-Meteo is a drop-in alternative, but its free tier is rate-limited per shared IP.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

UA = "sitesignal-hackathon-demo"   # NWS requires an identifying User-Agent


def _get(url: str, tries: int = 3) -> dict:
    last = None
    for i in range(tries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/geo+json"})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(2 ** (i + 1))
    raise RuntimeError(f"NWS request failed: {url}: {last}")


def forecast_highs(lat: float, lon: float, days: int = 7) -> list[float]:
    pt = _get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
    periods = _get(pt["properties"]["forecast"])["properties"]["periods"]
    highs = [float(p["temperature"]) for p in periods if p.get("isDaytime")]   # daytime period = the day's high
    unit = {p.get("temperatureUnit", "F") for p in periods}
    if unit - {"F"}:
        raise RuntimeError(f"unexpected temperature unit {unit}")
    return highs[:days]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle")
    ap.add_argument("-o", "--out", default="-")
    a = ap.parse_args()
    out = {}
    for s in json.load(open(a.bundle)).get("sites", []):
        if s.get("lat") is None or s.get("lon") is None:
            print(f"  {s['site_id']}: no coordinates, skipped", file=sys.stderr)
            continue
        try:
            out[s["site_id"]] = forecast_highs(s["lat"], s["lon"])
            print(f"  {s['site_id']}: {out[s['site_id']]}", file=sys.stderr)
        except Exception as e:                       # one bad site must not lose the others
            print(f"  {s['site_id']}: FAILED ({e})", file=sys.stderr)
    text = json.dumps(out, indent=2)
    if a.out == "-":
        print(text)
    else:
        open(a.out, "w").write(text)


if __name__ == "__main__":
    main()
