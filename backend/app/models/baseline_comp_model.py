"""v1 valuation: weighted nearest comparable sales.

No training, no model file, no fitting step. Every estimate is a weighted
median of real nearby sales, and the weights are arithmetic a reader can check
by hand. That explainability is the point: when this says a house is worth
$340,000 it can name the six sales it used and how much each one counted.

The spec keeps this on permanently as a sanity-check baseline even after the
gradient-boosted regressor exists, because a comp model that disagrees loudly
with a trained model is a useful alarm.

Three things decide how much a comp counts:

* **Distance.** Half-life weighting, so a sale twice the half-life away counts
  a quarter as much. Hard-cut at a radius beyond which a sale is not a comp.
* **Recency.** Same shape, in months. A sale from three years ago in a moving
  market is barely evidence.
* **Similarity.** Size dominates; bedrooms, bathrooms and age adjust.

The output is always an interval, never a point. Where comps are sparse or far
away, the interval widens and the result is flagged low confidence rather than
being quietly reported as if it were precise.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Sequence

from app.ingestion.records import CompSale

EARTH_RADIUS_MILES = 3958.8

#: Distance at which a comp counts half as much as one next door.
DISTANCE_HALF_LIFE_MILES = 0.5

#: Beyond this, a sale is not a comparable at any weight.
MAX_DISTANCE_MILES = 3.0

#: Months at which a sale counts half as much as one from today.
RECENCY_HALF_LIFE_MONTHS = 9.0

#: Sales older than this are excluded outright.
MAX_AGE_MONTHS = 24.0

#: A comp whose size differs by this fraction counts half as much.
SIZE_HALF_LIFE_RATIO = 0.25

#: Fewest comps for an estimate to be produced at all.
MIN_COMPS = 3

#: How many of the heaviest comps actually feed the estimate. Chosen by
#: backtest rather than taste: on Philadelphia, 12 gives a median error of
#: 21.9% against 23.7% at 25 and 24.2% at 5. Too few and one odd sale swings
#: the answer; too many and distant, dissimilar sales dilute it.
DEFAULT_MAX_COMPS = 12

#: Below this, the estimate is flagged low confidence however tight it looks.
LOW_CONFIDENCE_COMPS = 6

#: Narrowest the published interval may be, as a fraction of the estimate on
#: each side.
#:
#: Where every comp agrees the interquartile band collapses to nothing, and an
#: interval of zero width is a point value wearing a range's clothing. Backtests
#: put the median error of even high-confidence estimates near 12%, so the model
#: is never as certain as perfectly agreeing comps make it look. The floor keeps
#: the published range from claiming a precision the model has never
#: demonstrated.
MIN_INTERVAL_HALF_WIDTH = 0.05

#: Price-per-square-foot bounds. Philadelphia's assessor publishes an unusable
#: livable-area figure for roughly 4% of records — a $780k sale recorded against
#: 183 sq ft — and untrimmed those distort any weighted average. See
#: docs/data-validation.md.
MIN_PLAUSIBLE_PPSF = 20.0
MAX_PLAUSIBLE_PPSF = 1500.0

Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class SubjectProperty:
    """The property being valued."""

    latitude: float
    longitude: float
    building_sqft: float | None = None
    beds: int | None = None
    full_baths: int | None = None
    year_built: int | None = None


@dataclass(frozen=True)
class WeightedComp:
    """One comparable sale and why it counted as much as it did.

    Every component is kept rather than just the product, so a user asking
    "why is that a comp?" gets an answer instead of a number.
    """

    comp: CompSale
    distance_miles: float
    age_months: float
    distance_weight: float
    recency_weight: float
    similarity_weight: float

    @property
    def weight(self) -> float:
        return self.distance_weight * self.recency_weight * self.similarity_weight

    @property
    def price_per_sqft(self) -> float | None:
        return self.comp.price_per_sqft


@dataclass(frozen=True)
class ValuationEstimate:
    """A valuation, always as a range.

    ``low`` and ``high`` are a weighted interquartile-style band, not a
    standard error. They say where comparable sales actually landed, which is
    an honest thing to show a user and does not pretend to a distribution the
    data does not support.
    """

    estimate: float
    low: float
    high: float
    confidence: Confidence
    comps_used: tuple[WeightedComp, ...] = ()
    price_per_sqft: float | None = None
    notes: tuple[str, ...] = ()

    @property
    def spread_ratio(self) -> float:
        """Interval width as a fraction of the estimate."""
        return (self.high - self.low) / self.estimate if self.estimate else 0.0


@dataclass(frozen=True)
class InsufficientComps:
    """No estimate could be made honestly.

    Returned rather than raising, and rather than returning a number with a
    wide interval. The spec is explicit: when comps are too sparse or too far
    to trust, hand back the closest available sales flagged low confidence
    instead of a falsely precise figure.
    """

    reason: str
    nearest: tuple[WeightedComp, ...] = ()
    comps_considered: int = 0


def haversine_miles(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Great-circle distance in miles.

    Exact enough at neighbourhood scale, and unlike a flat-earth approximation
    it does not quietly break as longitude degrees narrow toward the poles.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def months_between(earlier: date, later: date) -> float:
    """Approximate months between two dates, never negative."""
    days = (later - earlier).days
    return max(days / 30.44, 0.0)


def half_life_weight(value: float, half_life: float) -> float:
    """Weight that halves every ``half_life`` units.

    Smooth and unbounded below, so nothing falls off a cliff: a comp at 0.51
    miles is worth fractionally less than one at 0.49, not half as much.
    """
    if half_life <= 0:
        raise ValueError(f"half_life must be positive, got {half_life!r}")
    return 0.5 ** (max(value, 0.0) / half_life)


def similarity_weight(subject: SubjectProperty, comp: CompSale) -> float:
    """How alike two properties are, in [0, 1].

    Size carries the most weight because it drives price more than any other
    single feature. Bedrooms, bathrooms and age apply smaller penalties. A
    missing field is not penalised — an unknown build year should not make a
    good comp look bad.
    """
    weight = 1.0

    if subject.building_sqft and comp.building_sqft:
        ratio = abs(comp.building_sqft - subject.building_sqft) / subject.building_sqft
        weight *= half_life_weight(ratio, SIZE_HALF_LIFE_RATIO)

    if subject.beds is not None and comp.beds is not None:
        weight *= half_life_weight(abs(comp.beds - subject.beds), 2.0)

    if subject.full_baths is not None and comp.full_baths is not None:
        weight *= half_life_weight(abs(comp.full_baths - subject.full_baths), 2.0)

    if subject.year_built and comp.year_built:
        weight *= half_life_weight(abs(comp.year_built - subject.year_built), 30.0)

    return weight


def is_plausible(comp: CompSale) -> bool:
    """Reject records whose implied price per square foot cannot be real.

    This is a data-quality guard, not a market judgement. Philadelphia's
    livable-area field is wrong often enough that trimming is a requirement
    rather than a refinement.
    """
    ppsf = comp.price_per_sqft
    if ppsf is None:
        return False
    return MIN_PLAUSIBLE_PPSF <= ppsf <= MAX_PLAUSIBLE_PPSF


def weigh_comps(
    subject: SubjectProperty,
    comps: Sequence[CompSale],
    *,
    as_of: date,
    max_distance_miles: float = MAX_DISTANCE_MILES,
    max_age_months: float = MAX_AGE_MONTHS,
) -> list[WeightedComp]:
    """Score every comp, dropping those outside the distance or age cutoffs.

    Returned sorted by weight, heaviest first, so a caller can show the top few
    without re-sorting.
    """
    weighted: list[WeightedComp] = []
    for comp in comps:
        if not is_plausible(comp):
            continue

        distance = haversine_miles(
            subject.latitude, subject.longitude, comp.latitude, comp.longitude
        )
        if distance > max_distance_miles:
            continue

        age = months_between(comp.sale_date, as_of)
        if age > max_age_months:
            continue

        weighted.append(
            WeightedComp(
                comp=comp,
                distance_miles=distance,
                age_months=age,
                distance_weight=half_life_weight(distance, DISTANCE_HALF_LIFE_MILES),
                recency_weight=half_life_weight(age, RECENCY_HALF_LIFE_MONTHS),
                similarity_weight=similarity_weight(subject, comp),
            )
        )

    weighted.sort(key=lambda w: w.weight, reverse=True)
    return weighted


def weighted_quantile(
    values: Sequence[float], weights: Sequence[float], quantile: float
) -> float:
    """Weighted quantile by linear interpolation on cumulative weight.

    A weighted median rather than a weighted mean: one mispriced sale should
    not drag the answer, and in thin comp sets that happens often.
    """
    if not values:
        raise ValueError("no values to take a quantile of")
    if len(values) != len(weights):
        raise ValueError("values and weights must be the same length")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError(f"quantile must be in [0, 1], got {quantile!r}")

    pairs = sorted(zip(values, weights), key=lambda pair: pair[0])
    total = sum(weight for _, weight in pairs)
    if total <= 0:
        return statistics.median(values)

    # Each value sits at the midpoint of its own block of weight. Without the
    # half-weight offset the estimate is biased low by half a block, which with
    # equal weights puts the "median" of 1, 2, 3 at 1.5 rather than 2.
    positions: list[float] = []
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        positions.append((cumulative - 0.5 * weight) / total)

    if quantile <= positions[0]:
        return pairs[0][0]
    if quantile >= positions[-1]:
        return pairs[-1][0]

    for index in range(1, len(positions)):
        if quantile <= positions[index]:
            lower_pos, upper_pos = positions[index - 1], positions[index]
            lower_val, upper_val = pairs[index - 1][0], pairs[index][0]
            if upper_pos == lower_pos:
                return upper_val
            fraction = (quantile - lower_pos) / (upper_pos - lower_pos)
            return lower_val + (upper_val - lower_val) * fraction
    return pairs[-1][0]


def _confidence(
    weighted: Sequence[WeightedComp], spread_ratio: float
) -> tuple[Confidence, list[str]]:
    """Grade an estimate, and say why.

    Count and agreement both matter. Six comps that disagree wildly deserve no
    more trust than three that agree, and the notes make the reason legible
    rather than leaving the user to guess at a label.
    """
    notes: list[str] = []
    level: Confidence = "high"

    if len(weighted) < LOW_CONFIDENCE_COMPS:
        level = "low"
        notes.append(f"only {len(weighted)} comparable sales found")

    median_distance = statistics.median(w.distance_miles for w in weighted)
    if median_distance > 1.0:
        level = "low" if level == "low" else "medium"
        notes.append(f"comps average {median_distance:.1f} miles away")

    if spread_ratio > 0.5:
        level = "low"
        notes.append("comparable sales disagree widely")
    elif spread_ratio > 0.3 and level == "high":
        level = "medium"
        notes.append("moderate spread across comparable sales")

    median_age = statistics.median(w.age_months for w in weighted)
    if median_age > 12:
        level = "low" if level == "low" else "medium"
        notes.append(f"comps are a median {median_age:.0f} months old")

    return level, notes


def estimate_value(
    subject: SubjectProperty,
    comps: Sequence[CompSale],
    *,
    as_of: date | None = None,
    max_comps: int = DEFAULT_MAX_COMPS,
    max_distance_miles: float = MAX_DISTANCE_MILES,
) -> ValuationEstimate | InsufficientComps:
    """Estimate a property's value from comparable sales.

    Values price per square foot rather than price outright when the subject's
    size is known, then multiplies back up. A 900 sq ft house and a 2,400 sq ft
    house on the same street have wildly different prices but similar rates,
    so the rate is the more stable thing to average.

    Returns :class:`InsufficientComps` rather than a number when the evidence
    will not support one.
    """
    as_of = as_of or date.today()

    weighted = weigh_comps(
        subject,
        comps,
        as_of=as_of,
        max_distance_miles=max_distance_miles,
    )
    if len(weighted) < MIN_COMPS:
        return InsufficientComps(
            reason=(
                f"found {len(weighted)} usable comparable sales within "
                f"{max_distance_miles:g} miles; need at least {MIN_COMPS}"
            ),
            nearest=tuple(weighted),
            comps_considered=len(comps),
        )

    selected = weighted[:max_comps]
    weights = [w.weight for w in selected]

    use_ppsf = bool(subject.building_sqft)
    if use_ppsf:
        assert subject.building_sqft is not None
        values = [w.price_per_sqft for w in selected]
        assert all(v is not None for v in values)
        basis = [float(v) for v in values if v is not None]
        scale = subject.building_sqft
    else:
        basis = [float(w.comp.sale_price) for w in selected]
        scale = 1.0

    middle = weighted_quantile(basis, weights, 0.5)
    low = weighted_quantile(basis, weights, 0.25)
    high = weighted_quantile(basis, weights, 0.75)

    # Never publish a band tighter than the model's own demonstrated accuracy.
    floor = abs(middle) * MIN_INTERVAL_HALF_WIDTH
    low = min(low, middle - floor)
    high = max(high, middle + floor)

    estimate = middle * scale
    spread = (high - low) * scale / estimate if estimate else 0.0
    confidence, notes = _confidence(selected, spread)

    if not use_ppsf:
        notes.append("subject size unknown; valued on sale price, not rate")

    return ValuationEstimate(
        estimate=estimate,
        low=low * scale,
        high=high * scale,
        confidence=confidence,
        comps_used=tuple(selected),
        price_per_sqft=middle if use_ppsf else None,
        notes=tuple(notes),
    )
