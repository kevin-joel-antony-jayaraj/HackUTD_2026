"""Failure-risk model: Weibull age curve + proportional-hazard adjustments.

How it works, in plain terms:
1. Age (asset list) -> a Weibull curve gives the base chance this unit fails in
   the next `horizon_days`, given that it has survived to its current age.
2. Work-order history and the weather forecast don't replace that curve; they
   scale how fast the unit is "using up" its life (a hazard multiplier).
3. Combined: P(fail) = 1 - S_base ** exp(eta), where S_base is the Weibull
   conditional survival and eta is the weighted sum of the other factors.

Every coefficient below is an ASSUMPTION for the demo. Fit them on your
synthetic data (or real data) before trusting the numbers; see `fit_weights`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

# Median service life in years (ASHRAE equipment life chart; rooftop units = 15).
# Water heater and walk-in cooler values are placeholders: replace with a source.
MEDIAN_LIFE_YEARS = {
    "rtu": 15.0,           # packaged rooftop AC (ASHRAE: 15)
    "walk_in_cooler": 15.0,  # assumption
    "water_heater": 10.0,    # assumption
}

# Weibull shape (k). k > 1 means "wears out": risk rises with age.
# 3.0 is a common wear-out assumption, not a measured value.
WEIBULL_SHAPE = {"rtu": 3.0, "walk_in_cooler": 3.0, "water_heater": 3.0}


@dataclass
class Weights:
    """Log-hazard weights. exp(weight) = how much one unit of the feature
    multiplies the failure rate. All values are demo assumptions."""
    calls_90d: float = 0.35              # each service call in last 90 days
    refrigerant_recharges_180d: float = 0.60  # each recharge (likely leak)
    repeat_issue: float = 0.40           # same issue logged twice in 90 days
    days_since_repair_lt_30: float = 0.25  # was repaired very recently
    pm_overdue_months: float = 0.10      # per month past due preventive maintenance
    hot_days_next_7: float = 0.20        # forecast days >= HOT_DAY_F in next week
    max_temp_over_100: float = 0.30      # forecast peak at or above 100 F
    field_report_negative: float = 0.80  # site manager said it isn't working right


HOT_DAY_F = 95.0


@dataclass
class AssetFacts:
    asset_id: str
    site_id: str
    asset_type: str               # "rtu" | "walk_in_cooler" | "water_heater"
    install_date: date | None     # may be estimated from the serial number
    calls_90d: int = 0
    refrigerant_recharges_180d: int = 0
    repeat_issue: bool = False
    days_since_last_repair: int | None = None
    pm_overdue_months: float = 0.0
    forecast_max_temps_f: list[float] = field(default_factory=list)  # next 7 days
    field_report_negative: bool = False


@dataclass
class RiskResult:
    asset_id: str
    site_id: str
    age_years: float | None
    base_prob: float              # Weibull-only chance of failing in horizon
    hazard_multiplier: float      # exp(eta)
    prob: float                   # final chance of failing in horizon
    drivers: list[dict]           # each factor's contribution, largest first
    horizon_days: int
    insufficient_data: bool = False


def weibull_scale(median_years: float, shape: float) -> float:
    """Scale (lambda) such that the Weibull median equals `median_years`."""
    return median_years / (math.log(2) ** (1.0 / shape))


def conditional_survival(age: float, horizon: float, shape: float, scale: float) -> float:
    """P(still working at age+horizon | working at age)."""
    return math.exp((age / scale) ** shape - ((age + horizon) / scale) ** shape)


def score_asset(a: AssetFacts, today: date, horizon_days: int = 30,
                w: Weights | None = None) -> RiskResult:
    w = w or Weights()
    if a.install_date is None or a.asset_type not in MEDIAN_LIFE_YEARS:
        return RiskResult(a.asset_id, a.site_id, None, 0.0, 1.0, 0.0, [],
                          horizon_days, insufficient_data=True)

    age = max((today - a.install_date).days / 365.25, 0.0)
    k = WEIBULL_SHAPE[a.asset_type]
    lam = weibull_scale(MEDIAN_LIFE_YEARS[a.asset_type], k)
    s_base = conditional_survival(age, horizon_days / 365.25, k, lam)
    base_prob = 1.0 - s_base

    temps = a.forecast_max_temps_f[:7]
    terms = {
        "calls_90d": w.calls_90d * a.calls_90d,
        "refrigerant_recharges_180d": w.refrigerant_recharges_180d * a.refrigerant_recharges_180d,
        "repeat_issue": w.repeat_issue * (1 if a.repeat_issue else 0),
        "recent_repair": w.days_since_repair_lt_30 * (
            1 if a.days_since_last_repair is not None and a.days_since_last_repair < 30 else 0),
        "pm_overdue_months": w.pm_overdue_months * min(a.pm_overdue_months, 12.0),  # cap: a year+ overdue
        "hot_days_next_7": w.hot_days_next_7 * sum(t >= HOT_DAY_F for t in temps),
        "peak_100f": w.max_temp_over_100 * (1 if temps and max(temps) >= 100 else 0),
        "field_report_negative": w.field_report_negative * (1 if a.field_report_negative else 0),
    }
    eta = min(sum(terms.values()), 8.0)  # cap the multiplier (~3000x) so bad data can't explode it
    mult = math.exp(eta)
    prob = 1.0 - s_base ** mult

    drivers = [{"factor": "age", "value": round(age, 1),
                "detail": f"{age:.1f} yrs vs {MEDIAN_LIFE_YEARS[a.asset_type]:.0f}-yr median life",
                "base_prob": round(base_prob, 4)}]
    drivers += [{"factor": f, "log_hazard": round(v, 3), "multiplier": round(math.exp(v), 2)}
                for f, v in sorted(terms.items(), key=lambda kv: -kv[1]) if v > 0]

    return RiskResult(a.asset_id, a.site_id, round(age, 2), round(base_prob, 4),
                      round(mult, 3), round(prob, 4), drivers, horizon_days)


def tier(prob: float, high: float = 0.35, medium: float = 0.15) -> str:
    """Thresholds are demo defaults; tune them so the number of 'high' alerts
    per week is something a facilities team can actually act on."""
    if prob >= high:
        return "high"
    if prob >= medium:
        return "medium"
    return "low"


def fit_weights(rows, labels):
    """Optional: fit the log-hazard weights on labeled history.

    rows: list of AssetFacts snapshots; labels: 1 if a failure work order
    followed within the horizon. With scikit-learn available, a
    LogisticRegression on [log(-log S_base)] + features approximates this
    model (complementary log-log link). Left as a stub so the demo runs with
    numpy/scipy only.
    """
    raise NotImplementedError("Fit with sklearn or lifelines once you have labeled data.")
