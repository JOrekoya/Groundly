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

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.finance.engine import DealMetrics
from app.models.baseline_comp_model import (
    InsufficientComps,
    SubjectProperty,
    ValuationEstimate,
    WeightedComp,
)
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


class SubjectPropertyModel(BaseModel):
    """A property to value."""

    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    building_sqft: float | None = Field(default=None, gt=0)
    beds: int | None = Field(default=None, ge=0)
    full_baths: int | None = Field(default=None, ge=0)
    year_built: int | None = Field(default=None, ge=1600, le=2100)

    def to_domain(self) -> SubjectProperty:
        return SubjectProperty(**self.model_dump())


class WeightedCompModel(BaseModel):
    """One comparable sale, and why it counted as much as it did.

    The weight components are sent, not just their product, so the UI can
    answer "why is that a comp?" without asking the server again.
    """

    parcel_id: str
    county: str
    address: str | None
    sale_date: date
    sale_price: int
    building_sqft: float | None
    beds: int | None
    full_baths: int | None
    year_built: int | None
    latitude: float
    longitude: float
    price_per_sqft: float | None

    distance_miles: float
    age_months: float
    distance_weight: float
    recency_weight: float
    similarity_weight: float
    weight: float

    @classmethod
    def from_domain(cls, weighted: WeightedComp) -> WeightedCompModel:
        comp = weighted.comp
        return cls(
            parcel_id=comp.parcel_id,
            county=comp.county,
            address=comp.address,
            sale_date=comp.sale_date,
            sale_price=comp.sale_price,
            building_sqft=comp.building_sqft,
            beds=comp.beds,
            full_baths=comp.full_baths,
            year_built=comp.year_built,
            latitude=comp.latitude,
            longitude=comp.longitude,
            price_per_sqft=comp.price_per_sqft,
            distance_miles=weighted.distance_miles,
            age_months=weighted.age_months,
            distance_weight=weighted.distance_weight,
            recency_weight=weighted.recency_weight,
            similarity_weight=weighted.similarity_weight,
            weight=weighted.weight,
        )


class ValuationResponse(BaseModel):
    """A valuation, or an honest refusal to give one.

    ``estimate`` is null when the comps were too thin to support a number. The
    spec is explicit that this is preferable to a falsely precise figure, so
    the shape of this response makes the refusal a first-class outcome rather
    than an error.
    """

    estimated: bool
    estimate: float | None = None
    low: float | None = None
    high: float | None = None
    price_per_sqft: float | None = None
    confidence: str | None = None
    reason: str | None = None
    notes: list[str] = Field(default_factory=list)
    comps: list[WeightedCompModel] = Field(default_factory=list)

    @classmethod
    def from_domain(
        cls, result: ValuationEstimate | InsufficientComps
    ) -> ValuationResponse:
        if isinstance(result, InsufficientComps):
            return cls(
                estimated=False,
                reason=result.reason,
                comps=[WeightedCompModel.from_domain(w) for w in result.nearest],
            )
        return cls(
            estimated=True,
            estimate=result.estimate,
            low=result.low,
            high=result.high,
            price_per_sqft=result.price_per_sqft,
            confidence=result.confidence,
            notes=list(result.notes),
            comps=[WeightedCompModel.from_domain(w) for w in result.comps_used],
        )


class ChatHistoryTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=4000)


class ChatRequest(BaseModel):
    """One message from the user, plus the deal it is about.

    The scope travels with each message rather than living in server memory, so
    the service stays stateless and the browser's sliders and the chat cannot
    drift apart.
    """

    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, max_length=2000)
    scope: DealScopeModel
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    building_sqft: float | None = Field(default=None, gt=0)
    beds: int | None = Field(default=None, ge=0)
    full_baths: int | None = Field(default=None, ge=0)
    #: Prior turns, oldest first. The server keeps no conversation state, so
    #: the client sends what the assistant should remember.
    history: list[ChatHistoryTurn] = Field(default_factory=list, max_length=60)


class ChatResponse(BaseModel):
    """The answer, the updated deal, and an honest account of what ran.

    ``used_llm_for_planning`` and ``used_llm_for_narration`` are reported
    separately because "no model was involved in this answer" is a claim this
    project makes, and it should be checkable rather than asserted.
    """

    reply: str
    scope: DealScopeModel
    metrics: DealMetricsModel
    steps: list[str] = Field(default_factory=list)
    plan_source: str = "router"
    used_llm: bool = False
    tool_calls: list[str] = Field(default_factory=list)
    provenance_ok: bool = True
    rejected_figures: list[str] = Field(default_factory=list)
    llm_available: bool = False
    latitude: float | None = None
    longitude: float | None = None

    # Older names, kept so nothing that read them breaks. Both mean used_llm.
    used_llm_for_planning: bool = False
    used_llm_for_narration: bool = False
