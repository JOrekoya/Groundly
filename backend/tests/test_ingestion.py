"""Ingestion tests. No network: every case runs against FakeSocrataClient.

Parsing and joining are where county data actually goes wrong — a condo's
building size used as its unit size, a sale that joins to nothing, a PIN that
appears in two tables. Those are all deterministic given rows, so they are
tested here rather than discovered later against live data.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.ingestion.cook_county import (
    COUNTY_NAME as COOK_NAME,
)
from app.ingestion.cook_county import (
    CookCountyAdapter,
    arms_length_where,
    parse_condo,
    parse_location,
    parse_sale,
    parse_single_family,
    pin_in_clause,
    property_class_where,
    sales_where,
)
from app.ingestion.records import (
    Characteristics,
    CompSale,
    Location,
    Sale,
    join_sales,
    month_windows,
    to_date,
    to_float,
    to_int,
)
from app.ingestion.coverage import (
    MIN_SALES,
    evaluate_county,
    format_report,
)
from app.ingestion.socrata import FakeSocrataClient, build_query


class TestFieldParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("3.0", 3.0), ("1200", 1200.0), ("", None), (None, None), ("NA", None)],
    )
    def test_to_float(self, raw, expected):
        assert to_float(raw) == expected

    def test_to_int_handles_float_strings(self):
        # Socrata sends bedroom counts as "3.0", which int() alone rejects.
        assert to_int("3.0") == 3

    def test_to_date_strips_the_time_component(self):
        assert to_date("2026-07-14T00:00:00.000") == date(2026, 7, 14)

    def test_to_date_rejects_junk(self):
        assert to_date("not a date") is None


class TestSaleParsing:
    def test_parses_a_good_row(self):
        sale = parse_sale(
            {"pin": "14302020370000", "sale_date": "2026-07-14T00:00:00.000",
             "sale_price": "425000"}
        )
        assert sale == Sale("14302020370000", date(2026, 7, 14), 425_000)

    @pytest.mark.parametrize(
        "row",
        [
            {"pin": "", "sale_date": "2026-07-14T00:00:00.000", "sale_price": "1"},
            {"pin": "1", "sale_date": "", "sale_price": "425000"},
            {"pin": "1", "sale_date": "2026-07-14T00:00:00.000", "sale_price": "0"},
            {"pin": "1", "sale_date": "2026-07-14T00:00:00.000", "sale_price": ""},
        ],
    )
    def test_drops_unusable_rows(self, row):
        assert parse_sale(row) is None


class TestCharacteristicsParsing:
    def test_single_family(self):
        chars = parse_single_family(
            {"pin": "1", "char_beds": "3.0", "char_fbath": "2.0",
             "char_hbath": "1.0", "char_bldg_sf": "2295.0",
             "char_land_sf": "6000.0", "char_yrblt": "2005.0"}
        )
        assert chars == Characteristics(
            parcel_id="1", property_type="single_family", beds=3, full_baths=2,
            half_baths=1, building_sqft=2295.0, land_sqft=6000.0, year_built=2005,
        )

    def test_condo_uses_unit_size_not_building_size(self):
        """The trap in this dataset: char_building_sf is the whole structure.

        Using it as the comp feature would value a 1,198 sq ft condo as if it
        were a 138,960 sq ft property.
        """
        chars = parse_condo(
            {"pin": "2", "char_bedrooms": "2.0", "char_unit_sf": "1198.0",
             "char_building_sf": "138960.0", "char_yrblt": "1998.0"}
        )
        assert chars is not None
        assert chars.building_sqft == 1198.0
        assert chars.property_type == "condo"

    def test_condo_has_no_bathroom_data(self):
        """A real gap in the source, recorded so it is not mistaken for a bug."""
        chars = parse_condo({"pin": "2", "char_bedrooms": "2.0",
                             "char_unit_sf": "1198.0"})
        assert chars is not None
        assert chars.full_baths is None
        assert chars.half_baths is None

    def test_location_requires_both_coordinates(self):
        assert parse_location({"pin": "1", "lat": "41.9", "lon": ""}) is None
        assert parse_location({"pin": "1", "lat": "41.9", "lon": "-87.6"}) == Location(
            parcel_id="1", latitude=41.9, longitude=-87.6
        )


class TestQueryBuilding:
    def test_arms_length_filter_excludes_the_known_bad_categories(self):
        where = arms_length_where(date(2025, 3, 19))
        assert "sale_date >= '2025-03-19T00:00:00'" in where
        assert "is_multisale = false" in where
        assert "sale_filter_less_than_10k = false" in where
        assert "sale_filter_deed_type = false" in where
        assert "sale_filter_same_sale_within_365 = false" in where

    def test_pin_clause_quotes_every_pin(self):
        assert pin_in_clause(["1", "2"]) == "pin in ('1','2')"

    def test_soql_params_get_a_dollar_prefix(self):
        query = build_query({"select": "pin", "limit": 5, "township_code": "70"})
        assert "%24select=pin" in query
        assert "%24limit=5" in query
        assert "township_code=70" in query


class TestJoining:
    def test_joins_on_pin(self):
        comps = join_sales(
            [Sale("1", date(2026, 1, 5), 400_000)],
            {"1": Characteristics("1", "single_family", beds=3, building_sqft=2000.0)},
            {"1": Location("1", 41.9, -87.6, area_code="70")},
            county=COOK_NAME,
        )
        assert len(comps) == 1
        assert comps[0].beds == 3
        assert comps[0].price_per_sqft == pytest.approx(200.0)

    def test_drops_a_sale_with_no_characteristics(self):
        comps = join_sales(
            [Sale("1", date(2026, 1, 5), 400_000)],
            {},
            {"1": Location("1", 41.9, -87.6)},
            county=COOK_NAME,
        )
        assert comps == []

    def test_drops_a_sale_with_no_location(self):
        """A comp the model cannot place on a map is not a comp."""
        comps = join_sales(
            [Sale("1", date(2026, 1, 5), 400_000)],
            {"1": Characteristics("1", "single_family", building_sqft=2000.0)},
            {},
            county=COOK_NAME,
        )
        assert comps == []

    def test_price_per_sqft_is_none_without_size(self):
        comp = CompSale(
            parcel_id="1", county=COOK_NAME, sale_date=date(2026, 1, 5),
            sale_price=400_000, property_type="condo",
            latitude=41.9, longitude=-87.6,
        )
        assert comp.price_per_sqft is None


class TestFetchAgainstAFake:
    """End-to-end wiring, still with no network."""

    @pytest.fixture
    def client(self):
        return FakeSocrataClient(
            {
                "wvhk-k5uv": [
                    {"pin": "1", "sale_date": "2026-05-01T00:00:00.000",
                     "sale_price": "400000"},
                    {"pin": "2", "sale_date": "2026-05-02T00:00:00.000",
                     "sale_price": "310000"},
                ],
                "x54s-btds": [
                    {"pin": "1", "char_beds": "3.0", "char_fbath": "2.0",
                     "char_bldg_sf": "2000.0", "char_yrblt": "1995.0"},
                ],
                "3r7i-mrz4": [
                    {"pin": "2", "char_bedrooms": "2.0", "char_unit_sf": "1100.0",
                     "char_yrblt": "2004.0"},
                ],
                "nj4t-kc8j": [
                    {"pin": "1", "lat": "41.90", "lon": "-87.60",
                     "township_code": "70"},
                    {"pin": "2", "lat": "41.95", "lon": "-87.65",
                     "township_code": "70"},
                ],
            }
        )

    def test_returns_both_property_types(self, client):
        comps = CookCountyAdapter(client).fetch_comp_sales(
            date(2025, 3, 19), limit=100, stratify=False
        )
        assert {c.property_type for c in comps} == {"single_family", "condo"}

    def test_condos_would_be_lost_without_the_second_dataset(self, client):
        """Guards the mistake this adapter exists to avoid."""
        client.rows_by_dataset["3r7i-mrz4"] = []
        comps = CookCountyAdapter(client).fetch_comp_sales(
            date(2025, 3, 19), limit=100, stratify=False
        )
        assert [c.parcel_id for c in comps] == ["1"]

    def test_township_filter_reaches_the_query(self, client):
        CookCountyAdapter(client, township_code="70").fetch_comp_sales(
            date(2025, 3, 19), limit=100
        )
        dataset, params = client.calls[0]
        assert dataset == "wvhk-k5uv"
        assert "township_code = '70'" in params["where"]

    def test_no_sales_means_no_further_queries(self):
        """With nothing sold there is nothing to look up, so the three
        characteristics and geo datasets must never be touched."""
        empty = FakeSocrataClient({"wvhk-k5uv": []})
        assert (
            CookCountyAdapter(empty).fetch_comp_sales(date(2025, 3, 19), limit=100)
            == []
        )
        assert {dataset for dataset, _ in empty.calls} == {"wvhk-k5uv"}


def make_comps(
    count: int,
    *,
    sale_price: int = 400_000,
    building_sqft: float | None = 2000.0,
    beds: int | None = 3,
    full_baths: int | None = 2,
) -> list[CompSale]:
    """Build identical comps for coverage tests.

    Explicit keyword parameters rather than a **overrides dict: a dict of
    mixed values widens every field to str | float and loses the checking
    these fixtures exist to rely on.
    """
    return [
        CompSale(
            parcel_id=str(i),
            county="Test County",
            sale_date=date(2026, 1, 1),
            sale_price=sale_price,
            property_type="single_family",
            latitude=41.9,
            longitude=-87.6,
            beds=beds,
            full_baths=full_baths,
            building_sqft=building_sqft,
            year_built=1995,
        )
        for i in range(count)
    ]


class TestCoverageVerdict:
    def test_a_healthy_county_is_a_go(self):
        report = evaluate_county(
            "Cook County, IL", window_months=18,
            sales_in_window=70_000, comps=make_comps(68_000),
        )
        assert report.verdict == "GO"
        assert report.failures == ()

    def test_too_few_sales_is_a_no_go(self):
        report = evaluate_county(
            "Tiny County", window_months=18,
            sales_in_window=500, comps=make_comps(500),
        )
        assert report.verdict == "NO-GO"
        assert "arms-length sales" in report.failures[0]

    def test_prices_without_characteristics_is_a_no_go(self):
        """The common failure: a county publishes what sold for how much, but
        not what the building is."""
        report = evaluate_county(
            "Prices Only County", window_months=18,
            sales_in_window=50_000, comps=make_comps(10_000),
        )
        assert report.verdict == "NO-GO"
        assert any("join rate" in f for f in report.failures)

    def test_missing_sizes_is_a_no_go(self):
        report = evaluate_county(
            "No Sizes County", window_months=18,
            sales_in_window=50_000, comps=make_comps(50_000, building_sqft=None),
        )
        assert report.verdict == "NO-GO"
        assert any("building size" in f for f in report.failures)

    def test_nominal_transfers_are_caught_by_the_median_check(self):
        """A $1 quitclaim flood would otherwise look like abundant data."""
        comps = [
            CompSale(parcel_id=str(i), county="Test County",
                     sale_date=date(2026, 1, 1), sale_price=1,
                     property_type="single_family", latitude=41.9, longitude=-87.6,
                     building_sqft=2000.0, beds=3, full_baths=2)
            for i in range(5_000)
        ]
        report = evaluate_county(
            "Quitclaim County", window_months=18, sales_in_window=5_000, comps=comps
        )
        assert report.verdict == "NO-GO"
        assert any("implausibly low" in f for f in report.failures)

    def test_threshold_boundary(self):
        just_under = evaluate_county(
            "Edge", window_months=18,
            sales_in_window=MIN_SALES - 1, comps=make_comps(MIN_SALES - 1),
        )
        exactly_at = evaluate_county(
            "Edge", window_months=18,
            sales_in_window=MIN_SALES, comps=make_comps(MIN_SALES),
        )
        assert just_under.verdict == "NO-GO"
        assert exactly_at.verdict == "GO"

    def test_report_renders_without_data(self):
        """Formatting must survive an empty pull rather than dividing by zero."""
        report = evaluate_county("Empty", window_months=18, sales_in_window=0, comps=[])
        assert "NO-GO" in format_report(report)


class TestPropertyTypeScoping:
    """v1 is scoped to single-family because that is where Cook County's
    characteristics are complete. These pin the class filter that does it."""

    def test_single_family_excludes_the_condo_class(self):
        where = property_class_where("single_family")
        assert where is not None
        assert "class LIKE '2%'" in where
        assert "class NOT LIKE '299'" in where

    def test_condo_selects_only_the_condo_class(self):
        assert property_class_where("condo") == "class = '299'"

    def test_no_type_means_no_class_filter(self):
        assert property_class_where(None) is None

    def test_sales_where_combines_every_scope(self):
        where = sales_where(
            date(2025, 3, 19), township_code="70", property_type="single_family"
        )
        assert "is_multisale = false" in where
        assert "township_code = '70'" in where
        assert "class LIKE '2%'" in where

    def test_type_filter_reaches_the_sales_query(self):
        client = FakeSocrataClient({"wvhk-k5uv": []})
        CookCountyAdapter(client).fetch_comp_sales(
            date(2025, 3, 19), limit=100, property_type="single_family"
        )
        _, params = client.calls[0]
        assert "class LIKE '2%'" in params["where"]


class TestWindowSampling:
    """Sorting by date and taking the first N samples the newest quarter, not
    the window. These pin the stratified pull that fixes it."""

    def test_month_windows_cover_the_range_without_gaps(self):
        windows = month_windows(date(2025, 11, 15), date(2026, 2, 10))
        assert windows == [
            (date(2025, 11, 15), date(2025, 12, 1)),
            (date(2025, 12, 1), date(2026, 1, 1)),
            (date(2026, 1, 1), date(2026, 2, 1)),
            (date(2026, 2, 1), date(2026, 2, 10)),
        ]

    def test_month_windows_roll_over_the_year(self):
        windows = month_windows(date(2025, 12, 1), date(2026, 1, 1))
        assert windows == [(date(2025, 12, 1), date(2026, 1, 1))]

    def test_stratified_pull_queries_every_month(self):
        client = FakeSocrataClient({"wvhk-k5uv": []})
        CookCountyAdapter(client).fetch_sales(
            date(2025, 10, 1), until=date(2026, 1, 1), limit=300
        )
        assert len(client.calls) == 3
        # The budget is divided across months rather than spent on the newest.
        assert all(params["limit"] == 100 for _, params in client.calls)

    def test_stratified_pull_bounds_each_month(self):
        client = FakeSocrataClient({"wvhk-k5uv": []})
        CookCountyAdapter(client).fetch_sales(
            date(2025, 10, 1), until=date(2025, 12, 1), limit=100
        )
        first = client.calls[0][1]["where"]
        assert "sale_date >= '2025-10-01T00:00:00'" in first
        assert "sale_date < '2025-11-01T00:00:00'" in first

    def test_unstratified_pull_is_a_single_query(self):
        client = FakeSocrataClient({"wvhk-k5uv": []})
        CookCountyAdapter(client).fetch_sales(
            date(2025, 10, 1), until=date(2026, 1, 1), limit=300, stratify=False,
        )
        assert len(client.calls) == 1
        assert client.calls[0][1]["limit"] == 300
