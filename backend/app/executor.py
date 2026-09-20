"""Runs typed plan steps. No LLM, no network beyond the comp store it is given.

The executor is the only place a plan turns into a change. It takes a session
and a plan and returns results; it never decides what to do, never calls a
model, and never touches a field outside the allow-list in ``app.plan``.

Fully testable with no network call, which is the whole reason the planner's
output is validated into typed steps before it arrives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from app.finance.engine import DealMetrics, calculate_deal_metrics
from app.models.baseline_comp_model import (
    InsufficientComps,
    SubjectProperty,
    ValuationEstimate,
)
from app.plan import (
    Clarify,
    Plan,
    SetField,
    ShowComps,
    ShowMetric,
    ShowSummary,
    Step,
    StepResult,
    ValueFromComps,
)
from app.state import DealScope
from app.tools.comps import value_property
from app.tools.contract import CompStore, EmptyCompStore


@dataclass
class Session:
    """One conversation's state.

    Holds the deal scope and the last valuation, which is what lets "show me
    those comps" mean anything on the next turn.
    """

    scope: DealScope
    latitude: float | None = None
    longitude: float | None = None
    last_valuation: ValuationEstimate | None = None
    history: list[str] = field(default_factory=list)

    @property
    def metrics(self) -> DealMetrics:
        return calculate_deal_metrics(self.scope)


def _money(value: float) -> str:
    return f"${value:,.0f}"


def _format_metric(name: str, value: object) -> str:
    """Render a metric the way a person would say it."""
    if value is None:
        return "not applicable"
    if name == "passes_seventy_percent_rule":
        return "passes" if value else "fails"
    if isinstance(value, bool):
        return "yes" if value else "no"
    assert isinstance(value, (int, float))
    if name in {"cap_rate", "cash_on_cash_return"}:
        return f"{value * 100:.2f}%"
    if name == "debt_service_coverage_ratio":
        return f"{value:.2f}"
    return _money(float(value))


def _apply_set(session: Session, step: SetField) -> StepResult:
    """Change one field and re-derive everything."""
    before = session.metrics.monthly_cash_flow

    if step.is_expense:
        expenses = replace(session.scope.expenses, **{step.field: step.value})
        session.scope = session.scope.with_changes(expenses=expenses)
    else:
        session.scope = session.scope.with_changes(**{step.field: step.value})

    after = session.metrics.monthly_cash_flow
    shown = _format_metric(
        "cap_rate" if step.field.endswith("_rate") else step.field, step.value
    )
    return StepResult(
        step=step,
        message=(
            f"Set {step.label} to {shown}. "
            f"Monthly cash flow moved from {_money(before)} to {_money(after)}."
        ),
        data={"field": step.field, "value": step.value},
    )


def _report_metric(session: Session, step: ShowMetric) -> StepResult:
    value = getattr(session.metrics, step.metric)
    rendered = _format_metric(step.metric, value)
    note = ""
    if value is None and step.metric == "debt_service_coverage_ratio":
        note = " There is no debt on this deal, so there is nothing to cover."
    return StepResult(
        step=step,
        message=f"{step.label.capitalize()} is {rendered}.{note}",
        data={"metric": step.metric, "value": value},
    )


def _summarise(session: Session, step: ShowSummary) -> StepResult:
    m = session.metrics
    parts = [
        f"{_money(session.scope.purchase_price)} purchase",
        f"{session.scope.down_payment_rate:.0%} down at "
        f"{session.scope.interest_rate:.3%}",
        f"{_money(m.monthly_payment)}/mo principal and interest",
        f"{_money(m.monthly_cash_flow)}/mo cash flow",
        f"{m.cap_rate * 100:.2f}% cap rate",
    ]
    if m.cash_on_cash_return is not None:
        parts.append(f"{m.cash_on_cash_return * 100:.2f}% cash-on-cash")
    if m.debt_service_coverage_ratio is not None:
        parts.append(f"{m.debt_service_coverage_ratio:.2f} DSCR")
    return StepResult(step=step, message=". ".join(parts) + ".", data={})


def _value(session: Session, step: ValueFromComps, store: CompStore) -> StepResult:
    latitude = step.latitude if step.latitude is not None else session.latitude
    longitude = step.longitude if step.longitude is not None else session.longitude

    if latitude is None or longitude is None:
        return StepResult(
            step=step,
            message=(
                "I need a location before I can pull comps. Give me coordinates "
                "for the property."
            ),
            data={},
        )

    result = value_property(
        store,
        SubjectProperty(
            latitude=latitude,
            longitude=longitude,
            building_sqft=None,
            beds=None,
            full_baths=None,
        ),
    )

    if isinstance(result, InsufficientComps):
        session.last_valuation = None
        return StepResult(
            step=step,
            message=f"I cannot value this from comps: {result.reason}.",
            data={"estimated": False, "reason": result.reason},
        )

    session.last_valuation = result
    note = f" Notes: {'; '.join(result.notes)}." if result.notes else ""
    return StepResult(
        step=step,
        message=(
            f"Comparable sales put this between {_money(result.low)} and "
            f"{_money(result.high)}, midpoint {_money(result.estimate)}, at "
            f"{result.confidence} confidence from {len(result.comps_used)} "
            f"sales.{note}"
        ),
        data={
            "estimated": True,
            "estimate": result.estimate,
            "low": result.low,
            "high": result.high,
            "confidence": result.confidence,
        },
    )


def _show_comps(session: Session, step: ShowComps) -> StepResult:
    if session.last_valuation is None:
        return StepResult(
            step=step,
            message="No valuation has been run yet, so there are no comps to show.",
            data={"comps": []},
        )

    comps = session.last_valuation.comps_used[: step.limit]
    lines = [
        f"{c.comp.address or c.comp.parcel_id}: {_money(c.comp.sale_price)} on "
        f"{c.comp.sale_date.isoformat()}, {c.distance_miles:.2f} miles away"
        for c in comps
    ]
    return StepResult(
        step=step,
        message="\n".join(lines) if lines else "No comps were used.",
        data={"comps": [c.comp.parcel_id for c in comps]},
    )


def execute(
    session: Session, plan: Plan, *, store: CompStore | None = None
) -> list[StepResult]:
    """Run every step in order against the session.

    Steps mutate the session in sequence, so "set the rate to 8% and show me
    the DSCR" reports the DSCR after the change, which is what was asked.
    """
    store = store or EmptyCompStore()
    results: list[StepResult] = []

    for step in plan.steps:
        results.append(_run_step(session, step, store))
    return results


def _run_step(session: Session, step: Step, store: CompStore) -> StepResult:
    if isinstance(step, SetField):
        return _apply_set(session, step)
    if isinstance(step, ShowMetric):
        return _report_metric(session, step)
    if isinstance(step, ShowSummary):
        return _summarise(session, step)
    if isinstance(step, ValueFromComps):
        return _value(session, step, store)
    if isinstance(step, ShowComps):
        return _show_comps(session, step)
    if isinstance(step, Clarify):
        return StepResult(step=step, message=step.question, data={})
    raise TypeError(f"unknown step type: {type(step).__name__}")
