"""Philadelphia, Pennsylvania adapter.

Pennsylvania is a disclosure state, and Philadelphia's Office of Property
Assessment publishes `opa_properties_public` — one denormalized table carrying
the last sale price and date alongside full physical characteristics, a street
address, and a geometry column. Nothing needs joining.

That makes it a useful second county precisely because it is unlike Cook
County in every mechanical respect:

======================  ==========================  =========================
                        Cook County                 Philadelphia
======================  ==========================  =========================
Transport               Socrata (SoQL)              Carto (PostgreSQL)
Shape                   4 datasets joined on PIN    1 denormalized table
Parcel key              14-digit PIN                OPA account number
Coordinates             ``lat`` / ``lon`` columns   PostGIS geometry column
Types                   everything is a string      already typed
Arms-length screening   publisher-supplied flags    **none; built here**
Street address          not in these datasets       included
======================  ==========================  =========================

The missing arms-length flags are the substantive difference. Cook County ships
screening for bulk transfers, nominal consideration, and unusual deed types;
Philadelphia ships none, so this adapter applies its own price floor. Roughly
6,500 residential "sales" in an 18-month window record a price of $1 or $0 —
intra-family transfers, deed corrections, sheriff activity — and they would
wreck a comp model if taken at face value.

One structural caveat: the table holds each parcel's *most recent* sale, not a
transaction history. A property sold twice inside the window appears once, at
the later price. For comp selection that is the correct record anyway.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.ingestion.carto import CartoClient, quote_literal
from app.ingestion.records import (
    CompSale,
    PropertyType,
    Sale,
    month_windows,
    to_date,
    to_float,
    to_int,
)

DOMAIN = "phl.carto.com"
TABLE = "opa_properties_public"
COUNTY_NAME = "Philadelphia, PA"

#: OPA category codes. 1 is single family; 2 is multi family. Condominiums are
#: not a separate category here — they fall under single family — so unlike
#: Cook County there is no condo scope to exclude.
SINGLE_FAMILY_CATEGORY = "1"
MULTI_FAMILY_CATEGORY = "2"

#: Minimum consideration for a sale to count as arms-length. Mirrors the
#: threshold Cook County's own publisher-supplied flag uses, which makes the
#: two counties comparable rather than each drawing its own line.
MIN_ARMS_LENGTH_PRICE = 10_000

#: Columns pulled for every comp. ``ST_Y``/``ST_X`` unpack the geometry.
SELECT_COLUMNS = (
    "parcel_number, location, sale_date, sale_price, category_code, "
    "number_of_bedrooms, number_of_bathrooms, total_livable_area, "
    "total_area, year_built, zip_code, "
    "ST_Y(the_geom) AS latitude, ST_X(the_geom) AS longitude"
)


def category_for(property_type: PropertyType | None) -> str | None:
    """Map a Groundly property type onto an OPA category code.

    ``condo`` returns ``None`` rather than a code: Philadelphia does not
    categorise condominiums separately, so there is nothing to select.
    """
    if property_type == "single_family":
        return SINGLE_FAMILY_CATEGORY
    return None


def sales_where(
    since: date,
    *,
    until: date | None = None,
    property_type: PropertyType | None = None,
    zip_code: str | None = None,
) -> str:
    """Build the WHERE clause for an arms-length sales query.

    Philadelphia publishes no screening flags, so the price floor here is doing
    the work Cook County's ``sale_filter_less_than_10k`` does for it.
    """
    clauses = [
        f"sale_date >= '{since.isoformat()}'",
        f"sale_price > {MIN_ARMS_LENGTH_PRICE}",
        "the_geom IS NOT NULL",
    ]
    if until is not None:
        clauses.append(f"sale_date < '{until.isoformat()}'")
    category = category_for(property_type)
    if category:
        clauses.append(f"category_code = '{quote_literal(category)}'")
    if zip_code:
        clauses.append(f"zip_code = '{quote_literal(zip_code)}'")
    return " AND ".join(clauses)


def parse_comp(row: dict[str, Any]) -> CompSale | None:
    """Turn one OPA row into a comp, or ``None`` if it is unusable.

    Philadelphia needs no join, so parsing and joining collapse into a single
    step here. The same drop rules apply as everywhere else: no parcel, no
    date, no price, or no coordinates means it is not a comp.
    """
    parcel_id = row.get("parcel_number")
    sale_date = to_date(row.get("sale_date"))
    price = to_int(row.get("sale_price"))
    latitude = to_float(row.get("latitude"))
    longitude = to_float(row.get("longitude"))

    if not parcel_id or sale_date is None or not price or price <= 0:
        return None
    if latitude is None or longitude is None:
        return None

    baths = to_float(row.get("number_of_bathrooms"))
    # OPA records bathrooms as a decimal, where the half counts as 0.5.
    full_baths = int(baths) if baths is not None else None
    half_baths = (
        round((baths - int(baths)) * 2) if baths is not None else None
    )

    return CompSale(
        parcel_id=str(parcel_id),
        county=COUNTY_NAME,
        sale_date=sale_date,
        sale_price=price,
        property_type="single_family",
        latitude=latitude,
        longitude=longitude,
        beds=to_int(row.get("number_of_bedrooms")),
        full_baths=full_baths,
        half_baths=half_baths,
        building_sqft=to_float(row.get("total_livable_area")),
        land_sqft=to_float(row.get("total_area")),
        year_built=to_int(row.get("year_built")),
        area_code=row.get("zip_code"),
        address=row.get("location"),
    )


def parse_sale(row: dict[str, Any]) -> Sale | None:
    """Parse only the transaction part of a row."""
    parcel_id = row.get("parcel_number")
    sale_date = to_date(row.get("sale_date"))
    price = to_int(row.get("sale_price"))
    if not parcel_id or sale_date is None or not price or price <= 0:
        return None
    return Sale(parcel_id=str(parcel_id), sale_date=sale_date, sale_price=price)


class PhiladelphiaAdapter:
    """Satisfies :class:`~app.ingestion.adapter.CountyAdapter`."""

    def __init__(self, client: CartoClient, *, zip_code: str | None = None) -> None:
        self.client = client
        self.zip_code = zip_code

    @property
    def name(self) -> str:
        return (
            f"{COUNTY_NAME} (zip {self.zip_code})" if self.zip_code else COUNTY_NAME
        )

    def count_sales(
        self, since: date, *, property_type: PropertyType | None = None
    ) -> int:
        where = sales_where(
            since, property_type=property_type, zip_code=self.zip_code
        )
        rows = self.client.query(f"SELECT count(*) AS n FROM {TABLE} WHERE {where}")
        return int(rows[0]["n"]) if rows else 0

    def fetch_comp_sales(
        self,
        since: date,
        *,
        limit: int,
        property_type: PropertyType | None = None,
        stratify: bool = True,
        until: date | None = None,
    ) -> list[CompSale]:
        until = until or date.today()

        def pull(where: str, row_limit: int) -> list[dict[str, Any]]:
            return self.client.query(
                f"SELECT {SELECT_COLUMNS} FROM {TABLE} WHERE {where} "
                f"ORDER BY sale_date DESC LIMIT {row_limit}"
            )

        rows: list[dict[str, Any]] = []
        if not stratify:
            rows = pull(
                sales_where(
                    since, property_type=property_type, zip_code=self.zip_code
                ),
                limit,
            )
        else:
            windows = month_windows(since, until)
            per_window = max(1, limit // len(windows)) if windows else limit
            for start, end in windows:
                rows.extend(
                    pull(
                        sales_where(
                            start,
                            until=end,
                            property_type=property_type,
                            zip_code=self.zip_code,
                        ),
                        per_window,
                    )
                )

        return [comp for comp in map(parse_comp, rows) if comp is not None]
