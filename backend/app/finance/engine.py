"""Pure-Python finance engine.

No LLM, no network, no I/O, no global state. Every function is a deterministic
function of its arguments, which is what makes the golden-scope eval harness in
``backend/tests/golden_scopes`` cheap to run on every change.

Definitions used throughout, stated explicitly because investors disagree about
them and a silent choice here is a wrong number later:

* **Effective gross income (EGI)** is scheduled rent less vacancy, plus other
  income.
* **NOI** is EGI less operating expenses. It excludes debt service, and it
  excludes the capital-expenditure reserve, which is not an operating expense
  under the textbook definition.
* **Cash flow** is NOI less the capex reserve less debt service. The reserve is
  a real outflow, so it belongs here even though it is not in NOI.
* **DSCR** is NOI over annual debt service, which is how a lender sizes it.
* **Cap rate** is NOI over purchase price, not over total cost basis.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.state import DealScope

#: The flip heuristic: offer at most 70% of after-repair value, less rehab.
SEVENTY_PERCENT_RULE = 0.70


def mortgage_payment(loan_amount: float, annual_rate: float, term_years: int) -> float:
    """Level monthly principal-and-interest payment on an amortizing loan.

    Uses the standard amortization formula ``L * i / (1 - (1 + i)**-n)`` with a
    monthly period. A zero interest rate is handled separately because the
    formula divides by zero there.
    """
    if loan_amount < 0:
        raise ValueError(f"loan_amount must be non-negative, got {loan_amount!r}")
    if term_years <= 0:
        raise ValueError(f"term_years must be positive, got {term_years!r}")
    if loan_amount == 0:
        return 0.0

    periods = term_years * 12
    monthly_rate = annual_rate / 12
    if monthly_rate == 0:
        return loan_amount / periods
    return loan_amount * monthly_rate / (1 - (1 + monthly_rate) ** -periods)


def cap_rate(noi: float, purchase_price: float) -> float:
    """NOI over purchase price."""
    if purchase_price <= 0:
        raise ValueError(f"purchase_price must be positive, got {purchase_price!r}")
    return noi / purchase_price


def cash_on_cash_return(
    annual_cash_flow: float, total_cash_invested: float
) -> float | None:
    """Annual cash flow over cash out of pocket.

    Returns ``None`` when no cash was invested, since the ratio is undefined
    rather than infinite in any useful sense.
    """
    if total_cash_invested <= 0:
        return None
    return annual_cash_flow / total_cash_invested


def debt_service_coverage_ratio(
    noi: float, annual_debt_service: float
) -> float | None:
    """NOI over annual debt service.

    Returns ``None`` on an all-cash deal: there is no debt to cover, so the
    ratio is undefined. Callers should treat that as "not applicable", not as
    a failing score.
    """
    if annual_debt_service <= 0:
        return None
    return noi / annual_debt_service


def max_allowable_offer(after_repair_value: float, rehab_budget: float) -> float:
    """The ceiling the 70% rule puts on a flip offer: 70% of ARV, less rehab."""
    return SEVENTY_PERCENT_RULE * after_repair_value - rehab_budget


def annual_debt_service(scope: DealScope) -> float:
    """Twelve months of principal and interest."""
    return mortgage_payment(scope.loan_amount, scope.interest_rate, scope.term_years) * 12


def break_even_rent(scope: DealScope) -> float | None:
    """Monthly rent at which annual cash flow is exactly zero.

    Solved in closed form rather than searched. With ``f`` the fixed operating
    costs, ``d`` the annual debt service, and ``v`` the share of EGI taken by
    maintenance, management, and the capex reserve, cash flow is zero when
    ``EGI * (1 - v) == f + d``. Scheduled rent is then backed out of the
    required EGI.

    Returns ``None`` when vacancy is 100%, since no rent produces income then.
    """
    if scope.vacancy_rate >= 1.0:
        return None

    expenses = scope.expenses
    variable = expenses.variable_rate + expenses.capex_reserve_rate
    fixed_and_debt = expenses.fixed_annual + annual_debt_service(scope)

    required_egi = fixed_and_debt / (1 - variable)
    required_scheduled_rent = required_egi - scope.other_monthly_income * 12
    annual_rent = required_scheduled_rent / (1 - scope.vacancy_rate)
    return max(annual_rent / 12, 0.0)


@dataclass(frozen=True)
class DealMetrics:
    """Everything the engine derives from a scope, in one flat result.

    Ratios that do not apply to a deal are ``None`` rather than zero, so a
    caller can tell "no debt" apart from "cannot cover its debt".
    """

    # Income statement
    gross_scheduled_income: float
    vacancy_loss: float
    effective_gross_income: float
    operating_expenses: float
    net_operating_income: float

    # Financing
    loan_amount: float
    monthly_payment: float
    annual_debt_service: float
    total_cash_invested: float

    # Cash flow
    capex_reserve: float
    annual_cash_flow: float
    monthly_cash_flow: float

    # Ratios
    cap_rate: float
    cash_on_cash_return: float | None
    debt_service_coverage_ratio: float | None
    break_even_monthly_rent: float | None

    # Flip heuristic; None unless the scope carries an after-repair value
    max_allowable_offer: float | None
    passes_seventy_percent_rule: bool | None


def calculate_deal_metrics(scope: DealScope) -> DealMetrics:
    """Derive every metric from a scope in one deterministic pass.

    This is the single entry point the API, the router, and the narrator all
    call. Changing one field in the scope and calling this again is the whole
    "what if I put down 25% instead" path.
    """
    expenses = scope.expenses

    gross_scheduled_income = scope.monthly_rent * 12
    vacancy_loss = gross_scheduled_income * scope.vacancy_rate
    effective_gross_income = (
        gross_scheduled_income - vacancy_loss + scope.other_monthly_income * 12
    )

    variable_expenses = effective_gross_income * expenses.variable_rate
    operating_expenses = expenses.fixed_annual + variable_expenses
    net_operating_income = effective_gross_income - operating_expenses

    monthly_payment = mortgage_payment(
        scope.loan_amount, scope.interest_rate, scope.term_years
    )
    debt_service = monthly_payment * 12

    capex_reserve = effective_gross_income * expenses.capex_reserve_rate
    annual_cash_flow = net_operating_income - capex_reserve - debt_service

    if scope.after_repair_value is None:
        mao: float | None = None
        passes_rule: bool | None = None
    else:
        mao = max_allowable_offer(scope.after_repair_value, scope.rehab_budget)
        passes_rule = scope.purchase_price <= mao

    return DealMetrics(
        gross_scheduled_income=gross_scheduled_income,
        vacancy_loss=vacancy_loss,
        effective_gross_income=effective_gross_income,
        operating_expenses=operating_expenses,
        net_operating_income=net_operating_income,
        loan_amount=scope.loan_amount,
        monthly_payment=monthly_payment,
        annual_debt_service=debt_service,
        total_cash_invested=scope.total_cash_invested,
        capex_reserve=capex_reserve,
        annual_cash_flow=annual_cash_flow,
        monthly_cash_flow=annual_cash_flow / 12,
        cap_rate=cap_rate(net_operating_income, scope.purchase_price),
        cash_on_cash_return=cash_on_cash_return(
            annual_cash_flow, scope.total_cash_invested
        ),
        debt_service_coverage_ratio=debt_service_coverage_ratio(
            net_operating_income, debt_service
        ),
        break_even_monthly_rent=break_even_rent(scope),
        max_allowable_offer=mao,
        passes_seventy_percent_rule=passes_rule,
    )
