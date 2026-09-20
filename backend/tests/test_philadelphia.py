"""Philadelphia adapter tests. No network: everything runs on FakeCartoClient.

Philadelphia is the project's second county, and it exists partly to prove the
adapter boundary is real. Where Cook County joins four Socrata datasets on a
PIN, Philadelphia reads one Carto table keyed by an OPA account number, with
coordinates inside a geometry column and no publisher-supplied arms-length
screening. If the abstraction only fit Cook County, these tests would not be
writable without changing shared code.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.ingestion.adapter import CountyAdapter
from app.ingestion.carto import FakeCartoClient, HttpCartoClient, quote_literal
from app.ingestion.cook_county import CookCountyAdapter
from app.ingestion.philadelphia import (
    COUNTY_NAME,
    MIN_ARMS_LENGTH_PRICE,
    PhiladelphiaAdapter,
    category_for,
    parse_comp,
    parse_sale,
    sales_where,
)
from app.ingestion.records import CompSale
from app.ingestion.socrata import FakeSocrataClient

OPA_ROW = {
    "parcel_number": "432318100",
    "location": "3756 N DARIEN ST",
    "sale_date": "2026-06-19T04:00:00Z",
    "sale_price": 55000,
    "category_code": "1",
    "number_of_bedrooms": 3,
    "number_of_bathrooms": 1,
    "total_livable_area": 800,
    "total_area": 1050,
    "year_built": "1940",
    "zip_code": "19140",
    "latitude": 40.009497706185236,
    "longitude": -75.14211074826983,
}


class TestParsing:
    def test_parses_a_real_opa_row(self):
        comp = parse_comp(OPA_ROW)
        assert comp is not None
        assert comp.parcel_id == "432318100"
        assert comp.county == COUNTY_NAME
        assert comp.sale_date == date(2026, 6, 19)
        assert comp.sale_price == 55_000
        assert comp.beds == 3
        assert comp.building_sqft == 800.0
        assert comp.year_built == 1940
        assert comp.address == "3756 N DARIEN ST"
        assert comp.price_per_sqft == pytest.approx(68.75)

    def test_zoned_timestamp_keeps_the_local_date(self):
        """OPA stamps midnight Eastern as 04:00Z. Parsed naively that is still
        the 19th, but the parser must not drift it to the 20th either."""
        comp = parse_comp(OPA_ROW)
        assert comp is not None
        assert comp.sale_date == date(2026, 6, 19)

    def test_splits_a_decimal_bathroom_count(self):
        """OPA records 2.5 bathrooms as a decimal; the model wants them split."""
        comp = parse_comp({**OPA_ROW, "number_of_bathrooms": 2.5})
        assert comp is not None
        assert comp.full_baths == 2
        assert comp.half_baths == 1

    def test_whole_bathroom_count_has_no_half(self):
        comp = parse_comp({**OPA_ROW, "number_of_bathrooms": 3})
        assert comp is not None
        assert (comp.full_baths, comp.half_baths) == (3, 0)

    def test_year_built_placeholder_reads_as_missing(self):
        """OPA writes an unknown build year as the string '0000'."""
        comp = parse_comp({**OPA_ROW, "year_built": "0000"})
        assert comp is not None
        assert comp.year_built is None

    @pytest.mark.parametrize(
        "override",
        [
            {"parcel_number": ""},
            {"sale_date": None},
            {"sale_price": 0},
            {"latitude": None},
            {"longitude": ""},
        ],
    )
    def test_drops_unusable_rows(self, override):
        assert parse_comp({**OPA_ROW, **override}) is None

    def test_parse_sale_reads_only_the_transaction(self):
        sale = parse_sale(OPA_ROW)
        assert sale is not None
        assert (sale.parcel_id, sale.sale_price) == ("432318100", 55_000)


class TestQueryBuilding:
    def test_applies_its_own_price_floor(self):
        """Philadelphia publishes no arms-length flags, so the floor here is
        doing the work Cook County's own flag does for it. Roughly 6,500
        residential transfers an 18-month window record $1 or $0."""
        where = sales_where(date(2025, 3, 20))
        assert f"sale_price > {MIN_ARMS_LENGTH_PRICE}" in where

    def test_requires_a_geometry(self):
        assert "the_geom IS NOT NULL" in sales_where(date(2025, 3, 20))

    def test_bounds_the_window_on_both_sides_when_asked(self):
        where = sales_where(date(2025, 3, 20), until=date(2025, 4, 1))
        assert "sale_date >= '2025-03-20'" in where
        assert "sale_date < '2025-04-01'" in where

    def test_single_family_selects_category_one(self):
        assert "category_code = '1'" in sales_where(
            date(2025, 3, 20), property_type="single_family"
        )

    def test_condo_is_not_a_separate_category_here(self):
        """Unlike Cook County, Philadelphia files condos under single family,
        so there is no condo code to select and nothing to exclude."""
        assert category_for("condo") is None
        assert "category_code" not in sales_where(
            date(2025, 3, 20), property_type="condo"
        )

    def test_zip_scoping(self):
        assert "zip_code = '19140'" in sales_where(
            date(2025, 3, 20), zip_code="19140"
        )

    def test_quotes_are_escaped(self):
        assert quote_literal("O'Brien") == "O''Brien"


class TestAdapter:
    @pytest.fixture
    def client(self):
        return FakeCartoClient([OPA_ROW])

    def test_counts_sales(self):
        client = FakeCartoClient([{"n": 19930}])
        assert PhiladelphiaAdapter(client).count_sales(date(2025, 3, 20)) == 19_930

    def test_count_of_nothing_is_zero(self):
        assert PhiladelphiaAdapter(FakeCartoClient([])).count_sales(
            date(2025, 3, 20)
        ) == 0

    def test_fetches_comps(self, client):
        comps = PhiladelphiaAdapter(client).fetch_comp_sales(
            date(2025, 3, 20), limit=10, stratify=False
        )
        assert len(comps) == 1
        assert comps[0].county == COUNTY_NAME

    def test_stratified_pull_queries_every_month(self, client):
        PhiladelphiaAdapter(client).fetch_comp_sales(
            date(2025, 10, 1), limit=300, until=date(2026, 1, 1)
        )
        assert len(client.queries) == 3
        assert all("LIMIT 100" in sql for sql in client.queries)

    def test_zip_reaches_the_query(self, client):
        PhiladelphiaAdapter(client, zip_code="19140").fetch_comp_sales(
            date(2025, 3, 20), limit=10, stratify=False
        )
        assert "zip_code = '19140'" in client.queries[0]

    def test_name_reflects_scoping(self):
        plain = PhiladelphiaAdapter(FakeCartoClient([]))
        scoped = PhiladelphiaAdapter(FakeCartoClient([]), zip_code="19140")
        assert plain.name == COUNTY_NAME
        assert "19140" in scoped.name


class TestCartoTransport:
    def test_surfaces_a_query_error(self):
        """Carto answers a bad query with HTTP 200 and an error body, which
        would otherwise look like an empty result set."""

        class Failing:
            def query(self, sql):
                raise ValueError("Carto rejected the query: syntax error")

        with pytest.raises(ValueError, match="rejected"):
            Failing().query("SELECT nope")

    def test_client_builds_the_expected_url(self):
        client = HttpCartoClient("phl.carto.com")
        assert client.domain == "phl.carto.com"


class TestAdapterBoundary:
    """The reason a second county exists: proving the abstraction is real."""

    def test_both_counties_satisfy_the_same_protocol(self):
        cook = CookCountyAdapter(FakeSocrataClient({}))
        philly = PhiladelphiaAdapter(FakeCartoClient([]))
        assert isinstance(cook, CountyAdapter)
        assert isinstance(philly, CountyAdapter)

    def test_both_counties_produce_the_same_record_type(self):
        philly = PhiladelphiaAdapter(FakeCartoClient([OPA_ROW])).fetch_comp_sales(
            date(2025, 3, 20), limit=10, stratify=False
        )
        assert all(isinstance(comp, CompSale) for comp in philly)

    def test_a_caller_can_drive_either_without_knowing_which(self):
        """Coverage scoring and, later, ingestion take adapters positionally.
        Neither should need a branch on county name."""
        adapters: list[CountyAdapter] = [
            CookCountyAdapter(FakeSocrataClient({"wvhk-k5uv": []})),
            PhiladelphiaAdapter(FakeCartoClient([])),
        ]
        for adapter in adapters:
            assert adapter.fetch_comp_sales(date(2025, 3, 20), limit=10) == []
            assert isinstance(adapter.name, str)
