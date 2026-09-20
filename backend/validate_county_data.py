"""Step 2 go/no-go: pull real sales from a county and score the coverage.

The only script in the project that touches the network. It answers one
question before any model code gets written: can this county actually supply
transaction-level sales with enough characteristics to build a comp model on?

    python backend/validate_county_data.py --county cook --property-type single_family
    python backend/validate_county_data.py --county philadelphia
    python backend/validate_county_data.py --county all --months 6

Exits 0 when every county checked is a GO, 1 otherwise, so it can run as a
data-drift alarm.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ingestion.adapter import CountyAdapter  # noqa: E402
from app.ingestion.carto import HttpCartoClient  # noqa: E402
from app.ingestion.cook_county import DOMAIN as COOK_DOMAIN  # noqa: E402
from app.ingestion.cook_county import CookCountyAdapter  # noqa: E402
from app.ingestion.coverage import evaluate_county, format_report  # noqa: E402
from app.ingestion.philadelphia import DOMAIN as PHILLY_DOMAIN  # noqa: E402
from app.ingestion.philadelphia import PhiladelphiaAdapter  # noqa: E402
from app.ingestion.records import CompSale, PropertyType  # noqa: E402
from app.ingestion.socrata import HttpSocrataClient  # noqa: E402

COUNTY_CHOICES = ("cook", "philadelphia", "all")


def months_ago(months: int) -> date:
    """Approximate date ``months`` back. Precision is irrelevant for a window."""
    return date.today() - timedelta(days=round(months * 30.44))


def build_adapters(
    name: str,
    *,
    township: str | None = None,
    zip_code: str | None = None,
    app_token: str | None = None,
) -> list[CountyAdapter]:
    """Construct the adapters for a ``--county`` choice.

    County-specific options are applied here. Everything downstream works
    through the CountyAdapter protocol and never learns which county it has,
    which is the whole point of validating a second one.
    """
    adapters: list[CountyAdapter] = []
    if name in ("cook", "all"):
        adapters.append(
            CookCountyAdapter(
                HttpSocrataClient(COOK_DOMAIN, app_token=app_token),
                township_code=township,
            )
        )
    if name in ("philadelphia", "all"):
        adapters.append(
            PhiladelphiaAdapter(HttpCartoClient(PHILLY_DOMAIN), zip_code=zip_code)
        )
    return adapters


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score a county's open sale data against the thresholds "
        "in app/ingestion/coverage.py."
    )
    parser.add_argument("--county", choices=COUNTY_CHOICES, default="all")
    parser.add_argument("--months", type=int, default=18, help="lookback window")
    parser.add_argument(
        "--limit", type=int, default=1800, help="max sales to pull and join"
    )
    parser.add_argument(
        "--property-type",
        choices=["single_family", "condo"],
        default=None,
        help="scope to one property type (v1 targets single_family)",
    )
    parser.add_argument("--township", default=None, help="Cook County township code")
    parser.add_argument("--zip", dest="zip_code", default=None, help="Philadelphia zip")
    parser.add_argument("--app-token", default=None, help="Socrata app token")
    parser.add_argument("--save", type=Path, default=None, help="write comps to JSON")
    parser.add_argument(
        "--sample", type=int, default=0, help="print N joined comps per county"
    )
    return parser


def comp_to_row(comp: CompSale) -> dict:
    """Flatten a comp for JSON output."""
    row = asdict(comp)
    row["sale_date"] = comp.sale_date.isoformat()
    row["price_per_sqft"] = (
        round(comp.price_per_sqft, 2) if comp.price_per_sqft else None
    )
    return row


def main() -> int:
    args = build_parser().parse_args()
    # argparse hands back a plain str; choices= already constrained it to the
    # two valid values, so narrow it for the type checker.
    property_type: PropertyType | None = args.property_type
    since = months_ago(args.months)

    adapters = build_adapters(
        args.county,
        township=args.township,
        zip_code=args.zip_code,
        app_token=args.app_token,
    )

    print(f"Pulling sales since {since.isoformat()} ...", file=sys.stderr)
    all_go = True
    everything: list[CompSale] = []

    for adapter in adapters:
        try:
            total = adapter.count_sales(since, property_type=property_type)
            comps = adapter.fetch_comp_sales(
                since, limit=args.limit, property_type=property_type
            )
        except (OSError, ValueError) as exc:
            print(f"error: {adapter.name}: {exc}", file=sys.stderr)
            all_go = False
            continue

        everything.extend(comps)

        if args.sample:
            for comp in comps[: args.sample]:
                print(json.dumps(comp_to_row(comp)))
            continue

        report = evaluate_county(
            adapter.name,
            window_months=args.months,
            sales_in_window=total,
            sales_sampled=min(total, args.limit),
            comps=comps,
        )
        print(format_report(report, comps))
        all_go = all_go and report.verdict == "GO"

    if args.save and everything:
        args.save.write_text(
            json.dumps([comp_to_row(c) for c in everything], indent=2),
            encoding="utf-8",
        )
        print(f"  wrote {len(everything):,} comps to {args.save}\n")

    return 0 if all_go else 1


if __name__ == "__main__":
    raise SystemExit(main())
