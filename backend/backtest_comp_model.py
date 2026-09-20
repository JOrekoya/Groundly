"""Backtest the v1 comp model against real county sales.

Pulls sales from a validated county, hides each price in turn, and predicts it
from the sales that had already happened. Reports accuracy and — just as
importantly — whether the published interval is honest.

    python backend/backtest_comp_model.py --county cook
    python backend/backtest_comp_model.py --county philadelphia --limit 3000
    python backend/backtest_comp_model.py --county all --subjects 400

Exits 0 when every county backtested clears the accuracy bar, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ingestion.records import CompSale  # noqa: E402
from app.models.backtest import (  # noqa: E402
    BacktestResult,
    format_result,
    run_backtest,
)
from validate_county_data import build_adapters, months_ago  # noqa: E402

#: The bar is set on high-confidence estimates, not on all of them.
#:
#: Overall median error runs near 22% in both validated counties, and that is
#: not a number to act on. But the model grades its own output, and the grade
#: is strongly predictive: high-confidence estimates land near 12% while
#: low-confidence ones are near 34%. What matters is therefore whether the
#: estimates the model vouches for are good, and whether it is honest about
#: the rest — not the blended average of the two.
MAX_HIGH_CONFIDENCE_MDAPE = 0.15

#: Reported, not gated. Assessor characteristics carry no condition or finish
#: quality, so a comp model alone cannot reach single digits here.
REPORTED_OVERALL_MDAPE_CEILING = 0.30

#: The published interval is a weighted interquartile band, so a calibrated one
#: contains the true price near half the time. Far above means the band is
#: uselessly wide; far below means it is lying about its own uncertainty.
MIN_INTERVAL_COVERAGE = 0.40
MAX_INTERVAL_COVERAGE = 0.60


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backtest the weighted nearest-comp model on real sales."
    )
    parser.add_argument("--county", choices=("cook", "philadelphia", "all"),
                        default="all")
    parser.add_argument("--months", type=int, default=18, help="pool window")
    parser.add_argument("--limit", type=int, default=4000,
                        help="sales to pull as the comp pool")
    parser.add_argument("--subjects", type=int, default=0,
                        help="hold out only the most recent N sales as subjects")
    parser.add_argument("--subject-months", type=int, default=6,
                        help="hold out sales from the last N months as subjects")
    parser.add_argument("--township", default=None)
    parser.add_argument("--zip", dest="zip_code", default=None)
    parser.add_argument("--app-token", default=None)
    return parser


def check(result: BacktestResult) -> list[str]:
    """Score a backtest against the bars above, returning every failure."""
    failures: list[str] = []

    if result.predicted == 0:
        return ["no sale had enough prior comps to estimate"]

    high = result.by_confidence.get("high")
    if high is None:
        failures.append("no estimate earned high confidence")
    elif high["median_abs_pct_error"] > MAX_HIGH_CONFIDENCE_MDAPE:
        failures.append(
            f"high-confidence median error {high['median_abs_pct_error']:.1%} "
            f"exceeds {MAX_HIGH_CONFIDENCE_MDAPE:.0%}"
        )

    if result.median_abs_pct_error > REPORTED_OVERALL_MDAPE_CEILING:
        failures.append(
            f"overall median error {result.median_abs_pct_error:.1%} exceeds "
            f"{REPORTED_OVERALL_MDAPE_CEILING:.0%}"
        )

    coverage = result.interval_coverage
    if not MIN_INTERVAL_COVERAGE <= coverage <= MAX_INTERVAL_COVERAGE:
        failures.append(
            f"interval coverage {coverage:.1%} outside "
            f"{MIN_INTERVAL_COVERAGE:.0%}-{MAX_INTERVAL_COVERAGE:.0%}: the "
            "published range is not calibrated"
        )

    # The grade must actually mean something.
    low = result.by_confidence.get("low")
    if high and low and high["median_abs_pct_error"] >= low["median_abs_pct_error"]:
        failures.append(
            "high-confidence estimates are no better than low-confidence ones; "
            "the confidence signal is not working"
        )

    return failures


def subjects_cutoff(months: int) -> date:
    return date.today() - timedelta(days=round(months * 30.44))


def main() -> int:
    args = build_parser().parse_args()
    since = months_ago(args.months)
    adapters = build_adapters(
        args.county,
        township=args.township,
        zip_code=args.zip_code,
        app_token=args.app_token,
    )

    print(
        f"Pulling comps since {since.isoformat()} ...",
        file=sys.stderr,
    )

    all_pass = True
    for adapter in adapters:
        try:
            comps: list[CompSale] = adapter.fetch_comp_sales(
                since, limit=args.limit, property_type="single_family"
            )
        except (OSError, ValueError) as exc:
            print(f"error: {adapter.name}: {exc}", file=sys.stderr)
            all_pass = False
            continue

        if args.subjects:
            pool = sorted(comps, key=lambda c: c.sale_date)
            subjects_from = pool[-args.subjects :][0].sale_date
        else:
            subjects_from = subjects_cutoff(args.subject_months)

        print(
            f"  {adapter.name}: {len(comps):,} comps in pool, "
            f"subjects from {subjects_from.isoformat()}",
            file=sys.stderr,
        )

        result: BacktestResult = run_backtest(
            adapter.name, comps, since=subjects_from
        )
        print(format_result(result))

        failures = check(result)
        if failures:
            for failure in failures:
                print(f"  FAIL: {failure}")
            all_pass = False
        else:
            high = result.by_confidence["high"]
            print(
                f"  PASS: high-confidence median error "
                f"{high['median_abs_pct_error']:.1%}, interval coverage "
                f"{result.interval_coverage:.1%}"
            )
        print()

    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
