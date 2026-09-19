"""Typed deal scope: the single source of truth for a deal in progress.

Every calculation reads from a ``DealScope``. Every mutation produces a new one
via :meth:`DealScope.with_changes`, so "what if I put down 25% instead" is one
call and a full re-derive, with no LLM in the loop.

Rates are decimal fractions everywhere, never percents: 6.5% is ``0.065``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class Strategy(str, Enum):
    """What the buyer intends to do with the property."""

    RENTAL = "rental"
    FLIP = "flip"
    BRRRR = "brrrr"


def _check_rate(name: str, value: float, *, maximum: float = 1.0) -> None:
    if not 0.0 <= value <= maximum:
        raise ValueError(f"{name} must be between 0 and {maximum}, got {value!r}")


def _check_non_negative(name: str, value: float) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value!r}")


@dataclass(frozen=True)
class OperatingExpenses:
    """Annual operating costs.

    Flat costs are dollars per year. Rate-based costs are fractions of
    effective gross income (gross rent less vacancy, plus other income), which
    is the convention most investors quote maintenance and management in.

    ``capex_reserve_rate`` is deliberately kept out of NOI. Capital
    expenditures are not an operating expense under the textbook definition,
    but they are a real cash outflow, so the engine deducts them below the NOI
    line when computing cash flow.
    """

    property_taxes_annual: float = 0.0
    insurance_annual: float = 0.0
    hoa_annual: float = 0.0
    other_annual: float = 0.0
    maintenance_rate: float = 0.05
    management_rate: float = 0.08
    capex_reserve_rate: float = 0.05

    def __post_init__(self) -> None:
        for name in (
            "property_taxes_annual",
            "insurance_annual",
            "hoa_annual",
            "other_annual",
        ):
            _check_non_negative(name, getattr(self, name))
        for name in ("maintenance_rate", "management_rate", "capex_reserve_rate"):
            _check_rate(name, getattr(self, name))
        variable = self.maintenance_rate + self.management_rate + self.capex_reserve_rate
        if variable >= 1.0:
            raise ValueError(
                "maintenance_rate + management_rate + capex_reserve_rate must be "
                f"below 1.0, got {variable}"
            )

    @property
    def fixed_annual(self) -> float:
        """Operating costs that do not scale with rent."""
        return (
            self.property_taxes_annual
            + self.insurance_annual
            + self.hoa_annual
            + self.other_annual
        )

    @property
    def variable_rate(self) -> float:
        """Share of effective gross income consumed by rate-based operating costs.

        Excludes the capex reserve, which sits below the NOI line.
        """
        return self.maintenance_rate + self.management_rate


@dataclass(frozen=True)
class DealScope:
    """The current deal. Frozen so a mutation is always an explicit new scope."""

    purchase_price: float
    address: str | None = None
    strategy: Strategy = Strategy.RENTAL

    # Financing
    down_payment_rate: float = 0.20
    interest_rate: float = 0.07
    term_years: int = 30
    closing_cost_rate: float = 0.03
    finance_rehab: bool = False

    # Work and resale
    rehab_budget: float = 0.0
    after_repair_value: float | None = None

    # Income
    monthly_rent: float = 0.0
    other_monthly_income: float = 0.0
    vacancy_rate: float = 0.05

    expenses: OperatingExpenses = field(default_factory=OperatingExpenses)

    def __post_init__(self) -> None:
        if self.purchase_price <= 0:
            raise ValueError(
                f"purchase_price must be positive, got {self.purchase_price!r}"
            )
        _check_non_negative("rehab_budget", self.rehab_budget)
        _check_non_negative("monthly_rent", self.monthly_rent)
        _check_non_negative("other_monthly_income", self.other_monthly_income)
        _check_rate("down_payment_rate", self.down_payment_rate)
        _check_rate("vacancy_rate", self.vacancy_rate)
        _check_rate("closing_cost_rate", self.closing_cost_rate)
        # Rates above 100% are a unit mistake (7 instead of 0.07) far more often
        # than a real loan, so reject them rather than silently modelling them.
        _check_rate("interest_rate", self.interest_rate)
        if self.term_years <= 0:
            raise ValueError(f"term_years must be positive, got {self.term_years!r}")
        if self.after_repair_value is not None:
            _check_non_negative("after_repair_value", self.after_repair_value)

    # -- derived financing inputs -------------------------------------------

    @property
    def down_payment(self) -> float:
        return self.purchase_price * self.down_payment_rate

    @property
    def closing_costs(self) -> float:
        return self.purchase_price * self.closing_cost_rate

    @property
    def loan_amount(self) -> float:
        """Borrowed principal, including rehab when it is rolled into the loan."""
        base = self.purchase_price - self.down_payment
        return base + self.rehab_budget if self.finance_rehab else base

    @property
    def total_cash_invested(self) -> float:
        """Cash out of pocket at close: down payment, closing costs, and any
        rehab the borrower is paying for directly."""
        cash = self.down_payment + self.closing_costs
        return cash if self.finance_rehab else cash + self.rehab_budget

    def with_changes(self, **changes: Any) -> DealScope:
        """Return a new scope with fields replaced, re-validated on the way out."""
        return replace(self, **changes)
