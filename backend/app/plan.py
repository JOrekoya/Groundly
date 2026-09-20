"""Typed plan steps — the only things the executor will run.

Both the deterministic router and the LLM planner produce values from this
module and nothing else. That is the allow-list: a planner cannot invent an
action, because there is no step type for one, and anything it emits is
validated into these dataclasses before the executor sees it.

Deliberately not a parsed text envelope. The planner uses native tool-calling
and its output is validated against these shapes, so a malformed plan fails at
the boundary rather than halfway through execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union

#: Scope fields a user is allowed to change by name. Anything outside this set
#: is rejected, so neither a typo nor a prompt injection can reach an arbitrary
#: attribute on the scope.
SETTABLE_FIELDS: dict[str, str] = {
    "purchase_price": "purchase price",
    "monthly_rent": "monthly rent",
    "down_payment_rate": "down payment",
    "interest_rate": "interest rate",
    "term_years": "loan term",
    "closing_cost_rate": "closing costs",
    "rehab_budget": "rehab budget",
    "after_repair_value": "after-repair value",
    "vacancy_rate": "vacancy",
    "other_monthly_income": "other income",
}

#: Expense fields, which live one level down on the scope.
SETTABLE_EXPENSE_FIELDS: dict[str, str] = {
    "property_taxes_annual": "property taxes",
    "insurance_annual": "insurance",
    "hoa_annual": "HOA dues",
    "maintenance_rate": "maintenance",
    "management_rate": "management",
    "capex_reserve_rate": "capex reserve",
}

#: Metrics a user can ask for by name.
REPORTABLE_METRICS: dict[str, str] = {
    "cap_rate": "cap rate",
    "cash_on_cash_return": "cash-on-cash return",
    "debt_service_coverage_ratio": "DSCR",
    "monthly_cash_flow": "monthly cash flow",
    "annual_cash_flow": "annual cash flow",
    "net_operating_income": "net operating income",
    "monthly_payment": "monthly payment",
    "break_even_monthly_rent": "break-even rent",
    "total_cash_invested": "cash to close",
    "loan_amount": "loan amount",
    "passes_seventy_percent_rule": "70% rule",
}


@dataclass(frozen=True)
class SetField:
    """Change one field on the deal scope."""

    field: str
    value: float

    def __post_init__(self) -> None:
        if (
            self.field not in SETTABLE_FIELDS
            and self.field not in SETTABLE_EXPENSE_FIELDS
        ):
            raise ValueError(f"{self.field!r} is not a settable field")

    @property
    def is_expense(self) -> bool:
        return self.field in SETTABLE_EXPENSE_FIELDS

    @property
    def label(self) -> str:
        return SETTABLE_FIELDS.get(self.field) or SETTABLE_EXPENSE_FIELDS[self.field]


@dataclass(frozen=True)
class ShowMetric:
    """Report one named metric."""

    metric: str

    def __post_init__(self) -> None:
        if self.metric not in REPORTABLE_METRICS:
            raise ValueError(f"{self.metric!r} is not a reportable metric")

    @property
    def label(self) -> str:
        return REPORTABLE_METRICS[self.metric]


@dataclass(frozen=True)
class ShowSummary:
    """Report the whole deal."""


@dataclass(frozen=True)
class ValueFromComps:
    """Estimate value from comparable sales at a location."""

    latitude: float | None = None
    longitude: float | None = None
    use_scope_location: bool = True


@dataclass(frozen=True)
class ShowComps:
    """List the comparable sales behind the last valuation."""

    limit: int = 10


@dataclass(frozen=True)
class Clarify:
    """Ask the user a question rather than guess.

    A first-class step, not an error. The planner is expected to emit this when
    a request is genuinely ambiguous, which is better than picking one reading
    and silently acting on it.
    """

    question: str


Step = Union[SetField, ShowMetric, ShowSummary, ValueFromComps, ShowComps, Clarify]

#: Where a plan came from. Worth carrying because the router handling a message
#: means no model was involved at all, and that is worth being able to show.
PlanSource = Literal["router", "planner"]


@dataclass(frozen=True)
class Plan:
    """An ordered list of steps, and where it came from."""

    steps: tuple[Step, ...] = ()
    source: PlanSource = "router"
    rationale: str | None = None

    def __bool__(self) -> bool:
        return bool(self.steps)


@dataclass(frozen=True)
class StepResult:
    """What running one step produced."""

    step: Step
    message: str
    data: dict = field(default_factory=dict)
