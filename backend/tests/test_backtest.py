"""Tests for the backtest harness. No network.

A backtest that flatters the model is worse than none, because it produces
confident numbers that are wrong. The two ways this one could cheat — letting a
property comp itself, and letting it see sales from the future — are both
pinned here.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.ingestion.records import CompSale
from app.models.backtest import (
    Prediction,
    candidates_for,
    format_result,
    predict_one,
    run_backtest,
)
from app.models.baseline_comp_model import InsufficientComps

ORIGIN_LAT, ORIGIN_LON = 41.90, -87.60


def make_comp(
    parcel_id: str,
    *,
    price: int = 400_000,
    sqft: float = 2000.0,
    lat: float = ORIGIN_LAT,
    lon: float = ORIGIN_LON,
    sold: date = date(2026, 6, 1),
) -> CompSale:
    return CompSale(
        parcel_id=parcel_id,
        county="Test County",
        sale_date=sold,
        sale_price=price,
        property_type="single_family",
        latitude=lat,
        longitude=lon,
        beds=3,
        full_baths=2,
        building_sqft=sqft,
        year_built=1990,
    )


def dense_pool(n: int = 30, *, sold: date = date(2026, 3, 1)) -> list[CompSale]:
    return [
        make_comp(f"p{i}", price=400_000, lat=ORIGIN_LAT + i * 0.0004, sold=sold)
        for i in range(n)
    ]


class TestNoLeakage:
    def test_a_property_is_never_its_own_comp(self):
        """The single most important rule. Without it the model scores
        perfectly and means nothing."""
        subject = make_comp("subject")
        pool = [subject] + dense_pool(10)
        assert all(c.parcel_id != "subject" for c in candidates_for(subject, pool))

    def test_future_sales_are_not_visible(self):
        subject = make_comp("subject", sold=date(2026, 6, 1))
        future = make_comp("future", sold=date(2026, 8, 1))
        past = make_comp("past", sold=date(2026, 3, 1))
        ids = {c.parcel_id for c in candidates_for(subject, [future, past])}
        assert ids == {"past"}

    def test_a_same_day_sale_is_not_visible(self):
        """Strictly before, not on or before: a sale closing the same day was
        not knowable when the subject was priced."""
        subject = make_comp("subject", sold=date(2026, 6, 1))
        same_day = make_comp("same", sold=date(2026, 6, 1))
        assert candidates_for(subject, [same_day]) == []

    def test_sales_beyond_the_lookback_are_dropped(self):
        subject = make_comp("subject", sold=date(2026, 6, 1))
        ancient = make_comp("ancient", sold=date(2021, 1, 1))
        assert candidates_for(subject, [ancient]) == []

    def test_distant_sales_are_prefiltered(self):
        subject = make_comp("subject", sold=date(2026, 6, 1))
        far = make_comp("far", lat=ORIGIN_LAT + 5.0, sold=date(2026, 3, 1))
        assert candidates_for(subject, [far]) == []


class TestPredictOne:
    def test_predicts_from_prior_sales(self):
        subject = make_comp("subject", price=500_000, sold=date(2026, 6, 1))
        result = predict_one(subject, [subject] + dense_pool(20))
        assert isinstance(result, Prediction)
        # Comps all sold at $400k; the subject's own $500k must not leak in.
        assert result.predicted == pytest.approx(400_000, rel=0.05)
        assert result.actual == 500_000

    def test_declines_when_there_is_nothing_to_go_on(self):
        subject = make_comp("subject", sold=date(2026, 6, 1))
        assert isinstance(predict_one(subject, [subject]), InsufficientComps)

    def test_error_and_interval_are_computed_from_the_actual_price(self):
        prediction = Prediction(
            parcel_id="1", actual=400_000, predicted=440_000,
            low=380_000, high=460_000, confidence="high", comps_used=10,
        )
        assert prediction.abs_pct_error == pytest.approx(0.10)
        assert prediction.inside_interval is True

    def test_detects_a_miss_outside_the_interval(self):
        prediction = Prediction(
            parcel_id="1", actual=800_000, predicted=400_000,
            low=380_000, high=420_000, confidence="low", comps_used=4,
        )
        assert prediction.inside_interval is False
        assert prediction.abs_pct_error == pytest.approx(0.5)


class TestRunBacktest:
    def test_scores_a_uniform_market_almost_perfectly(self):
        """Every house identical and every sale $400k: the model should nail
        it. If this is not near zero error, something is broken."""
        pool = [
            make_comp(f"old{i}", price=400_000, lat=ORIGIN_LAT + i * 0.0004,
                      sold=date(2026, 3, 1))
            for i in range(30)
        ] + [
            make_comp(f"new{i}", price=400_000, lat=ORIGIN_LAT + i * 0.0004,
                      sold=date(2026, 6, 1))
            for i in range(12)
        ]
        result = run_backtest("Uniform", pool, since=date(2026, 5, 1))
        assert result.predicted == 12
        assert result.median_abs_pct_error < 0.01
        assert result.within_10_pct == 1.0

    def test_counts_sales_it_declined_to_estimate(self):
        lonely = [
            make_comp(f"lonely{i}", lat=ORIGIN_LAT + i * 2.0, sold=date(2026, 6, 1))
            for i in range(4)
        ]
        result = run_backtest("Lonely", lonely, since=date(2026, 5, 1))
        assert result.skipped == 4
        assert result.predicted == 0
        assert result.coverage_rate == 0.0

    def test_an_empty_backtest_does_not_divide_by_zero(self):
        result = run_backtest("Empty", [], since=date(2026, 5, 1))
        assert result.predicted == 0
        assert "Empty" in format_result(result)

    def test_implausible_records_are_never_subjects(self):
        """A $780k sale against 183 sq ft is a bad size field, not a sale.

        Scoring the model against one would punish it for the county's data
        entry, so such records are dropped before subjects are chosen.
        """
        good = make_comp("good", lat=ORIGIN_LAT + 0.001, sold=date(2026, 6, 1))
        junk = make_comp("junk", price=780_000, sqft=183.0, sold=date(2026, 6, 1))
        pool = dense_pool(20, sold=date(2026, 3, 1)) + [good, junk]

        result = run_backtest("Trimmed", pool, since=date(2026, 5, 1))
        assert result.attempted == 1, "only the good sale should be a subject"

    def test_an_implausible_record_cannot_move_a_prediction(self):
        subject = make_comp("subject", lat=ORIGIN_LAT + 0.001, sold=date(2026, 6, 1))
        clean_pool = dense_pool(20, sold=date(2026, 3, 1))
        junk = make_comp("junk", price=780_000, sqft=183.0, sold=date(2026, 3, 1))

        clean = predict_one(subject, clean_pool)
        poisoned = predict_one(subject, clean_pool + [junk])
        assert isinstance(clean, Prediction)
        assert isinstance(poisoned, Prediction)
        assert poisoned.predicted == pytest.approx(clean.predicted)

    def test_since_holds_out_only_recent_sales(self):
        pool = dense_pool(20, sold=date(2026, 1, 1)) + [
            make_comp("recent", lat=ORIGIN_LAT + 0.001, sold=date(2026, 6, 1))
        ]
        result = run_backtest("Held out", pool, since=date(2026, 5, 1))
        assert result.attempted == 1

    def test_groups_scores_by_confidence(self):
        pool = [
            make_comp(f"old{i}", price=400_000, lat=ORIGIN_LAT + i * 0.0004,
                      sold=date(2026, 3, 1))
            for i in range(30)
        ] + [
            make_comp(f"new{i}", price=400_000, lat=ORIGIN_LAT + i * 0.0004,
                      sold=date(2026, 6, 1))
            for i in range(10)
        ]
        result = run_backtest("Graded", pool, since=date(2026, 5, 1))
        assert result.by_confidence
        for stats in result.by_confidence.values():
            assert 0.0 <= stats["interval_coverage"] <= 1.0
            assert stats["count"] > 0


class TestReportFormatting:
    def test_names_the_county_and_the_headline_numbers(self):
        pool = dense_pool(30, sold=date(2026, 3, 1)) + [
            make_comp(f"s{i}", lat=ORIGIN_LAT + i * 0.0004, sold=date(2026, 6, 1))
            for i in range(8)
        ]
        text = format_result(run_backtest("Cook County, IL", pool,
                                          since=date(2026, 5, 1)))
        assert "Cook County, IL" in text
        assert "Median abs percent error" in text
        assert "Interval coverage" in text

    def test_says_what_calibrated_coverage_looks_like(self):
        """A coverage number with no target beside it is not interpretable."""
        text = format_result(run_backtest("X", [], since=date(2026, 5, 1)))
        assert "near 50%" in text
