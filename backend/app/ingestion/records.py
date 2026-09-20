"""County-neutral record types and field parsers.

Every county adapter produces these, whatever shape its source data arrives in.
Cook County joins four Socrata datasets on a parcel number; Philadelphia reads
one denormalized Carto table. Both end up as :class:`CompSale`, so the comp
model and the coverage report never learn which county a record came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

#: What was sold. Counties describe far more categories than this, but these
#: are the two the valuation model distinguishes.
PropertyType = Literal["single_family", "condo"]


# --------------------------------------------------------------------------
# Field parsing. Open data portals return numbers as strings more often than
# not, and each has its own idea of what "missing" looks like.
# --------------------------------------------------------------------------

#: Values that mean "no data" across the portals seen so far.
MISSING_VALUES = frozenset({None, "", "NA", "nan", "NULL", "0000"})


def to_float(value: Any) -> float | None:
    """Parse a numeric field, treating blanks and junk as missing."""
    if value in MISSING_VALUES:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    """Parse an integer field that may arrive as ``'3.0'``."""
    parsed = to_float(value)
    return None if parsed is None else int(parsed)


def to_date(value: Any) -> date | None:
    """Parse an ISO timestamp, with or without a zone or fractional seconds."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Sale:
    """One recorded transaction.

    ``parcel_id`` is whatever the county calls its parcel key — a PIN in Cook
    County, an OPA account number in Philadelphia. It is opaque above the
    adapter.
    """

    parcel_id: str
    sale_date: date
    sale_price: int


@dataclass(frozen=True)
class Characteristics:
    """Physical attributes of a parcel."""

    parcel_id: str
    property_type: PropertyType
    beds: int | None = None
    full_baths: int | None = None
    half_baths: int | None = None
    building_sqft: float | None = None
    land_sqft: float | None = None
    year_built: int | None = None


@dataclass(frozen=True)
class Location:
    """Where the parcel is, for distance weighting."""

    parcel_id: str
    latitude: float
    longitude: float
    area_code: str | None = None
    address: str | None = None


@dataclass(frozen=True)
class CompSale:
    """A sale joined to its characteristics and location.

    This is the unit the v1 weighted-comp model consumes. A record only exists
    if every join succeeded, so downstream code never has to handle a comp with
    no location or no size.
    """

    parcel_id: str
    county: str
    sale_date: date
    sale_price: int
    property_type: PropertyType
    latitude: float
    longitude: float
    beds: int | None = None
    full_baths: int | None = None
    half_baths: int | None = None
    building_sqft: float | None = None
    land_sqft: float | None = None
    year_built: int | None = None
    area_code: str | None = None
    address: str | None = None

    @property
    def price_per_sqft(self) -> float | None:
        """Price per finished square foot, or ``None`` when size is unknown."""
        if not self.building_sqft:
            return None
        return self.sale_price / self.building_sqft


def join_sales(
    sales: list[Sale],
    characteristics: dict[str, Characteristics],
    locations: dict[str, Location],
    *,
    county: str,
) -> list[CompSale]:
    """Combine the three sources into comp records.

    A sale is dropped unless both its characteristics and its location are
    present. That is deliberate: a comp the model cannot place on a map or
    size is not a comp.
    """
    joined: list[CompSale] = []
    for sale in sales:
        chars = characteristics.get(sale.parcel_id)
        location = locations.get(sale.parcel_id)
        if chars is None or location is None:
            continue
        joined.append(
            CompSale(
                parcel_id=sale.parcel_id,
                county=county,
                sale_date=sale.sale_date,
                sale_price=sale.sale_price,
                property_type=chars.property_type,
                latitude=location.latitude,
                longitude=location.longitude,
                beds=chars.beds,
                full_baths=chars.full_baths,
                half_baths=chars.half_baths,
                building_sqft=chars.building_sqft,
                land_sqft=chars.land_sqft,
                year_built=chars.year_built,
                area_code=location.area_code,
                address=location.address,
            )
        )
    return joined


def month_windows(since: date, until: date) -> list[tuple[date, date]]:
    """Split a date range into calendar months, half-open on the right.

    Used by every adapter to stratify a sample across the window. Sorting by
    date and taking the first N returns only the most recent slice, which is a
    sample of last quarter rather than of the window.
    """
    windows: list[tuple[date, date]] = []
    cursor = since.replace(day=1)
    while cursor < until:
        year, month = divmod(cursor.month, 12)
        nxt = cursor.replace(year=cursor.year + year, month=month + 1, day=1)
        windows.append((max(cursor, since), min(nxt, until)))
        cursor = nxt
    return windows
