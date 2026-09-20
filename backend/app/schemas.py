"""Pydantic models for the HTTP edge.

These exist so the finance core stays free of web-framework types. The engine
and :class:`~app.state.DealScope` are plain dataclasses with no pydantic
import; validation and serialisation live here, at the boundary, and convert
in both directions.

The duplication is deliberate. A request model that *is* the domain object ties
the core's field names to a public API contract, and the first time a field is
renamed for clarity every client breaks.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.finance.engine import DealMetrics
from app.state import DealScope, OperatingExpenses, Strategy


class OperatingExpensesModel(BaseModel):
    """Annual operating costs. Rates are fractions of effective gross income."""

    model_config = ConfigDict(extra="forbid")

    property_taxes_annual: float = Field(default=0.0, ge=0)
    insurance_annual: float = Field(default=0.0, ge=0)
    hoa_annual: float = Field(default=0.0, ge=0)
    other_annual: float = Field(default=0.0, ge=0)
    maintenance_rate: float = Field(default=0.05, ge=0, le=1)
    management_rate: float = Field(default=0.08, ge=0, le=1)
    capex_reserve_rate: float = Field(default=0.05, ge=0, le=1)

    def to_domain(self) -> OperatingExpenses:
        return OperatingExpenses(**self.model_dump())

    @classmethod
    def from_domain(cls, expenses: OperatingExpenses) -> OperatingExpensesModel:
        return cls(**expenses.__dict__)


class DealScopeModel(BaseModel):
    """A deal as it crosses the wire.

    Rates are decimal fractions, never percents: 6.5% is ``0.065``. The bounds
    here mirror DealScope's own validation so a bad request is rejected with a
    field-level message rather than a bare ValueError.
    """

    model_config = ConfigDict(extra="forbid")

    purchase_price: float = Field(..., gt=0)
    address: str | None = None
    strategy: Strategy = Strategy.RENTAL

    down_payment_rate: float = Field(default=0.20, ge=0, le=1)
    interest_rate: float = Field(default=0.07, ge=0, le=1)
    term_years: int = Field(default=30, gt=0)
    closing_cost_rate: float = Field(default=0.03, ge=0, le=1)
    finance_rehab: bool = False

    rehab_budget: float = Field(default=0.0, ge=0)
    after_repair_value: float | None = Field(default=None, ge=0)

    monthly_rent: float = Field(default=0.0, ge=0)
    other_monthly_income: float = Field(default=0.0, ge=0)
    vacancy_rate: float = Field(default=0.05, ge=0, le=1)

    # A lambda rather than the class itself: pydantic's Field accepts either,
    # but passing the class matches an overload that expects a one-argument
    # factory and the type checker rejects it.
    expenses: OperatingExpensesModel = Field(
        default_factory=lambda: OperatingExpensesModel()
    )

    def to_domain(self) -> DealScope:
        data: dict[str, Any] = self.model_dump()
        data["expenses"] = self.expenses.to_domain()
        return DealScope(**data)

    @classmethod
    def from_domain(cls, scope: DealScope) -> DealScopeModel:
        data = dict(scope.__dict__)
        data["expenses"] = OperatingExpensesModel.from_domain(scope.expenses)
        return cls(**data)


class DealMetricsModel(BaseModel):
    """Everything the engine derives, flat.

    Ratios that do not apply come back as ``null`` rather than zero, so a
    client can tell "no debt" apart from "cannot cover its debt".
    """

    gross_scheduled_income: float
    vacancy_loss: float
    effective_gross_income: float
    operating_expenses: float
    net_operating_income: float

    loan_amount: float
    monthly_payment: float
    annual_debt_service: float
    total_cash_invested: float
    down_payment: float
    closing_costs: float

    capex_reserve: float
    annual_cash_flow: float
    monthly_cash_flow: float

    cap_rate: float
    cash_on_cash_return: float | None
    debt_service_coverage_ratio: float | None
    break_even_monthly_rent: float | None

    max_allowable_offer: float | None
    passes_seventy_percent_rule: bool | None

    @classmethod
    def from_domain(cls, metrics: DealMetrics, scope: DealScope) -> DealMetricsModel:
        """Build the response.

        ``down_payment`` and ``closing_costs`` come from the scope rather than
        the metrics: the dashboard breaks cash-to-close into its parts, and
        recomputing them in the browser would put a second implementation of
        the math somewhere other than Python.
        """
        return cls(
            **metrics.__dict__,
            down_payment=scope.down_payment,
            closing_costs=scope.closing_costs,
        )


class AnalyzeResponse(BaseModel):
    """A deal and its numbers, returned together.

    The scope is echoed back so the client always renders what the server
    actually computed, rather than what it believed it had sent.
    """

    scope: DealScopeModel
    metrics: DealMetricsModel
