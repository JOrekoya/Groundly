"""The step 2 go/no-go: is a county's data good enough to build a comp model on?

Pure functions over already-fetched records, so the verdict logic is testable
without touching a network. The thresholds below are the bar a county has to
clear before any model code is written against it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from app.ingestion.records import CompSale

#: Minimum arms-length sales in the window. Below this the nearest-comp model
#: has too little to draw on once filtered by type and neighbourhood.
MIN_SALES = 2_000

#: Share of sales that must survive the join to characteristics and location.
#: A low rate means the county publishes prices without saying what was sold.
MIN_JOIN_RATE = 0.80

#: Size drives price more than any other single feature, so it is required.
MIN_SQFT_RATE = 0.80

#: Distance weighting is the core of the v1 model; without coordinates there
#: is no model.
MIN_GEO_RATE = 0.95

#: Sanity bound on the median. A county whose median sale is $15k is almost
#: certainly still full of nominal transfers.
MIN_PLAUSIBLE_MEDIAN = 50_000


@dataclass(frozen=True)
class CoverageReport:
    """What a county offers, and whether it clears the bar.

    ``sales_in_window`` is the whole population and drives the volume check.
    ``sales_sampled`` is how many were actually pulled and joined, and is the
    denominator for the join rate. Keeping them apart matters: pulling a 1,500
    row sample from a 70,000 sale county must not read as a county with only
    1,500 sales.
    """

    county: str
    window_months: int
    sales_in_window: int
    sales_sampled: int
    joined: int
    with_beds: int
    with_baths: int
    with_sqft: int
    with_year_built: int
    with_location: int
    median_price: float | None
    median_price_per_sqft: float | None
    property_type_counts: dict[str, int] = field(default_factory=dict)
    failures: tuple[str, ...] = ()

    @property
    def join_rate(self) -> float:
        return self.joined / self.sales_sampled if self.sales_sampled else 0.0

    @property
    def verdict(self) -> str:
        return "GO" if not self.failures else "NO-GO"

    def _rate(self, count: int) -> float:
        return count / self.joined if self.joined else 0.0

    @property
    def sqft_rate(self) -> float:
        return self._rate(self.with_sqft)

    @property
    def beds_rate(self) -> float:
        return self._rate(self.with_beds)

    @property
    def baths_rate(self) -> float:
        return self._rate(self.with_baths)

    @property
    def geo_rate(self) -> float:
        return self._rate(self.with_location)


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def evaluate_county(
    county: str,
    *,
    window_months: int,
    sales_in_window: int,
    comps: list[CompSale],
    sales_sampled: int | None = None,
) -> CoverageReport:
    """Score a county's pulled comps against the thresholds.

    ``sales_sampled`` defaults to ``sales_in_window`` for the common case where
    everything in the window was pulled.
    """
    if sales_sampled is None:
        sales_sampled = sales_in_window
    joined = len(comps)
    with_sqft = sum(1 for c in comps if c.building_sqft)
    with_beds = sum(1 for c in comps if c.beds)
    with_baths = sum(1 for c in comps if c.full_baths)
    with_year = sum(1 for c in comps if c.year_built)
    with_location = sum(
        1 for c in comps if c.latitude is not None and c.longitude is not None
    )

    prices = [float(c.sale_price) for c in comps]
    ppsf = [c.price_per_sqft for c in comps if c.price_per_sqft]
    median_price = _median(prices)

    type_counts: dict[str, int] = {}
    for comp in comps:
        type_counts[comp.property_type] = type_counts.get(comp.property_type, 0) + 1

    report = CoverageReport(
        county=county,
        window_months=window_months,
        sales_in_window=sales_in_window,
        sales_sampled=sales_sampled,
        joined=joined,
        with_beds=with_beds,
        with_baths=with_baths,
        with_sqft=with_sqft,
        with_year_built=with_year,
        with_location=with_location,
        median_price=median_price,
        median_price_per_sqft=_median([p for p in ppsf if p is not None]),
        property_type_counts=type_counts,
    )

    failures: list[str] = []
    if sales_in_window < MIN_SALES:
        failures.append(
            f"only {sales_in_window:,} arms-length sales in {window_months} "
            f"months (need {MIN_SALES:,})"
        )
    if report.join_rate < MIN_JOIN_RATE:
        failures.append(
            f"join rate {report.join_rate:.0%} below {MIN_JOIN_RATE:.0%}: prices "
            "published without matching characteristics"
        )
    if report.sqft_rate < MIN_SQFT_RATE:
        failures.append(
            f"building size present on only {report.sqft_rate:.0%} of comps "
            f"(need {MIN_SQFT_RATE:.0%})"
        )
    if report.geo_rate < MIN_GEO_RATE:
        failures.append(
            f"coordinates present on only {report.geo_rate:.0%} of comps "
            f"(need {MIN_GEO_RATE:.0%})"
        )
    if median_price is not None and median_price < MIN_PLAUSIBLE_MEDIAN:
        failures.append(
            f"median sale price ${median_price:,.0f} is implausibly low; the "
            "arms-length filter is probably not working"
        )

    return CoverageReport(**{**report.__dict__, "failures": tuple(failures)})


def completeness_by_type(comps: list[CompSale]) -> dict[str, dict[str, float]]:
    """Field completeness split by property type.

    Worth breaking out because an aggregate number can hide a whole category
    being unusable: a county can look 78% complete overall while one property
    type is at 100% and another at 39%.
    """
    by_type: dict[str, list[CompSale]] = {}
    for comp in comps:
        by_type.setdefault(comp.property_type, []).append(comp)

    result: dict[str, dict[str, float]] = {}
    for name, group in sorted(by_type.items()):
        total = len(group)
        result[name] = {
            "count": float(total),
            "sqft": sum(1 for c in group if c.building_sqft) / total,
            "beds": sum(1 for c in group if c.beds) / total,
            "baths": sum(1 for c in group if c.full_baths) / total,
        }
    return result


def format_report(report: CoverageReport, comps: list[CompSale] | None = None) -> str:
    """Render a report for the terminal."""

    def pct(rate: float) -> str:
        return f"{rate * 100:5.1f}%"

    def money(value: float | None) -> str:
        return "n/a" if value is None else f"${value:,.0f}"

    lines = [
        "",
        f"  {report.county} - trailing {report.window_months} months",
        "  " + "-" * 58,
        f"  Arms-length sales in window    {report.sales_in_window:>12,}",
        f"  Sampled and joined             {report.joined:>12,}  "
        f"({pct(report.join_rate)} of {report.sales_sampled:,})",
        "",
        f"  Building size present          {pct(report.sqft_rate):>12}",
        f"  Bedrooms present               {pct(report.beds_rate):>12}",
        f"  Bathrooms present              {pct(report.baths_rate):>12}",
        f"  Coordinates present            {pct(report.geo_rate):>12}",
        "",
        f"  Median sale price              {money(report.median_price):>12}",
        f"  Median price per sq ft         {money(report.median_price_per_sqft):>12}",
    ]

    if comps:
        lines.append("")
        lines.append("  By property type       count     sqft    beds   baths")
        for name, stats in completeness_by_type(comps).items():
            lines.append(
                f"    {name.replace('_', ' '):<18} {int(stats['count']):>6}  "
                f"{stats['sqft'] * 100:5.0f}%  {stats['beds'] * 100:5.0f}%  "
                f"{stats['baths'] * 100:5.0f}%"
            )

    lines += ["  " + "-" * 58, f"  VERDICT                        {report.verdict:>12}"]
    for failure in report.failures:
        lines.append(f"    - {failure}")
    lines.append("")
    return "\n".join(lines)
