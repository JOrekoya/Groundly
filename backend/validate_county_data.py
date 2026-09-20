"""Step 2 go/no-go: pull real sales from a county and score the coverage.

This is the only script in the project that touches the network. It answers
one question before any model code gets written: can this county actually
supply transaction-level sales with enough characteristics to build a comp
model on?

    python backend/validate_county_data.py
    python backend/validate_county_data.py --months 6 --limit 1000
    python backend/validate_county_data.py --township 70 --save comps.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ingestion import cook_county  # noqa: E402
from app.ingestion.coverage import evaluate_county, format_report  # noqa: E402
from app.ingestion.socrata import HttpSocrataClient  # noqa: E402

#: Assessor characteristics are published per assessment year. The current year
#: is the right join target; an older one silently loses recent construction.
DEFAULT_ASSESSMENT_YEAR = 2026


def months_ago(months: int) -> date:
    """Approximate date ``months`` back. Precision is irrelevant for a window."""
    return date.today() - timedelta(days=round(months * 30.44))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--months", type=int, default=18, help="lookback window")
    parser.add_argument(
        "--limit", type=int, default=5000, help="max sales to pull and join"
    )
    parser.add_argument("--township", default=None, help="Cook County township code")
    parser.add_argument(
        "--property-type",
        choices=["single_family", "condo"],
        default=None,
        help="scope to one property type (v1 targets single_family)",
    )
    parser.add_argument(
        "--assessment-year", type=int, default=DEFAULT_ASSESSMENT_YEAR
    )
    parser.add_argument("--app-token", default=None, help="Socrata app token")
    parser.add_argument("--save", type=Path, default=None, help="write comps to JSON")
    parser.add_argument(
        "--sample", type=int, default=0, help="print N joined comps and exit"
    )
    return parser


def count_arms_length_sales(
    client: HttpSocrataClient,
    since: date,
    township: str | None,
    property_type: str | None,
) -> int:
    """Total matching sales in the window, before any join.

    Counted separately from the pull so the join rate is measured against the
    whole population rather than against the sample size.
    """
    where = cook_county.sales_where(
        since, township_code=township, property_type=property_type
    )
    rows = client.query(cook_county.SALES_DATASET, select="count(1)", where=where)
    return int(rows[0]["count_1"]) if rows else 0


def main() -> int:
    args = build_parser().parse_args()
    since = months_ago(args.months)
    client = HttpSocrataClient(cook_county.DOMAIN, app_token=args.app_token)

    label = "Cook County, IL"
    scope = []
    if args.property_type:
        scope.append(args.property_type.replace("_", " "))
    if args.township:
        scope.append(f"township {args.township}")
    if scope:
        label += " - " + ", ".join(scope)

    print(f"Pulling sales since {since.isoformat()} ...", file=sys.stderr)
    try:
        total_sales = count_arms_length_sales(
            client, since, args.township, args.property_type
        )
        comps = cook_county.fetch_comp_sales(
            client,
            since=since,
            assessment_year=args.assessment_year,
            limit=args.limit,
            township_code=args.township,
            property_type=args.property_type,
        )
    except OSError as exc:
        print(f"error: could not reach {cook_county.DOMAIN}: {exc}", file=sys.stderr)
        return 1

    if args.sample:
        for comp in comps[: args.sample]:
            row = asdict(comp)
            row["sale_date"] = comp.sale_date.isoformat()
            row["price_per_sqft"] = (
                round(comp.price_per_sqft, 2) if comp.price_per_sqft else None
            )
            print(json.dumps(row))
        return 0

    # Volume is judged on the whole window; the join rate on what was pulled.
    report = evaluate_county(
        label,
        window_months=args.months,
        sales_in_window=total_sales,
        sales_sampled=min(total_sales, args.limit),
        comps=comps,
    )
    print(format_report(report, comps))

    if args.save:
        payload = []
        for comp in comps:
            row = asdict(comp)
            row["sale_date"] = comp.sale_date.isoformat()
            payload.append(row)
        args.save.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"  wrote {len(payload):,} comps to {args.save}\n")

    return 0 if report.verdict == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
