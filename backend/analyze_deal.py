"""Command-line front end for the finance engine, for checking it by hand.

Throwaway convenience, not part of the architecture. The real entry points will
be the FastAPI layer and the dashboard; this exists so a deal can be sanity
checked against a mortgage calculator without opening a browser.

    python backend/analyze_deal.py --price 300000 --rent 2800 --rate 0.07
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Run from anywhere without exporting PYTHONPATH first. Only this convenience
# script does this; the test suite gets the path from pyproject.toml instead.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.finance.engine import calculate_deal_metrics  # noqa: E402
from app.state import DealScope, OperatingExpenses  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze a deal.")
    parser.add_argument("--price", type=float, required=True)
    parser.add_argument("--rent", type=float, default=0.0, help="monthly rent")
    parser.add_argument("--down", type=float, default=0.20, help="fraction, e.g. 0.25")
    parser.add_argument("--rate", type=float, default=0.07, help="fraction, e.g. 0.065")
    parser.add_argument("--term", type=int, default=30)
    parser.add_argument("--closing", type=float, default=0.02)
    parser.add_argument("--rehab", type=float, default=0.0)
    parser.add_argument("--finance-rehab", action="store_true")
    parser.add_argument("--arv", type=float, default=None)
    parser.add_argument("--vacancy", type=float, default=0.05)
    parser.add_argument("--taxes", type=float, default=0.0, help="annual")
    parser.add_argument("--insurance", type=float, default=0.0, help="annual")
    parser.add_argument("--hoa", type=float, default=0.0, help="annual")
    return parser


def money(value: float) -> str:
    return f"${value:,.2f}"


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f}%"


def ratio(value: float | None) -> str:
    return "n/a (no debt)" if value is None else f"{value:.2f}"


def format_report(scope: DealScope, metrics: object) -> str:
    m = metrics
    lines = [
        "",
        f"  Price {money(scope.purchase_price)}   "
        f"Down {scope.down_payment_rate:.0%}   "
        f"Rate {scope.interest_rate:.3%}   "
        f"Term {scope.term_years}yr",
        "  " + "-" * 56,
        f"  Loan amount             {money(m.loan_amount):>20}",
        f"  Monthly P&I             {money(m.monthly_payment):>20}",
        f"  Cash to close           {money(m.total_cash_invested):>20}",
        "",
        f"  Effective gross income  {money(m.effective_gross_income):>20}",
        f"  Operating expenses      {money(-m.operating_expenses):>20}",
        f"  Net operating income    {money(m.net_operating_income):>20}",
        f"  Capex reserve           {money(-m.capex_reserve):>20}",
        f"  Debt service            {money(-m.annual_debt_service):>20}",
        f"  Annual cash flow        {money(m.annual_cash_flow):>20}",
        f"  Monthly cash flow       {money(m.monthly_cash_flow):>20}",
        "",
        f"  Cap rate                {percent(m.cap_rate):>20}",
        f"  Cash-on-cash            {percent(m.cash_on_cash_return):>20}",
        f"  DSCR                    {ratio(m.debt_service_coverage_ratio):>20}",
        f"  Break-even rent         {money(m.break_even_monthly_rent or 0):>20}",
    ]
    if m.max_allowable_offer is not None:
        verdict = "PASS" if m.passes_seventy_percent_rule else "FAIL"
        lines += [
            "",
            f"  70% rule ceiling        {money(m.max_allowable_offer):>20}",
            f"  70% rule                {verdict:>20}",
        ]
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = build_parser().parse_args()
    scope = DealScope(
        purchase_price=args.price,
        monthly_rent=args.rent,
        down_payment_rate=args.down,
        interest_rate=args.rate,
        term_years=args.term,
        closing_cost_rate=args.closing,
        rehab_budget=args.rehab,
        finance_rehab=args.finance_rehab,
        after_repair_value=args.arv,
        vacancy_rate=args.vacancy,
        expenses=OperatingExpenses(
            property_taxes_annual=args.taxes,
            insurance_annual=args.insurance,
            hoa_annual=args.hoa,
        ),
    )
    print(format_report(scope, calculate_deal_metrics(scope)))


if __name__ == "__main__":
    main()
