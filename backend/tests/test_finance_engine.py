"""Unit tests for the finance engine.

The amortization tests use payment figures that are standard published values
for those loan terms, so they check the formula against the outside world
rather than against another copy of itself.
"""

from __future__ import annotations

import pytest

from app.finance.engine import (
    break_even_rent,
    calculate_deal_metrics,
    cap_rate,
    cash_on_cash_return,
    debt_service_coverage_ratio,
    max_allowable_offer,
    mortgage_payment,
)
from app.state import DealScope, OperatingExpenses


class TestMortgagePayment:
    @pytest.mark.parametrize(
        ("loan", "rate", "years", "expected"),
        [
            (100_000, 0.06, 30, 599.55),
            (200_000, 0.045, 30, 1013.37),
            (240_000, 0.07, 30, 1596.73),
            (300_000, 0.05, 15, 2372.38),
        ],
    )
    def test_matches_published_payment(self, loan, rate, years, expected):
        assert mortgage_payment(loan, rate, years) == pytest.approx(expected, abs=0.01)

    def test_zero_interest_is_straight_line(self):
        # A 0% loan is just principal spread evenly, with no amortization curve.
        assert mortgage_payment(120_000, 0.0, 10) == pytest.approx(1000.0)

    def test_no_loan_means_no_payment(self):
        assert mortgage_payment(0, 0.07, 30) == 0.0

    def test_shorter_term_costs_more_per_month_but_less_overall(self):
        fifteen = mortgage_payment(240_000, 0.07, 15)
        thirty = mortgage_payment(240_000, 0.07, 30)
        assert fifteen > thirty
        assert fifteen * 15 * 12 < thirty * 30 * 12

    @pytest.mark.parametrize(
        ("loan", "rate", "years"),
        [(-1, 0.07, 30), (100_000, 0.07, 0), (100_000, 0.07, -5)],
    )
    def test_rejects_impossible_inputs(self, loan, rate, years):
        with pytest.raises(ValueError):
            mortgage_payment(loan, rate, years)


class TestRatios:
    def test_cap_rate(self):
        assert cap_rate(24_000, 400_000) == pytest.approx(0.06)

    def test_cap_rate_rejects_zero_price(self):
        with pytest.raises(ValueError):
            cap_rate(24_000, 0)

    def test_cash_on_cash(self):
        assert cash_on_cash_return(6_000, 75_000) == pytest.approx(0.08)

    def test_cash_on_cash_undefined_without_investment(self):
        assert cash_on_cash_return(6_000, 0) is None

    def test_dscr(self):
        assert debt_service_coverage_ratio(30_000, 24_000) == pytest.approx(1.25)

    def test_dscr_undefined_without_debt(self):
        # All-cash: "not applicable", which must not be confused with a 0.0 score.
        assert debt_service_coverage_ratio(30_000, 0) is None


class TestSeventyPercentRule:
    def test_ceiling(self):
        assert max_allowable_offer(300_000, 45_000) == pytest.approx(165_000)

    def test_offer_at_the_ceiling_exactly_passes(self):
        scope = DealScope(
            purchase_price=165_000, after_repair_value=300_000, rehab_budget=45_000
        )
        assert calculate_deal_metrics(scope).passes_seventy_percent_rule is True

    def test_one_dollar_over_the_ceiling_fails(self):
        scope = DealScope(
            purchase_price=165_001, after_repair_value=300_000, rehab_budget=45_000
        )
        assert calculate_deal_metrics(scope).passes_seventy_percent_rule is False

    def test_not_evaluated_without_an_arv(self):
        scope = DealScope(purchase_price=165_000, rehab_budget=45_000)
        metrics = calculate_deal_metrics(scope)
        assert metrics.passes_seventy_percent_rule is None
        assert metrics.max_allowable_offer is None


class TestBreakEvenRent:
    def test_break_even_rent_actually_breaks_even(self, baseline_scope):
        """The strongest check available: feed the answer back in and the deal
        must land at exactly zero cash flow."""
        rent = break_even_rent(baseline_scope)
        at_break_even = baseline_scope.with_changes(monthly_rent=rent)
        metrics = calculate_deal_metrics(at_break_even)
        assert metrics.annual_cash_flow == pytest.approx(0.0, abs=1e-6)

    def test_break_even_holds_for_an_all_cash_deal(self):
        scope = DealScope(
            purchase_price=180_000,
            down_payment_rate=1.0,
            monthly_rent=1600,
            expenses=OperatingExpenses(property_taxes_annual=2400),
        )
        rent = break_even_rent(scope)
        metrics = calculate_deal_metrics(scope.with_changes(monthly_rent=rent))
        assert metrics.annual_cash_flow == pytest.approx(0.0, abs=1e-6)

    def test_undefined_at_total_vacancy(self):
        scope = DealScope(purchase_price=200_000, monthly_rent=1500, vacancy_rate=1.0)
        assert break_even_rent(scope) is None


class TestIncomeStatement:
    def test_noi_excludes_debt_service_and_capex(self, baseline_scope):
        """NOI is a property-level figure. Financing must not touch it."""
        metrics = calculate_deal_metrics(baseline_scope)
        all_cash = calculate_deal_metrics(
            baseline_scope.with_changes(down_payment_rate=1.0)
        )
        assert metrics.net_operating_income == pytest.approx(
            all_cash.net_operating_income
        )
        assert metrics.net_operating_income > metrics.annual_cash_flow

    def test_statement_lines_reconcile(self, baseline_scope):
        m = calculate_deal_metrics(baseline_scope)
        assert m.effective_gross_income == pytest.approx(
            m.gross_scheduled_income - m.vacancy_loss
        )
        assert m.net_operating_income == pytest.approx(
            m.effective_gross_income - m.operating_expenses
        )
        assert m.annual_cash_flow == pytest.approx(
            m.net_operating_income - m.capex_reserve - m.annual_debt_service
        )
        assert m.monthly_cash_flow == pytest.approx(m.annual_cash_flow / 12)

    def test_cap_rate_is_independent_of_financing(self, baseline_scope):
        a = calculate_deal_metrics(baseline_scope)
        b = calculate_deal_metrics(
            baseline_scope.with_changes(down_payment_rate=0.50, interest_rate=0.03)
        )
        assert a.cap_rate == pytest.approx(b.cap_rate)


class TestFinancedRehab:
    def test_rehab_moves_from_cash_into_the_loan(self):
        paid_in_cash = DealScope(
            purchase_price=120_000, rehab_budget=40_000, down_payment_rate=0.25
        )
        financed = paid_in_cash.with_changes(finance_rehab=True)

        assert financed.loan_amount == paid_in_cash.loan_amount + 40_000
        assert financed.total_cash_invested == paid_in_cash.total_cash_invested - 40_000


class TestWhatIfBehaviour:
    """The slider path: change one field, everything re-derives, nothing leaks."""

    def test_larger_down_payment_raises_cash_flow_but_not_cap_rate(
        self, baseline_scope
    ):
        base = calculate_deal_metrics(baseline_scope)
        more_down = calculate_deal_metrics(
            baseline_scope.with_changes(down_payment_rate=0.25)
        )
        assert more_down.annual_cash_flow > base.annual_cash_flow
        assert more_down.total_cash_invested > base.total_cash_invested
        assert more_down.debt_service_coverage_ratio > base.debt_service_coverage_ratio
        assert more_down.cap_rate == pytest.approx(base.cap_rate)

    def test_leverage_cuts_both_ways(self, baseline_scope):
        """Whether borrowing more helps cash-on-cash depends on the cap rate
        against the loan constant, and the engine must not assume a direction.

        Cheap debt (positive leverage) makes a smaller down payment the better
        return. Expensive debt (negative leverage) reverses it. A model that
        always rewards leverage would pass the first case and fail the second.
        """
        for rate, leverage_helps in ((0.04, True), (0.07, False)):
            at_rate = baseline_scope.with_changes(interest_rate=rate)
            less_down = calculate_deal_metrics(
                at_rate.with_changes(down_payment_rate=0.20)
            )
            more_down = calculate_deal_metrics(
                at_rate.with_changes(down_payment_rate=0.25)
            )
            loan_constant = less_down.annual_debt_service / less_down.loan_amount
            assert (less_down.cap_rate > loan_constant) is leverage_helps
            assert (
                less_down.cash_on_cash_return > more_down.cash_on_cash_return
            ) is leverage_helps

    def test_higher_rate_hurts_every_debt_sensitive_metric(self, baseline_scope):
        base = calculate_deal_metrics(baseline_scope)
        pricier = calculate_deal_metrics(
            baseline_scope.with_changes(interest_rate=0.09)
        )
        assert pricier.monthly_payment > base.monthly_payment
        assert pricier.annual_cash_flow < base.annual_cash_flow
        assert pricier.debt_service_coverage_ratio < base.debt_service_coverage_ratio
        assert pricier.break_even_monthly_rent > base.break_even_monthly_rent

    def test_scope_is_immutable(self, baseline_scope):
        changed = baseline_scope.with_changes(interest_rate=0.09)
        assert baseline_scope.interest_rate == 0.07
        assert changed.interest_rate == 0.09

    def test_engine_is_pure(self, baseline_scope):
        """Same scope in, identical metrics out, every time."""
        assert calculate_deal_metrics(baseline_scope) == calculate_deal_metrics(
            baseline_scope
        )


class TestScopeValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"purchase_price": 0},
            {"purchase_price": -1},
            {"purchase_price": 300_000, "down_payment_rate": 1.5},
            {"purchase_price": 300_000, "down_payment_rate": -0.1},
            {"purchase_price": 300_000, "vacancy_rate": 1.4},
            {"purchase_price": 300_000, "term_years": 0},
            {"purchase_price": 300_000, "monthly_rent": -100},
            {"purchase_price": 300_000, "rehab_budget": -1},
            # 7 instead of 0.07 is a unit slip, not a 700% loan.
            {"purchase_price": 300_000, "interest_rate": 7},
        ],
    )
    def test_rejects_bad_scopes(self, kwargs):
        with pytest.raises(ValueError):
            DealScope(**kwargs)

    def test_rejects_expense_rates_that_exceed_all_income(self):
        with pytest.raises(ValueError):
            OperatingExpenses(
                maintenance_rate=0.5, management_rate=0.4, capex_reserve_rate=0.2
            )

    def test_validation_survives_with_changes(self, baseline_scope):
        with pytest.raises(ValueError):
            baseline_scope.with_changes(down_payment_rate=2.0)
