"""Tests for the valuation endpoint and the comp tool. No network.

The interesting behaviour here is the refusal. A property with no neighbours
that sold must get an honest "not enough comps" rather than a number, and that
has to be a successful response rather than an error, or callers will learn to
route around it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.ingestion.records import CompSale
from app.main import app, load_comps
from app.models.baseline_comp_model import InsufficientComps, SubjectProperty
from app.tools.comps import value_property
from app.tools.contract import CompStore, EmptyCompStore, InMemoryCompStore

ORIGIN_LAT, ORIGIN_LON = 41.90, -87.60
TODAY = date.today()

SUBJECT = {
    "latitude": ORIGIN_LAT,
    "longitude": ORIGIN_LON,
    "building_sqft": 2000,
    "beds": 3,
    "full_baths": 2,
    "year_built": 1990,
}


def make_comp(
    parcel_id: str,
    *,
    price: int = 400_000,
    sqft: float = 2000.0,
    lat_offset: float = 0.0,
    days_ago: int = 60,
) -> CompSale:
    return CompSale(
        parcel_id=parcel_id,
        county="Cook County, IL",
        sale_date=TODAY - timedelta(days=days_ago),
        sale_price=price,
        property_type="single_family",
        latitude=ORIGIN_LAT + lat_offset,
        longitude=ORIGIN_LON,
        beds=3,
        full_baths=2,
        building_sqft=sqft,
        year_built=1990,
        address=f"{parcel_id} Test St",
    )


def dense_comps(n: int = 12) -> list[CompSale]:
    return [make_comp(f"p{i}", lat_offset=i * 0.0005) for i in range(n)]


@pytest.fixture
def client_with_comps():
    load_comps(dense_comps())
    yield TestClient(app)
    load_comps([])


@pytest.fixture
def client_without_comps():
    load_comps([])
    yield TestClient(app)


class TestCompStore:
    def test_finds_nearby_sales(self):
        store = InMemoryCompStore(dense_comps())
        found = store.comps_near(
            ORIGIN_LAT, ORIGIN_LON, radius_miles=3.0, since=TODAY - timedelta(days=365)
        )
        assert len(found) == 12

    def test_excludes_sales_outside_the_radius(self):
        store = InMemoryCompStore([make_comp("far", lat_offset=1.0)])
        found = store.comps_near(
            ORIGIN_LAT, ORIGIN_LON, radius_miles=3.0, since=TODAY - timedelta(days=365)
        )
        assert found == []

    def test_excludes_sales_older_than_the_window(self):
        store = InMemoryCompStore([make_comp("old", days_ago=900)])
        found = store.comps_near(
            ORIGIN_LAT, ORIGIN_LON, radius_miles=3.0, since=TODAY - timedelta(days=365)
        )
        assert found == []

    def test_returns_nearest_first(self):
        store = InMemoryCompStore(
            [make_comp("far", lat_offset=0.02), make_comp("near", lat_offset=0.001)]
        )
        found = store.comps_near(
            ORIGIN_LAT, ORIGIN_LON, radius_miles=3.0, since=TODAY - timedelta(days=365)
        )
        assert [c.parcel_id for c in found] == ["near", "far"]

    def test_honours_the_limit(self):
        store = InMemoryCompStore(dense_comps(50))
        found = store.comps_near(
            ORIGIN_LAT, ORIGIN_LON, radius_miles=3.0,
            since=TODAY - timedelta(days=365), limit=5,
        )
        assert len(found) == 5

    def test_both_stores_satisfy_the_protocol(self):
        assert isinstance(InMemoryCompStore([]), CompStore)
        assert isinstance(EmptyCompStore(), CompStore)


class TestValueProperty:
    def test_values_from_the_store(self):
        subject = SubjectProperty(
            latitude=ORIGIN_LAT, longitude=ORIGIN_LON, building_sqft=2000.0
        )
        result = value_property(InMemoryCompStore(dense_comps()), subject)
        assert not isinstance(result, InsufficientComps)
        assert result.estimate == pytest.approx(400_000, rel=0.05)

    def test_declines_with_an_empty_store(self):
        subject = SubjectProperty(latitude=ORIGIN_LAT, longitude=ORIGIN_LON)
        result = value_property(EmptyCompStore(), subject)
        assert isinstance(result, InsufficientComps)


class TestValuationEndpoint:
    def test_returns_an_estimate_with_a_range(self, client_with_comps):
        body = client_with_comps.post("/api/value", json=SUBJECT).json()
        assert body["estimated"] is True
        assert body["low"] <= body["estimate"] <= body["high"]
        assert body["confidence"] in ("high", "medium", "low")

    def test_never_returns_a_bare_point_value(self, client_with_comps):
        """The spec requires a range on every estimate, never a single number."""
        body = client_with_comps.post("/api/value", json=SUBJECT).json()
        assert body["low"] is not None
        assert body["high"] is not None
        assert body["high"] > body["low"]

    def test_names_the_comps_it_used(self, client_with_comps):
        """An estimate that cannot show its work is not explainable."""
        body = client_with_comps.post("/api/value", json=SUBJECT).json()
        assert body["comps"]
        comp = body["comps"][0]
        assert comp["address"]
        assert comp["distance_miles"] >= 0
        assert 0 < comp["weight"] <= 1
        assert comp["weight"] == pytest.approx(
            comp["distance_weight"]
            * comp["recency_weight"]
            * comp["similarity_weight"]
        )

    def test_comps_are_ordered_by_weight(self, client_with_comps):
        body = client_with_comps.post("/api/value", json=SUBJECT).json()
        weights = [c["weight"] for c in body["comps"]]
        assert weights == sorted(weights, reverse=True)

    def test_refusal_is_a_success_not_an_error(self, client_without_comps):
        """A property with no neighbours that sold is a normal outcome. Making
        it a 404 teaches callers to treat it as a bug."""
        response = client_without_comps.post("/api/value", json=SUBJECT)
        assert response.status_code == 200
        body = response.json()
        assert body["estimated"] is False
        assert body["estimate"] is None
        assert "need at least" in body["reason"]

    def test_health_reports_how_many_comps_are_loaded(self, client_with_comps):
        assert client_with_comps.get("/api/health").json()["comps_loaded"] == 12

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("latitude", 91),
            ("latitude", -91),
            ("longitude", 181),
            ("building_sqft", 0),
            ("beds", -1),
            ("year_built", 1200),
        ],
    )
    def test_rejects_impossible_geography(self, client_with_comps, field, value):
        response = client_with_comps.post(
            "/api/value", json={**SUBJECT, field: value}
        )
        assert response.status_code == 422

    def test_coordinates_are_required(self, client_with_comps):
        response = client_with_comps.post("/api/value", json={"beds": 3})
        assert response.status_code == 422

    def test_works_without_optional_characteristics(self, client_with_comps):
        """Only coordinates are truly required; the rest sharpen the estimate."""
        response = client_with_comps.post(
            "/api/value",
            json={"latitude": ORIGIN_LAT, "longitude": ORIGIN_LON},
        )
        assert response.status_code == 200
        assert response.json()["estimated"] is True

    def test_a_low_confidence_estimate_explains_itself(self):
        load_comps([make_comp(f"p{i}", lat_offset=i * 0.0005) for i in range(4)])
        try:
            body = TestClient(app).post("/api/value", json=SUBJECT).json()
            assert body["confidence"] == "low"
            assert body["notes"], "a downgraded estimate must say why"
        finally:
            load_comps([])
