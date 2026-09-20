"""Cook County, Illinois adapter.

Cook County publishes transaction-level sale prices with assessor
characteristics, which is the combination the comp model needs and the thing
most counties do not offer. Illinois is a disclosure state, so recorded sale
prices are public record.

Four datasets are joined on PIN (the 14-digit parcel number):

============  ===================================  ==========================
Dataset       Contents                             Why it is needed
============  ===================================  ==========================
wvhk-k5uv     Parcel sales: price, date, deed      The training label
x54s-btds     Single / multi-family characteristics Beds, baths, size
3r7i-mrz4     Condominium unit characteristics     Beds and unit size
nj4t-kc8j     Parcel universe: lat, lon, township  Distance weighting
============  ===================================  ==========================

Two of those are separate on purpose: the single-family table excludes condos
entirely, and in Cook County condos are roughly 40% of residential turnover.
Querying only the first silently loses them.

The sales dataset ships its own arms-length screening flags, so the engine does
not have to reimplement deed-type and flip detection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from app.ingestion.socrata import SocrataClient

DOMAIN = "datacatalog.cookcountyil.gov"

SALES_DATASET = "wvhk-k5uv"
SINGLE_FAMILY_DATASET = "x54s-btds"
CONDO_DATASET = "3r7i-mrz4"
PARCEL_UNIVERSE_DATASET = "nj4t-kc8j"

PropertyType = Literal["single_family", "condo"]


# --------------------------------------------------------------------------
# Parsing. Socrata returns every value as a string, including numbers.
# --------------------------------------------------------------------------


def to_float(value: Any) -> float | None:
    """Parse a Socrata numeric field, treating blanks and junk as missing."""
    if value in (None, "", "NA", "nan"):
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
    """Parse a floating ISO timestamp such as ``2026-07-14T00:00:00.000``."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "")).date()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Sale:
    """One recorded transaction."""

    pin: str
    sale_date: date
    sale_price: int


@dataclass(frozen=True)
class Characteristics:
    """Physical attributes of a parcel, from whichever table covers it."""

    pin: str
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

    pin: str
    latitude: float
    longitude: float
    township_code: str | None = None


@dataclass(frozen=True)
class CompSale:
    """A sale joined to its characteristics and location.

    This is the unit the v1 weighted-comp model consumes. A record only exists
    if every join succeeded, so downstream code never has to handle a comp with
    no location or no size.
    """

    pin: str
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
    township_code: str | None = None

    @property
    def price_per_sqft(self) -> float | None:
        """Price per finished square foot, or ``None`` when size is unknown."""
        if not self.building_sqft:
            return None
        return self.sale_price / self.building_sqft


def parse_sale(row: dict[str, Any]) -> Sale | None:
    """Build a :class:`Sale`, or ``None`` if the row lacks the essentials."""
    pin = row.get("pin")
    sale_date = to_date(row.get("sale_date"))
    price = to_int(row.get("sale_price"))
    if not pin or sale_date is None or not price or price <= 0:
        return None
    return Sale(pin=pin, sale_date=sale_date, sale_price=price)


def parse_single_family(row: dict[str, Any]) -> Characteristics | None:
    pin = row.get("pin")
    if not pin:
        return None
    return Characteristics(
        pin=pin,
        property_type="single_family",
        beds=to_int(row.get("char_beds")),
        full_baths=to_int(row.get("char_fbath")),
        half_baths=to_int(row.get("char_hbath")),
        building_sqft=to_float(row.get("char_bldg_sf")),
        land_sqft=to_float(row.get("char_land_sf")),
        year_built=to_int(row.get("char_yrblt")),
    )


def parse_condo(row: dict[str, Any]) -> Characteristics | None:
    """Parse a condo row.

    ``char_unit_sf`` is the unit's own size; ``char_building_sf`` is the whole
    structure and would be wildly wrong as a comp feature. The condo table
    carries no bathroom count at all, so those stay ``None``.
    """
    pin = row.get("pin")
    if not pin:
        return None
    return Characteristics(
        pin=pin,
        property_type="condo",
        beds=to_int(row.get("char_bedrooms")),
        building_sqft=to_float(row.get("char_unit_sf")),
        land_sqft=to_float(row.get("char_land_sf")),
        year_built=to_int(row.get("char_yrblt")),
    )


def parse_location(row: dict[str, Any]) -> Location | None:
    pin = row.get("pin")
    lat = to_float(row.get("lat"))
    lon = to_float(row.get("lon"))
    if not pin or lat is None or lon is None:
        return None
    return Location(
        pin=pin, latitude=lat, longitude=lon, township_code=row.get("township_code")
    )


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------


def arms_length_where(since: date) -> str:
    """SoQL filter for arms-length sales on or after ``since``.

    Leans on the flags the assessor already publishes rather than guessing:
    bulk transfers, nominal sub-$10k transfers, non-standard deed types, and
    repeat sales of the same parcel inside a year are all excluded.
    """
    return (
        f"sale_date >= '{since.isoformat()}T00:00:00'"
        " AND is_multisale = false"
        " AND sale_filter_less_than_10k = false"
        " AND sale_filter_deed_type = false"
        " AND sale_filter_same_sale_within_365 = false"
    )


#: Cook County property class codes. The 200 series is residential; within it
#: 299 is specifically the condominium class. Everything else in the series is
#: houses and small apartment buildings, which are exactly the investor
#: property types Groundly targets.
RESIDENTIAL_CLASS_PREFIX = "2"
CONDO_CLASS = "299"


def property_class_where(property_type: PropertyType | None) -> str | None:
    """SoQL fragment restricting a sales query to one property type.

    Scoping to ``single_family`` is the practical choice for v1: the county
    publishes complete size and bath data for those and very little for condos.
    """
    if property_type == "single_family":
        return (
            f"class LIKE '{RESIDENTIAL_CLASS_PREFIX}%' "
            f"AND class NOT LIKE '{CONDO_CLASS}'"
        )
    if property_type == "condo":
        return f"class = '{CONDO_CLASS}'"
    return None


def sales_where(
    since: date,
    *,
    township_code: str | None = None,
    property_type: PropertyType | None = None,
) -> str:
    """The full sales filter: arms-length, plus optional area and type scoping.

    Shared by the pull and the population count so the join rate is always
    measured against the same population it was sampled from.
    """
    where = arms_length_where(since)
    if township_code:
        where += f" AND township_code = '{township_code}'"
    class_filter = property_class_where(property_type)
    if class_filter:
        where += f" AND {class_filter}"
    return where


def pin_in_clause(pins: list[str]) -> str:
    """Render a PIN list as a SoQL ``in`` clause."""
    quoted = ",".join(f"'{pin}'" for pin in pins)
    return f"pin in ({quoted})"


def join_sales(
    sales: list[Sale],
    characteristics: dict[str, Characteristics],
    locations: dict[str, Location],
) -> list[CompSale]:
    """Combine the three sources into comp records.

    A sale is dropped unless both its characteristics and its location are
    present. That is deliberate: a comp the model cannot place on a map or
    size is not a comp.
    """
    joined: list[CompSale] = []
    for sale in sales:
        chars = characteristics.get(sale.pin)
        location = locations.get(sale.pin)
        if chars is None or location is None:
            continue
        joined.append(
            CompSale(
                pin=sale.pin,
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
                township_code=location.township_code,
            )
        )
    return joined


def _chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def month_windows(since: date, until: date) -> list[tuple[date, date]]:
    """Split a date range into calendar months, half-open on the right."""
    windows: list[tuple[date, date]] = []
    cursor = since.replace(day=1)
    while cursor < until:
        year, month = divmod(cursor.month, 12)
        nxt = cursor.replace(year=cursor.year + year, month=month + 1, day=1)
        windows.append((max(cursor, since), min(nxt, until)))
        cursor = nxt
    return windows


def fetch_sales(
    client: SocrataClient,
    *,
    since: date,
    until: date | None = None,
    limit: int,
    township_code: str | None = None,
    property_type: PropertyType | None = None,
    stratify: bool = True,
) -> list[Sale]:
    """Pull sales from the window.

    Sorting by date and taking the first N returns only the most recent slice,
    which is not a sample of the window — it is a sample of last quarter. That
    matters because assessor characteristics are published for a single
    assessment year, so join rates decay slightly as sales age, and a
    recency-biased sample reports a coverage figure the full window will not
    match.

    ``stratify`` spreads the pull evenly across the calendar months in the
    window instead. Set it to False to deliberately take the newest sales.
    """
    until = until or date.today()

    def pull(where: str, row_limit: int) -> list[dict[str, Any]]:
        return client.query(
            SALES_DATASET,
            select="pin, sale_date, sale_price, class, township_code",
            where=where,
            order="sale_date DESC",
            limit=row_limit,
        )

    if not stratify:
        rows = pull(
            sales_where(
                since, township_code=township_code, property_type=property_type
            ),
            limit,
        )
    else:
        windows = month_windows(since, until)
        per_window = max(1, limit // len(windows)) if windows else limit
        rows = []
        for start, end in windows:
            where = sales_where(
                start, township_code=township_code, property_type=property_type
            )
            where += f" AND sale_date < '{end.isoformat()}T00:00:00'"
            rows.extend(pull(where, per_window))

    return [sale for sale in map(parse_sale, rows) if sale is not None]


#: PINs per ``in (...)`` clause. Socrata rejects very long query strings.
PIN_BATCH_SIZE = 250


def fetch_comp_sales(
    client: SocrataClient,
    *,
    since: date,
    assessment_year: int,
    limit: int = 5000,
    township_code: str | None = None,
    property_type: PropertyType | None = None,
    stratify: bool = True,
) -> list[CompSale]:
    """Pull arms-length sales and join them to characteristics and location.

    Sales come first, then the other three datasets are queried only for the
    PINs that actually sold, which keeps the request volume proportional to the
    window rather than to the size of the county.
    """
    sales = fetch_sales(
        client,
        since=since,
        limit=limit,
        township_code=township_code,
        property_type=property_type,
        stratify=stratify,
    )
    if not sales:
        return []

    pins = sorted({sale.pin for sale in sales})
    characteristics: dict[str, Characteristics] = {}
    locations: dict[str, Location] = {}

    for batch in _chunk(pins, PIN_BATCH_SIZE):
        clause = f"{pin_in_clause(batch)} AND year = {assessment_year}"

        for row in client.query(
            SINGLE_FAMILY_DATASET,
            select=(
                "pin, char_beds, char_fbath, char_hbath, char_bldg_sf, "
                "char_land_sf, char_yrblt"
            ),
            where=clause,
            limit=PIN_BATCH_SIZE * 4,
        ):
            parsed = parse_single_family(row)
            if parsed is not None:
                characteristics.setdefault(parsed.pin, parsed)

        for row in client.query(
            CONDO_DATASET,
            select="pin, char_bedrooms, char_unit_sf, char_land_sf, char_yrblt",
            where=f"{clause} AND is_parking_space = false AND is_common_area = false",
            limit=PIN_BATCH_SIZE * 4,
        ):
            parsed = parse_condo(row)
            if parsed is not None:
                characteristics.setdefault(parsed.pin, parsed)

        for row in client.query(
            PARCEL_UNIVERSE_DATASET,
            select="pin, lat, lon, township_code",
            where=clause,
            limit=PIN_BATCH_SIZE * 4,
        ):
            parsed = parse_location(row)
            if parsed is not None:
                locations.setdefault(parsed.pin, parsed)

    return join_sales(sales, characteristics, locations)
