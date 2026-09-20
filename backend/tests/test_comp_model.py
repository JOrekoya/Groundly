"""Tests for the weighted nearest-comp baseline. No network, no data files.

Most of these are property tests rather than fixed numbers: for a weighting
scheme, "a nearer sale counts more than a farther one" is the behaviour worth
pinning, and a hard-coded expected weight would only assert that the constants
have not changed.

The distance tests use published city-pair distances so the geometry is checked
against the outside world rather than against itself.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.ingestion.records import CompSale
from app.models.baseline_comp_model import (
    LOW_CONFIDENCE_COMPS,
    MAX_AGE_MONTHS,
    MAX_DISTANCE_MILES,
    MAX_PLAUSIBLE_PPSF,
    MIN_COMPS,
    MIN_INTERVAL_HALF_WIDTH,
    InsufficientComps,
    SubjectProperty,
    ValuationEstimate,
    estimate_value,
    half_life_weight,
    haversine_miles,
    is_plausible,
    months_between,
    similarity_weight,
    weigh_comps,
    weighted_quantile,
)

AS_OF = date(2026, 9, 1)
ORIGIN_LAT, ORIGIN_LON = 41.90, -87.60


def make_comp(
    parcel_id: str = "1",
    *,
    price: int = 400_000,
    sqft: float | None = 2000.0,
    lat: float = ORIGIN_LAT,
    lon: float = ORIGIN_LON,
    sold: date = date(2026, 6, 1),
    beds: int | None = 3,
    baths: int | None = 2,
    year: int | None = 1990,
) -> CompSale:
    return CompSale(
        parcel_id=parcel_id,
        county="Test County",
        sale_date=sold,
        sale_price=price,
        property_type="single_family",
        latitude=lat,
        longitude=lon,
        beds=beds,
        full_baths=baths,
        building_sqft=sqft,
        year_built=year,
    )


def subject(
    *,
    latitude: float = ORIGIN_LAT,
    longitude: float = ORIGIN_LON,
    building_sqft: float | None = 2000.0,
    beds: int | None = 3,
    full_baths: int | None = 2,
    year_built: int | None = 1990,
) -> SubjectProperty:
    """The property under test. Explicit keywords rather than a **overrides
    dict, which widens every field to a union and loses the checking."""
    return SubjectProperty(
        latitude=latitude,
        longitude=longitude,
        building_sqft=building_sqft,
        beds=beds,
        full_baths=full_baths,
        year_built=year_built,
    )


def spread_of(comps: list[CompSale], **kwargs):
    return estimate_value(subject(), comps, as_of=AS_OF, **kwargs)


class TestDistance:
    @pytest.mark.parametrize(
        ("a", "b", "expected_miles"),
        [
            # Published great-circle distances, checked to within a percent.
            ((41.8781, -87.6298), (40.7128, -74.0060), 711.0),  # Chicago-NYC
            ((39.9526, -75.1652), (40.7128, -74.0060), 80.0),  # Philly-NYC
            ((34.0522, -118.2437), (37.7749, -122.4194), 347.0),  # LA-SF
        ],
    )
    def test_matches_published_distances(self, a, b, expected_miles):
        actual = haversine_miles(a[0], a[1], b[0], b[1])
        assert actual == pytest.approx(expected_miles, rel=0.01)

    def test_zero_distance_to_itself(self):
        assert haversine_miles(41.9, -87.6, 41.9, -87.6) == pytest.approx(0.0)

    def test_is_symmetric(self):
        there = haversine_miles(41.9, -87.6, 41.95, -87.65)
        back = haversine_miles(41.95, -87.65, 41.9, -87.6)
        assert there == pytest.approx(back)

    def test_longitude_degrees_narrow_toward_the_poles(self):
        """A flat-earth approximation would call these equal. They are not."""
        at_equator = haversine_miles(0.0, 0.0, 0.0, 1.0)
        far_north = haversine_miles(60.0, 0.0, 60.0, 1.0)
        assert far_north < at_equator * 0.55


class TestWeighting:
    def test_halves_at_the_half_life(self):
        assert half_life_weight(1.0, 1.0) == pytest.approx(0.5)
        assert half_life_weight(2.0, 1.0) == pytest.approx(0.25)
        assert half_life_weight(0.0, 1.0) == pytest.approx(1.0)

    def test_decays_smoothly_with_no_cliff(self):
        """A comp just past a boundary should be worth fractionally less, not
        half as much."""
        just_under = half_life_weight(0.49, 0.5)
        just_over = half_life_weight(0.51, 0.5)
        assert just_under > just_over
        assert just_under - just_over < 0.03

    def test_rejects_a_nonpositive_half_life(self):
        with pytest.raises(ValueError):
            half_life_weight(1.0, 0.0)

    def test_nearer_comps_outweigh_farther_ones(self):
        near = make_comp("near", lat=ORIGIN_LAT + 0.001)
        far = make_comp("far", lat=ORIGIN_LAT + 0.02)
        weighted = weigh_comps(subject(), [near, far], as_of=AS_OF)
        by_id = {w.comp.parcel_id: w for w in weighted}
        assert by_id["near"].weight > by_id["far"].weight

    def test_recent_comps_outweigh_stale_ones(self):
        fresh = make_comp("fresh", sold=date(2026, 8, 1))
        stale = make_comp("stale", sold=date(2025, 4, 1))
        weighted = weigh_comps(subject(), [fresh, stale], as_of=AS_OF)
        by_id = {w.comp.parcel_id: w for w in weighted}
        assert by_id["fresh"].weight > by_id["stale"].weight

    def test_similar_comps_outweigh_dissimilar_ones(self):
        alike = make_comp("alike", sqft=2050.0)
        unalike = make_comp("unalike", sqft=4500.0)
        weighted = weigh_comps(subject(), [alike, unalike], as_of=AS_OF)
        by_id = {w.comp.parcel_id: w for w in weighted}
        assert by_id["alike"].weight > by_id["unalike"].weight

    def test_returned_sorted_by_weight(self):
        comps = [make_comp(str(i), lat=ORIGIN_LAT + i * 0.004) for i in range(5)]
        weights = [w.weight for w in weigh_comps(subject(), comps, as_of=AS_OF)]
        assert weights == sorted(weights, reverse=True)

    def test_missing_fields_are_not_penalised(self):
        """An unknown build year should not make a good comp look bad."""
        complete = similarity_weight(subject(), make_comp())
        no_year = similarity_weight(subject(), make_comp(year=None))
        assert no_year >= complete


class TestCutoffs:
    def test_drops_comps_beyond_the_radius(self):
        far = make_comp("far", lat=ORIGIN_LAT + 1.0)
        assert weigh_comps(subject(), [far], as_of=AS_OF) == []

    def test_keeps_a_comp_just_inside_the_radius(self):
        inside = make_comp(lat=ORIGIN_LAT + 0.02)
        assert len(weigh_comps(subject(), [inside], as_of=AS_OF)) == 1

    def test_drops_comps_older_than_the_age_limit(self):
        ancient = make_comp(sold=date(2020, 1, 1))
        assert weigh_comps(subject(), [ancient], as_of=AS_OF) == []

    def test_radius_is_configurable(self):
        comp = make_comp(lat=ORIGIN_LAT + 0.02)
        assert weigh_comps(subject(), [comp], as_of=AS_OF, max_distance_miles=0.1) == []


class TestOutlierTrimming:
    """Philadelphia's livable-area field is wrong for roughly 4% of records.
    Untrimmed, those distort any weighted average. See docs/data-validation.md.
    """

    def test_rejects_an_absurd_price_per_square_foot(self):
        # A real record: $780,000 recorded against 183 sq ft of livable area.
        assert is_plausible(make_comp(price=780_000, sqft=183.0)) is False

    def test_rejects_an_implausibly_cheap_rate(self):
        assert is_plausible(make_comp(price=10_000, sqft=2000.0)) is False

    def test_accepts_an_ordinary_rate(self):
        assert is_plausible(make_comp(price=400_000, sqft=2000.0)) is True

    def test_rejects_a_comp_with_no_size(self):
        assert is_plausible(make_comp(sqft=None)) is False

    def test_a_bad_record_cannot_move_the_estimate(self):
        good = [make_comp(str(i), price=400_000, sqft=2000.0) for i in range(8)]
        poisoned = good + [make_comp("bad", price=780_000, sqft=183.0)]
        clean = estimate_value(subject(), good, as_of=AS_OF)
        with_junk = estimate_value(subject(), poisoned, as_of=AS_OF)
        assert isinstance(clean, ValuationEstimate)
        assert isinstance(with_junk, ValuationEstimate)
        assert with_junk.estimate == pytest.approx(clean.estimate)


class TestWeightedQuantile:
    def test_median_of_equal_weights(self):
        assert weighted_quantile([1.0, 2.0, 3.0], [1.0, 1.0, 1.0], 0.5) == (
            pytest.approx(2.0)
        )

    def test_weight_pulls_the_median(self):
        light = weighted_quantile([100.0, 200.0], [1.0, 1.0], 0.5)
        heavy = weighted_quantile([100.0, 200.0], [1.0, 9.0], 0.5)
        assert heavy > light

    def test_quantiles_are_ordered(self):
        values = [100.0, 150.0, 200.0, 250.0, 300.0]
        weights = [1.0] * 5
        q25 = weighted_quantile(values, weights, 0.25)
        q50 = weighted_quantile(values, weights, 0.5)
        q75 = weighted_quantile(values, weights, 0.75)
        assert q25 <= q50 <= q75

    def test_is_robust_to_one_wild_value(self):
        """A weighted mean would be dragged; a weighted median should not be."""
        ordinary = weighted_quantile([200.0] * 8, [1.0] * 8, 0.5)
        with_outlier = weighted_quantile(
            [200.0] * 8 + [9_000.0], [1.0] * 9, 0.5
        )
        assert with_outlier == pytest.approx(ordinary)

    def test_zero_total_weight_falls_back_to_the_plain_median(self):
        assert weighted_quantile([1.0, 2.0, 3.0], [0.0, 0.0, 0.0], 0.5) == 2.0

    @pytest.mark.parametrize(
        ("values", "weights", "quantile"),
        [([], [], 0.5), ([1.0], [1.0, 2.0], 0.5), ([1.0], [1.0], 1.5)],
    )
    def test_rejects_bad_input(self, values, weights, quantile):
        with pytest.raises(ValueError):
            weighted_quantile(values, weights, quantile)


class TestEstimate:
    def test_estimates_from_a_healthy_comp_set(self):
        comps = [
            make_comp(str(i), price=400_000, sqft=2000.0, lat=ORIGIN_LAT + i * 0.001)
            for i in range(8)
        ]
        result = estimate_value(subject(), comps, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        assert result.estimate == pytest.approx(400_000, rel=0.02)
        assert result.low <= result.estimate <= result.high
        assert result.confidence == "high"

    def test_values_the_rate_not_the_price(self):
        """Comps are all 1,000 sq ft at $200/sqft. A 2,000 sq ft subject should
        come out near $400,000, not near the $200,000 the comps sold for."""
        comps = [
            make_comp(str(i), price=200_000, sqft=1000.0, lat=ORIGIN_LAT + i * 0.001)
            for i in range(8)
        ]
        result = estimate_value(subject(building_sqft=2000.0), comps, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        assert result.estimate == pytest.approx(400_000, rel=0.05)
        assert result.price_per_sqft == pytest.approx(200.0, rel=0.05)

    def test_interval_always_contains_the_estimate(self):
        comps = [
            make_comp(str(i), price=300_000 + i * 40_000, sqft=2000.0,
                      lat=ORIGIN_LAT + i * 0.002)
            for i in range(10)
        ]
        result = estimate_value(subject(), comps, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        assert result.low <= result.estimate <= result.high

    def test_records_why_each_comp_counted(self):
        """An estimate must be able to explain itself."""
        comps = [make_comp(str(i), lat=ORIGIN_LAT + i * 0.001) for i in range(6)]
        result = estimate_value(subject(), comps, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        first = result.comps_used[0]
        assert first.distance_miles >= 0
        assert first.age_months >= 0
        assert 0 < first.weight <= 1
        assert first.weight == pytest.approx(
            first.distance_weight * first.recency_weight * first.similarity_weight
        )

    def test_falls_back_to_price_when_subject_size_is_unknown(self):
        comps = [make_comp(str(i), price=400_000, sqft=2000.0) for i in range(8)]
        result = estimate_value(subject(building_sqft=None), comps, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        assert result.price_per_sqft is None
        assert any("size unknown" in note for note in result.notes)

    def test_caps_how_many_comps_are_used(self):
        comps = [make_comp(str(i), lat=ORIGIN_LAT + i * 0.0001) for i in range(60)]
        result = estimate_value(subject(), comps, as_of=AS_OF, max_comps=10)
        assert isinstance(result, ValuationEstimate)
        assert len(result.comps_used) == 10


class TestRefusesToGuess:
    """The spec is explicit: when comps are too sparse or too far to trust,
    return the nearest available flagged low confidence rather than a falsely
    precise number."""

    def test_returns_insufficient_with_too_few_comps(self):
        result = estimate_value(subject(), [make_comp()], as_of=AS_OF)
        assert isinstance(result, InsufficientComps)
        assert f"need at least {MIN_COMPS}" in result.reason

    def test_returns_insufficient_when_everything_is_too_far(self):
        far = [make_comp(str(i), lat=ORIGIN_LAT + 1.0) for i in range(20)]
        result = estimate_value(subject(), far, as_of=AS_OF)
        assert isinstance(result, InsufficientComps)
        assert result.comps_considered == 20

    def test_returns_insufficient_with_no_comps_at_all(self):
        assert isinstance(estimate_value(subject(), [], as_of=AS_OF), InsufficientComps)

    def test_a_pile_of_bad_records_is_not_evidence(self):
        junk = [make_comp(str(i), price=780_000, sqft=183.0) for i in range(30)]
        assert isinstance(estimate_value(subject(), junk, as_of=AS_OF), InsufficientComps)


class TestConfidence:
    def test_few_comps_lowers_confidence(self):
        comps = [make_comp(str(i)) for i in range(MIN_COMPS)]
        result = estimate_value(subject(), comps, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        assert result.confidence == "low"
        assert any("comparable sales found" in note for note in result.notes)

    def test_wide_disagreement_lowers_confidence(self):
        """Plenty of comps, but they disagree by a factor of four."""
        comps = [
            make_comp(str(i), price=150_000 + i * 90_000, sqft=2000.0,
                      lat=ORIGIN_LAT + i * 0.0005)
            for i in range(12)
        ]
        result = estimate_value(subject(), comps, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        assert result.confidence == "low"

    def test_tight_recent_nearby_comps_earn_high_confidence(self):
        comps = [
            make_comp(str(i), price=400_000 + i * 2_000, sqft=2000.0,
                      lat=ORIGIN_LAT + i * 0.0005, sold=date(2026, 8, 1))
            for i in range(LOW_CONFIDENCE_COMPS + 4)
        ]
        result = estimate_value(subject(), comps, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        assert result.confidence == "high"
        assert result.notes == ()

    def test_stale_comps_lower_confidence(self):
        comps = [
            make_comp(str(i), price=400_000, sqft=2000.0,
                      lat=ORIGIN_LAT + i * 0.0005, sold=date(2025, 3, 1))
            for i in range(10)
        ]
        result = estimate_value(subject(), comps, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        assert result.confidence in ("low", "medium")
        assert any("months old" in note for note in result.notes)

    def test_every_low_confidence_result_says_why(self):
        comps = [make_comp(str(i)) for i in range(MIN_COMPS)]
        result = estimate_value(subject(), comps, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        assert result.notes, "a downgraded estimate must explain itself"


class TestConstantsAreSane:
    def test_cutoffs_exceed_half_lives(self):
        """A cutoff inside its own half-life would discard comps that still
        carry most of their weight."""
        assert MAX_DISTANCE_MILES > 0.5
        assert MAX_AGE_MONTHS > 9.0

    def test_low_confidence_threshold_is_above_the_minimum(self):
        assert LOW_CONFIDENCE_COMPS > MIN_COMPS

    def test_plausible_range_brackets_real_markets(self):
        """Cook County's median is $245/sqft and Philadelphia's is $208."""
        assert is_plausible(make_comp(price=490_000, sqft=2000.0))
        assert is_plausible(make_comp(price=416_000, sqft=2000.0))
        assert MAX_PLAUSIBLE_PPSF > 1000


class TestMonthsBetween:
    def test_counts_months(self):
        assert months_between(date(2026, 1, 1), date(2026, 7, 1)) == pytest.approx(
            6.0, rel=0.02
        )

    def test_never_negative(self):
        assert months_between(date(2026, 7, 1), date(2026, 1, 1)) == 0.0


class TestIntervalFloor:
    """The spec requires a range on every estimate. A zero-width range is a
    point value wearing a range's clothing, so there is a floor."""

    def test_perfectly_agreeing_comps_still_get_a_range(self):
        identical = [
            make_comp(str(i), price=400_000, sqft=2000.0, lat=ORIGIN_LAT + i * 0.0005)
            for i in range(10)
        ]
        result = estimate_value(subject(), identical, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        assert result.high > result.low
        assert result.spread_ratio >= 2 * MIN_INTERVAL_HALF_WIDTH * 0.99

    def test_the_floor_does_not_narrow_a_genuinely_wide_band(self):
        spread = [
            make_comp(str(i), price=200_000 + i * 50_000, sqft=2000.0,
                      lat=ORIGIN_LAT + i * 0.0005)
            for i in range(10)
        ]
        result = estimate_value(subject(), spread, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        assert result.spread_ratio > 2 * MIN_INTERVAL_HALF_WIDTH

    def test_estimate_stays_inside_the_widened_band(self):
        identical = [
            make_comp(str(i), price=400_000, sqft=2000.0, lat=ORIGIN_LAT + i * 0.0005)
            for i in range(10)
        ]
        result = estimate_value(subject(), identical, as_of=AS_OF)
        assert isinstance(result, ValuationEstimate)
        assert result.low < result.estimate < result.high
