"""Backtest the comp model against sales whose price is already known.

The honest test of a valuation model: take a real sale, hide its price, predict
it from the sales that had already happened, and compare. Everything here is
pure — it takes a list of comps and returns numbers — so the scoring logic is
tested without a network.

Two things are measured, and both matter:

**Accuracy.** Median absolute percent error. The median rather than the mean
because a handful of unusual properties would otherwise dominate, and because
a typical user cares what a typical estimate does.

**Calibration.** How often the published interval actually contains the true
price. A model that is accurate but dishonest about its uncertainty is worse
than one that is rough and truthful, because this project promises a range
rather than a point. The interval is a weighted interquartile band, so a
well-calibrated one contains truth roughly half the time — far above that means
the band is uselessly wide, far below means it is lying.

Two rules keep the test from flattering itself:

* A property is never a comp for itself.
* Only sales *strictly before* the subject's sale date are used, so the model
  never sees the future. Without this the scores are meaningless.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

from app.ingestion.records import CompSale
from app.models.baseline_comp_model import (
    MAX_AGE_MONTHS,
    InsufficientComps,
    SubjectProperty,
    ValuationEstimate,
    estimate_value,
    haversine_miles,
    is_plausible,
)

#: How far back to look for comps, in days. Mirrors the model's own age cutoff.
LOOKBACK_DAYS = int(MAX_AGE_MONTHS * 30.44)

#: Only consider candidate comps this close, before the model weighs them.
#: Purely a speed guard — the model applies its own, stricter radius.
PREFILTER_MILES = 5.0


@dataclass(frozen=True)
class Prediction:
    """One backtested sale."""

    parcel_id: str
    actual: int
    predicted: float
    low: float
    high: float
    confidence: str
    comps_used: int

    @property
    def abs_pct_error(self) -> float:
        return abs(self.predicted - self.actual) / self.actual

    @property
    def inside_interval(self) -> bool:
        return self.low <= self.actual <= self.high


@dataclass(frozen=True)
class BacktestResult:
    """Scores for one county."""

    county: str
    attempted: int
    predicted: int
    skipped: int
    median_abs_pct_error: float
    mean_abs_pct_error: float
    within_10_pct: float
    within_20_pct: float
    interval_coverage: float
    median_comps_used: float
    by_confidence: dict[str, dict[str, float]]

    @property
    def coverage_rate(self) -> float:
        """Share of attempted sales the model was willing to estimate at all."""
        return self.predicted / self.attempted if self.attempted else 0.0


def candidates_for(
    subject_sale: CompSale,
    pool: Sequence[CompSale],
    *,
    lookback_days: int = LOOKBACK_DAYS,
    prefilter_miles: float = PREFILTER_MILES,
) -> list[CompSale]:
    """Sales usable as comps for ``subject_sale``.

    Excludes the property itself and anything that had not yet sold, which are
    the two ways a backtest quietly cheats.
    """
    earliest = subject_sale.sale_date - timedelta(days=lookback_days)
    usable: list[CompSale] = []
    for comp in pool:
        if comp.parcel_id == subject_sale.parcel_id:
            continue
        if not (earliest <= comp.sale_date < subject_sale.sale_date):
            continue
        distance = haversine_miles(
            subject_sale.latitude,
            subject_sale.longitude,
            comp.latitude,
            comp.longitude,
        )
        if distance <= prefilter_miles:
            usable.append(comp)
    return usable


def predict_one(
    subject_sale: CompSale, pool: Sequence[CompSale], **kwargs
) -> Prediction | InsufficientComps:
    """Predict one sale's price from prior nearby sales."""
    subject = SubjectProperty(
        latitude=subject_sale.latitude,
        longitude=subject_sale.longitude,
        building_sqft=subject_sale.building_sqft,
        beds=subject_sale.beds,
        full_baths=subject_sale.full_baths,
        year_built=subject_sale.year_built,
    )
    comps = candidates_for(subject_sale, pool, **kwargs)
    result = estimate_value(subject, comps, as_of=subject_sale.sale_date)

    if isinstance(result, InsufficientComps):
        return result
    assert isinstance(result, ValuationEstimate)
    return Prediction(
        parcel_id=subject_sale.parcel_id,
        actual=subject_sale.sale_price,
        predicted=result.estimate,
        low=result.low,
        high=result.high,
        confidence=result.confidence,
        comps_used=len(result.comps_used),
    )


def _score(predictions: Sequence[Prediction]) -> dict[str, float]:
    errors = [p.abs_pct_error for p in predictions]
    return {
        "count": float(len(predictions)),
        "median_abs_pct_error": statistics.median(errors),
        "within_10_pct": sum(1 for e in errors if e <= 0.10) / len(errors),
        "interval_coverage": sum(1 for p in predictions if p.inside_interval)
        / len(predictions),
    }


def run_backtest(
    county: str,
    comps: Sequence[CompSale],
    *,
    since: date | None = None,
    **kwargs,
) -> BacktestResult:
    """Backtest every sale in ``comps`` against the others.

    ``since`` holds out only recent sales as subjects while keeping the whole
    pool available as comps, which is both faster and more realistic: in
    production the model always has history behind it.
    """
    usable = [comp for comp in comps if is_plausible(comp)]
    subjects = [c for c in usable if since is None or c.sale_date >= since]

    predictions: list[Prediction] = []
    skipped = 0
    for subject_sale in subjects:
        outcome = predict_one(subject_sale, usable, **kwargs)
        if isinstance(outcome, InsufficientComps):
            skipped += 1
        else:
            predictions.append(outcome)

    if not predictions:
        return BacktestResult(
            county=county,
            attempted=len(subjects),
            predicted=0,
            skipped=skipped,
            median_abs_pct_error=float("nan"),
            mean_abs_pct_error=float("nan"),
            within_10_pct=0.0,
            within_20_pct=0.0,
            interval_coverage=0.0,
            median_comps_used=0.0,
            by_confidence={},
        )

    errors = [p.abs_pct_error for p in predictions]
    by_confidence: dict[str, dict[str, float]] = {}
    for level in ("high", "medium", "low"):
        group = [p for p in predictions if p.confidence == level]
        if group:
            by_confidence[level] = _score(group)

    return BacktestResult(
        county=county,
        attempted=len(subjects),
        predicted=len(predictions),
        skipped=skipped,
        median_abs_pct_error=statistics.median(errors),
        mean_abs_pct_error=statistics.fmean(errors),
        within_10_pct=sum(1 for e in errors if e <= 0.10) / len(errors),
        within_20_pct=sum(1 for e in errors if e <= 0.20) / len(errors),
        interval_coverage=sum(1 for p in predictions if p.inside_interval)
        / len(predictions),
        median_comps_used=statistics.median(p.comps_used for p in predictions),
        by_confidence=by_confidence,
    )


def format_result(result: BacktestResult) -> str:
    """Render a backtest for the terminal."""
    def pct(value: float) -> str:
        return f"{value * 100:5.1f}%"

    lines = [
        "",
        f"  {result.county}",
        "  " + "-" * 58,
        f"  Sales attempted                {result.attempted:>12,}",
        f"  Estimated                      {result.predicted:>12,}  "
        f"({pct(result.coverage_rate)})",
        f"  Declined for thin comps        {result.skipped:>12,}",
        f"  Median comps used              {result.median_comps_used:>12,.0f}",
        "",
        f"  Median abs percent error       {pct(result.median_abs_pct_error):>12}",
        f"  Mean abs percent error         {pct(result.mean_abs_pct_error):>12}",
        f"  Within 10%                     {pct(result.within_10_pct):>12}",
        f"  Within 20%                     {pct(result.within_20_pct):>12}",
        "",
        f"  Interval coverage              {pct(result.interval_coverage):>12}",
        "    (an interquartile band should contain truth near 50%)",
    ]

    if result.by_confidence:
        lines += ["", "  By confidence      n     median err   within 10%   coverage"]
        for level in ("high", "medium", "low"):
            stats = result.by_confidence.get(level)
            if not stats:
                continue
            lines.append(
                f"    {level:<12} {int(stats['count']):>5}  "
                f"{pct(stats['median_abs_pct_error']):>10}  "
                f"{pct(stats['within_10_pct']):>10}  "
                f"{pct(stats['interval_coverage']):>9}"
            )
    lines.append("")
    return "\n".join(lines)
