"""The explanation layer: matching, thresholds, and provenance.

The most important test is the last class. Explanations are templates filled
with engine figures, so every dollar amount and percentage in any answer must
be derivable from the deal or its metrics. That property is what makes this
layer more trustworthy than a model, and it is checked rather than assumed.
"""

from __future__ import annotations

import re

import pytest

from app.agent import allowed_figures, unverified_figures
from app.executor import Session
from app.explain import (
    ASSESSORS,
    HELP_TEXT,
    MEANINGS,
    answer,
    assess_cap_rate,
    assess_cash_flow,
    assess_cash_on_cash,
    assess_deal,
    assess_dscr,
    assess_leverage,
    assess_margin,
    assess_seventy,
    explain_deal,
    explain_metric,
    improve,
    match,
    weakest,
)
from app.state import DealScope, OperatingExpenses


def session(
    *,
    purchase_price: float = 300_000,
    monthly_rent: float = 2800,
    down_payment_rate: float = 0.20,
    interest_rate: float = 0.07,
    closing_cost_rate: float = 0.02,
    after_repair_value: float | None = None,
    rehab_budget: float = 0.0,
) -> Session:
    """Explicit keywords rather than **overrides, which widens every field."""
    return Session(
        scope=DealScope(
            purchase_price=purchase_price,
            monthly_rent=monthly_rent,
            down_payment_rate=down_payment_rate,
            interest_rate=interest_rate,
            closing_cost_rate=closing_cost_rate,
            after_repair_value=after_repair_value,
            rehab_budget=rehab_budget,
            expenses=OperatingExpenses(property_taxes_annual=3600, insurance_annual=1200),
        )
    )


class TestMatching:
    @pytest.mark.parametrize(
        ("message", "intent"),
        [
            ("explain what all these numbers mean", "explain_deal"),
            ("what am I looking at", "explain_deal"),
            ("I'm confused", "explain_deal"),
            ("walk me through this", "explain_deal"),
            ("help me understand this", "explain_deal"),
            ("is this a good deal?", "assess_deal"),
            ("should I buy this", "assess_deal"),
            ("what do you think", "assess_deal"),
            ("how does this look", "assess_deal"),
            ("what's the weakest part of this deal?", "weakest"),
            ("what are the risks", "weakest"),
            ("what's wrong with it", "weakest"),
            ("how do I fix that", "improve"),
            ("how can I improve this", "improve"),
            ("what should I change", "improve"),
            ("is my loan helping or hurting", "leverage"),
            ("help", "help"),
        ],
    )
    def test_deal_level_questions(self, message, intent):
        matched = match(message)
        assert matched is not None, f"did not match {message!r}"
        assert matched[0] == intent

    @pytest.mark.parametrize(
        ("message", "intent", "metric"),
        [
            ("what does DSCR mean", "explain_metric", "debt_service_coverage_ratio"),
            ("what is cap rate", "explain_metric", "cap_rate"),
            ("explain cash on cash", "explain_metric", "cash_on_cash_return"),
            ("tell me about break-even rent", "explain_metric", "break_even_monthly_rent"),
            ("cap rate", "explain_metric", "cap_rate"),
            ("is my cap rate good", "assess_metric", "cap_rate"),
            ("is my DSCR ok?", "assess_metric", "debt_service_coverage_ratio"),
            ("is the cash flow healthy", "assess_metric", "monthly_cash_flow"),
            ("what does DSCR mean and is mine ok?", "assess_metric", "debt_service_coverage_ratio"),
        ],
    )
    def test_metric_questions(self, message, intent, metric):
        matched = match(message)
        assert matched is not None, f"did not match {message!r}"
        assert matched == (intent, metric)

    @pytest.mark.parametrize(
        "message",
        ["tell me about the weather", "hello", "", "change the rate to 7%",
         "what is the capital of France"],
    )
    def test_declines_what_it_does_not_handle(self, message):
        assert answer(session(), message) is None


class TestThresholds:
    def test_negative_cash_flow_is_bad(self):
        s = session(interest_rate=0.12)
        assert assess_cash_flow(s).level == "bad"
        assert "costs you" in assess_cash_flow(s).headline

    def test_healthy_cash_flow_is_good(self):
        s = session(monthly_rent=3800)
        assert assess_cash_flow(s).level == "good"

    def test_cap_rate_bands(self):
        assert assess_cap_rate(session(monthly_rent=1200)).level == "weak"
        assert assess_cap_rate(session()).level == "good"
        assert assess_cap_rate(session(monthly_rent=3500)).level == "strong"

    def test_dscr_bands(self):
        assert assess_dscr(session(interest_rate=0.12)).level == "bad"
        assert assess_dscr(session()).level in ("weak", "ok")
        assert assess_dscr(session(down_payment_rate=0.40)).level in ("good", "strong")

    def test_all_cash_dscr_is_not_a_failure(self):
        result = assess_dscr(session(down_payment_rate=1.0))
        assert result.level == "strong"
        assert "no mortgage" in result.detail

    def test_cash_on_cash_undefined_without_investment(self):
        # Not reachable with a positive price, but the assessor must not crash
        # on a None from the engine.
        s = session()
        assert assess_cash_on_cash(s).level in ("weak", "ok", "good", "strong", "bad")

    def test_margin_below_breakeven_is_bad(self):
        assert assess_margin(session(monthly_rent=2000)).level == "bad"

    def test_leverage_reads_the_cap_rate_against_the_loan_constant(self):
        """The least intuitive thing about financing a rental, judged correctly."""
        expensive = assess_leverage(session(interest_rate=0.07))
        cheap = assess_leverage(session(interest_rate=0.04))
        assert expensive is not None and expensive.level == "weak"
        assert cheap is not None and cheap.level == "good"
        assert "working against you" in expensive.headline
        assert "helping you" in cheap.headline

    def test_leverage_absent_without_a_loan(self):
        assert assess_leverage(session(down_payment_rate=1.0)) is None

    def test_seventy_rule_both_sides(self):
        passing = assess_seventy(session(purchase_price=165_000, after_repair_value=300_000, rehab_budget=45_000))
        failing = assess_seventy(session(purchase_price=190_000, after_repair_value=300_000, rehab_budget=45_000))
        assert passing is not None and passing.level == "good"
        assert failing is not None and failing.level == "weak"
        assert "$25,000 over" in failing.detail

    def test_seventy_rule_absent_without_an_arv(self):
        assert assess_seventy(session()) is None


class TestAnswers:
    def test_walkthrough_covers_the_whole_page(self):
        text = explain_deal(session())
        for fragment in ("$300,000", "20% down", "$1,597 a month", "$2,800",
                         "net operating income", "Cap rate", "Cash-on-cash", "DSCR",
                         "Break-even rent"):
            assert fragment in text, fragment

    def test_walkthrough_explains_before_it_judges(self):
        """A beginner needs to know what a cap rate is before hearing it is solid."""
        text = explain_deal(session())
        meaning = MEANINGS["cap_rate"]["meaning"][:30]
        assert text.index(meaning) < text.index("cap rate is solid")

    def test_verdict_names_best_and_worst(self):
        text = assess_deal(session())
        assert "best thing" in text
        assert "biggest concern" in text
        assert "only judges the numbers" in text

    def test_verdict_is_negative_when_cash_flow_is(self):
        assert "not a deal I would take" in assess_deal(session(interest_rate=0.12))

    def test_weakest_names_a_metric_and_what_moves_it(self):
        text = weakest(session())
        assert "weakest part is" in text
        assert "What moves it" in text

    def test_improve_gives_numbered_levers(self):
        text = improve(session())
        assert "1." in text and "2." in text
        assert "slider" in text

    def test_improve_targets_the_seventy_rule_when_that_is_weakest(self):
        s = session(purchase_price=190_000, after_repair_value=300_000, rehab_budget=45_000,
                    monthly_rent=3800)
        text = improve(s)
        assert "$165,000" in text

    def test_explain_metric_says_meaning_why_moves_and_yours(self):
        text = explain_metric("debt_service_coverage_ratio", session())
        assert "DSCR is" in text
        assert "Why it matters" in text
        assert "What moves it" in text
        assert "Yours:" in text

    def test_assess_metric_leads_with_the_meaning(self):
        text = answer(session(), "is my DSCR ok?")
        assert text is not None
        assert text.startswith("DSCR is")
        assert "1.20" in text

    def test_help_lists_what_it_can_do(self):
        assert answer(session(), "help") == HELP_TEXT

    def test_every_meaning_has_an_assessor_or_a_value(self):
        for name in MEANINGS:
            text = explain_metric(name, session())
            assert "Yours" in text, name


class TestProvenance:
    """Every figure in every answer must come from the engine or the deal.

    Reuses the agent's provenance checker: the explanation layer must satisfy
    the same rule the model is held to, and it is the easier of the two to
    hold to it, since it cannot want to invent anything.
    """

    QUESTIONS = [
        "explain what all these numbers mean",
        "is this a good deal?",
        "what's the weakest part",
        "how do I fix that",
        "is my loan helping",
        "what does DSCR mean and is mine ok",
        "is my cap rate good",
        "cash flow",
        "break-even rent",
        "cash to close",
    ]

    def _sources(self, s: Session) -> list[dict]:
        from app.agent import _deal_snapshot

        snap = _deal_snapshot(s)
        m = s.metrics
        # Figures the explanations legitimately derive: the loan constant, the
        # rent margin, the 70% overage. All arithmetic on engine outputs.
        derived = {
            "loan_constant": (m.annual_debt_service / m.loan_amount) if m.loan_amount else 0,
            "margin": ((s.scope.monthly_rent - (m.break_even_monthly_rent or 0)) / s.scope.monthly_rent)
            if s.scope.monthly_rent else 0,
            "overage": (s.scope.purchase_price - m.max_allowable_offer) if m.max_allowable_offer else 0,
            "negative_cash_flow": -m.monthly_cash_flow,
        }
        return [snap, derived]

    @pytest.mark.parametrize("question", QUESTIONS)
    def test_baseline_deal(self, question):
        s = session()
        text = answer(s, question)
        assert text is not None
        assert unverified_figures(text, allowed_figures(self._sources(s))) == []

    @pytest.mark.parametrize("question", QUESTIONS)
    def test_a_bad_deal(self, question):
        s = session(interest_rate=0.12, monthly_rent=2000)
        text = answer(s, question)
        assert text is not None
        assert unverified_figures(text, allowed_figures(self._sources(s))) == []

    @pytest.mark.parametrize("question", QUESTIONS)
    def test_a_flip(self, question):
        s = session(purchase_price=190_000, after_repair_value=300_000, rehab_budget=45_000)
        text = answer(s, question)
        assert text is not None
        assert unverified_figures(text, allowed_figures(self._sources(s))) == []

    def test_no_placeholder_ever_leaks(self):
        for q in self.QUESTIONS:
            text = answer(session(), q) or ""
            assert not re.search(r"\{|\}|\bNone\b|\bnan\b", text), q
