"""How much do the Safety Index weights matter? (Sensitivity analysis, NOT calibration.)

    python -m sitesignal.sensitivity [--n 300] [--draws 300] [--spread 0.3]

Builds a SYNTHETIC portfolio (random ages, repair histories, PM records) and re-scores it while
perturbing the weights. Reports how much the ranking and the bands change. A weight that barely
moves the ranking does not need calibrating first; one that does is where real failure data is
needed most. This says nothing about whether any weight is CORRECT: that needs labelled failures.
"""
from __future__ import annotations

import argparse
import random
from datetime import date, timedelta

from .safety_index import WEIGHTS, build

AS_OF = date(2026, 11, 8)
TYPES = ["rtu", "walk_in_cooler", "water_heater"]
CATS = ["not_cooling", "leak", "short_cycling", "compressor", "noise"]


def synth(n: int, rng: random.Random) -> dict:
    sites = [{"site_id": f"S{i:03d}", "name": f"S{i}", "type": "retail", "region_id": "x",
              "has_refrigeration": rng.random() < 0.4, "revenue_per_day_usd": rng.choice([3000, 8000, 14000])}
             for i in range(max(n // 3, 1))]
    assets, wos = [], []
    for i in range(n):
        site = sites[i % len(sites)]
        typ, aid = rng.choice(TYPES), f"A{i:04d}"
        age = rng.uniform(1, 24)
        assets.append({"asset_id": aid, "site_id": site["site_id"], "asset_type": typ,
                       "install_date": (AS_OF - timedelta(days=int(age * 365.25))).isoformat(),
                       "install_date_estimated": False, "pm_interval_days": 90, "flags": []})
        stress = age / 15
        for _ in range(int(rng.expovariate(1 / (0.4 + 0.8 * stress)))):      # older -> more history
            d = AS_OF - timedelta(days=rng.randint(1, 200))
            rech = rng.random() < 0.15 + 0.1 * stress
            wos.append({"asset_id": aid, "opened_at": d.isoformat(), "kind": rng.choice(["repair", "repair", "emergency"]),
                        "issue_category": rng.choice(CATS), "refrigerant_added": rech, "cost_usd": 300.0, "note": None, "flags": []})
        if rng.random() < 0.6:
            wos.append({"asset_id": aid, "opened_at": (AS_OF - timedelta(days=rng.randint(10, 300))).isoformat(),
                        "kind": "pm", "issue_category": None, "refrigerant_added": False, "cost_usd": 100.0, "note": None, "flags": []})
    return {"sites": sites, "assets": assets, "work_orders": wos}


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i]); r = [0.0] * len(xs); i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b); n = len(a); ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra) ** .5, sum((y - mb) ** 2 for y in rb) ** .5
    return cov / (va * vb) if va and vb else 1.0


def scores(bundle, w=None):
    out = {a["asset_id"]: a for a in build(bundle, None, AS_OF, w)["assets"]}
    return out


def compare(base, other):
    ids = list(base)
    idx = lambda d: [d[i]["safety_index"] if d[i]["safety_index"] is not None else -1 for i in ids]
    b, o = idx(base), idx(other)
    flips = sum(base[i]["band"] != other[i]["band"] for i in ids) / len(ids)
    k = max(len(ids) // 10, 1)
    topb = set(sorted(ids, key=lambda i: b[ids.index(i)])[:k]); topo = set(sorted(ids, key=lambda i: o[ids.index(i)])[:k])
    return spearman(b, o), flips, len(topb & topo) / k


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=300); ap.add_argument("--draws", type=int, default=300)
    ap.add_argument("--spread", type=float, default=0.3); ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(); rng = random.Random(a.seed)
    bundle = synth(a.n, rng); base = scores(bundle)
    bands = {}
    for v in base.values():
        bands[v["band"]] = bands.get(v["band"], 0) + 1
    print(f"SYNTHETIC portfolio: {a.n} assets, band mix {bands}")
    print(f"\nOne weight at a time, scaled x{1 - a.spread:.1f} and x{1 + a.spread:.1f} (others fixed):")
    print(f"{'weight':16}{'rank corr':>10}{'band flips':>12}{'worst-10% kept':>16}")
    rows = []
    for k in WEIGHTS:
        rs = [compare(base, scores(bundle, {k: WEIGHTS[k] * f})) for f in (1 - a.spread, 1 + a.spread)]
        rho, fl, top = min(r[0] for r in rs), max(r[1] for r in rs), min(r[2] for r in rs)
        rows.append((k, rho, fl, top))
    for k, rho, fl, top in sorted(rows, key=lambda r: r[1]):
        print(f"{k:16}{rho:>10.3f}{fl:>11.0%}{top:>15.0%}")
    res = []
    for _ in range(a.draws):
        w = {k: WEIGHTS[k] * rng.uniform(1 - a.spread, 1 + a.spread) for k in WEIGHTS}
        res.append(compare(base, scores(bundle, w)))
    f = lambda i, fn: fn(r[i] for r in res)
    print(f"\nAll weights perturbed together by up to +/-{a.spread:.0%} ({a.draws} random draws):")
    print(f"  rank correlation with baseline : mean {sum(r[0] for r in res)/len(res):.3f}, worst {min(r[0] for r in res):.3f}")
    print(f"  assets changing band           : mean {sum(r[1] for r in res)/len(res):.0%}, worst {max(r[1] for r in res):.0%}")
    print(f"  worst-10% list kept            : mean {sum(r[2] for r in res)/len(res):.0%}, worst {min(r[2] for r in res):.0%}")


if __name__ == "__main__":
    main()
