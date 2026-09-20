"""Pull comparable sales from a validated county and cache them to disk.

The API reads this file at startup. A flat file rather than Postgres is a
deliberate stopping point: a county's recent single-family sales are tens of
thousands of rows, which fits in memory comfortably, and standing up a database
before the model has earned one adds an operational dependency with nothing to
show for it. `InMemoryCompStore` and a future Postgres store satisfy the same
`CompStore` protocol, so that swap does not reach the model or the API.

    python backend/load_comps.py --county all --limit 8000
    GROUNDLY_COMPS_FILE=comps.json python -m uvicorn app.main:app
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ingestion.records import CompSale  # noqa: E402
from app.models.baseline_comp_model import is_plausible  # noqa: E402
from validate_county_data import build_adapters, months_ago  # noqa: E402

DEFAULT_PATH = Path("comps.json")


def to_row(comp: CompSale) -> dict:
    row = asdict(comp)
    row["sale_date"] = comp.sale_date.isoformat()
    return row


def from_row(row: dict) -> CompSale:
    data = dict(row)
    data["sale_date"] = date.fromisoformat(data["sale_date"])
    return CompSale(**data)


def load_file(path: Path) -> list[CompSale]:
    """Read a cached comp file. Returns an empty list if it is not there."""
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [from_row(row) for row in rows]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cache county comps to a file the API can load."
    )
    parser.add_argument("--county", choices=("cook", "philadelphia", "all"),
                        default="all")
    parser.add_argument("--months", type=int, default=18)
    parser.add_argument("--limit", type=int, default=6000,
                        help="sales per county")
    parser.add_argument("--township", default=None)
    parser.add_argument("--zip", dest="zip_code", default=None)
    parser.add_argument("--app-token", default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_PATH)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    since = months_ago(args.months)
    adapters = build_adapters(
        args.county,
        township=args.township,
        zip_code=args.zip_code,
        app_token=args.app_token,
    )

    everything: list[CompSale] = []
    for adapter in adapters:
        print(f"Pulling {adapter.name} ...", file=sys.stderr)
        try:
            comps = adapter.fetch_comp_sales(
                since, limit=args.limit, property_type="single_family"
            )
        except (OSError, ValueError) as exc:
            print(f"error: {adapter.name}: {exc}", file=sys.stderr)
            return 1

        # Trim here rather than at query time so the cached file is already
        # clean and the API never has to think about county data quality.
        usable = [comp for comp in comps if is_plausible(comp)]
        dropped = len(comps) - len(usable)
        print(
            f"  {len(usable):,} usable ({dropped:,} dropped as implausible)",
            file=sys.stderr,
        )
        everything.extend(usable)

    args.out.write_text(
        json.dumps([to_row(c) for c in everything], indent=2), encoding="utf-8"
    )
    print(f"wrote {len(everything):,} comps to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
