"""Deterministic first pass over an incoming message.

Most of what people type at a deal analyzer is not ambiguous. "change rate to
7%", "what's my cash-on-cash", "show comps" have exactly one reading, and
sending them to a model would add latency, cost and a failure mode in exchange
for nothing.

So this runs first, and only genuinely ambiguous language falls through to the
planner. Everything here is pure: same string in, same plan out, no network, no
model, no state.

The router is deliberately conservative. Matching something it should not is far
worse than declining and letting the planner handle it — a wrong match silently
changes the user's deal, while a decline just costs a model call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.plan import (
    Plan,
    SetField,
    ShowComps,
    ShowMetric,
    ShowSummary,
    Step,
    ValueFromComps,
)

#: A number with optional $ , % and decimals: "7", "7.5%", "$1,200", "350k".
NUMBER = r"\$?\s*(\d[\d,]*\.?\d*)\s*(k|m|%)?"

#: Fields that are rates. A bare number for these means percent, so "change
#: rate to 7" is 7%, not 700%.
RATE_FIELDS = frozenset(
    {
        "down_payment_rate",
        "interest_rate",
        "closing_cost_rate",
        "vacancy_rate",
        "maintenance_rate",
        "management_rate",
        "capex_reserve_rate",
    }
)

#: Phrases that name a scope field, longest first so "down payment" is tried
#: before "payment" could match something else.
FIELD_PHRASES: list[tuple[str, str]] = [
    (r"down\s*payment|down", "down_payment_rate"),
    (r"interest\s*rate|rate|apr", "interest_rate"),
    (r"purchase\s*price|price|purchase", "purchase_price"),
    (r"monthly\s*rent|rent", "monthly_rent"),
    (r"term|loan\s*length|amortization", "term_years"),
    (r"closing\s*costs?|closing", "closing_cost_rate"),
    (r"rehab|renovation|repairs?\s*budget", "rehab_budget"),
    (r"arv|after[\s-]*repair\s*value", "after_repair_value"),
    (r"vacancy", "vacancy_rate"),
    (r"property\s*tax(es)?|taxes", "property_taxes_annual"),
    (r"insurance", "insurance_annual"),
    (r"hoa", "hoa_annual"),
    (r"maintenance", "maintenance_rate"),
    (r"management|property\s*manager", "management_rate"),
    (r"capex|capital\s*expenditure", "capex_reserve_rate"),
    (r"other\s*income", "other_monthly_income"),
]

#: Phrases that name a metric.
METRIC_PHRASES: list[tuple[str, str]] = [
    (r"cash[\s-]*on[\s-]*cash|coc\b", "cash_on_cash_return"),
    (r"cap\s*rate", "cap_rate"),
    (r"dscr|debt\s*service\s*coverage", "debt_service_coverage_ratio"),
    (r"monthly\s*cash\s*flow", "monthly_cash_flow"),
    (r"annual\s*cash\s*flow|yearly\s*cash\s*flow", "annual_cash_flow"),
    (r"cash\s*flow", "monthly_cash_flow"),
    (r"noi|net\s*operating\s*income", "net_operating_income"),
    (r"break[\s-]*even", "break_even_monthly_rent"),
    (r"mortgage|monthly\s*payment|p\s*&\s*i|principal\s*and\s*interest",
     "monthly_payment"),
    (r"cash\s*to\s*close|cash\s*invested|out\s*of\s*pocket", "total_cash_invested"),
    (r"loan\s*amount|how\s*much.*borrow", "loan_amount"),
    (r"70\s*%?\s*rule|seventy\s*percent", "passes_seventy_percent_rule"),
]

SUMMARY_PATTERN = re.compile(
    r"\b(summar(y|ise|ize)|overview|how\s+does\s+(this|it)\s+look|"
    r"run\s+(the\s+)?numbers|analy[sz]e|the\s+deal|recap)\b",
    re.IGNORECASE,
)

#: "show me those comps", "list the comparable sales", "what comps did you use".
#: A short window between the verb and the noun rather than an exact phrase, so
#: ordinary determiners ("the", "those", "your") do not each need enumerating.
COMPS_PATTERN = re.compile(
    r"\b(show|list|see|what|which)\b[^.?!]{0,24}\bcomps?\b|\bcomparable\s+sales?\b",
    re.IGNORECASE,
)

VALUE_PATTERN = re.compile(
    r"\b(what.{0,25}worth|value\s+(it|this|the\s+property)|estimate\s+(the\s+)?value|"
    r"appraise|how\s+much\s+is\s+(it|this).{0,20}worth)\b",
    re.IGNORECASE,
)

#: Messages asking for reasoning rather than a lookup. "Why is the cash flow
#: negative and what should I change" names a metric, but reporting that metric
#: is not an answer to it.
#:
#: The router's bias is to decline: a wrong match silently misanswers someone's
#: deal, while a decline costs one model call.
REASONING_PATTERN = re.compile(
    r"\b(why|should\s+i|explain|compare|versus|vs\.?|better|worse|"
    r"what\s+should|how\s+come|is\s+(this|it|that)\s+a\s+good)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedNumber:
    """A number and whether it was written as a percent."""

    value: float
    was_percent: bool


def parse_number(raw: str, suffix: str | None) -> ParsedNumber:
    """Turn a matched number and its suffix into a value.

    ``k`` and ``m`` expand; ``%`` divides by a hundred and is remembered, since
    whether the user typed a percent sign decides how a bare number on a rate
    field should be read.
    """
    value = float(raw.replace(",", ""))
    suffix = (suffix or "").lower()

    if suffix == "k":
        return ParsedNumber(value * 1_000, False)
    if suffix == "m":
        return ParsedNumber(value * 1_000_000, False)
    if suffix == "%":
        return ParsedNumber(value / 100, True)
    return ParsedNumber(value, False)


def normalise_for_field(field: str, parsed: ParsedNumber) -> float:
    """Interpret a number in the context of the field it is being set on.

    "change the rate to 7" means 7%, not 700%. A rate above 1 that was written
    without a percent sign is therefore divided — someone typing 0.07 means
    0.07, and someone typing 7 means the same thing.
    """
    if field not in RATE_FIELDS:
        return parsed.value
    if parsed.was_percent:
        return parsed.value
    return parsed.value / 100 if parsed.value > 1 else parsed.value


def _find_field(text: str) -> str | None:
    for pattern, field in FIELD_PHRASES:
        if re.search(rf"\b({pattern})\b", text, re.IGNORECASE):
            return field
    return None


def _find_metric(text: str) -> str | None:
    for pattern, metric in METRIC_PHRASES:
        if re.search(pattern, text, re.IGNORECASE):
            return metric
    return None


#: Verbs that signal a change rather than a question.
SET_PATTERN = re.compile(
    r"\b(change|set|make|use|try|what\s+if|adjust|move|bump|raise|lower|drop|"
    r"increase|decrease|put)\b",
    re.IGNORECASE,
)


def route(message: str) -> Plan | None:
    """Match a message against known patterns.

    Returns ``None`` when nothing matches confidently, which is the signal to
    escalate to the LLM planner. Declining is cheap; matching wrongly is not.
    """
    text = message.strip()
    if not text:
        return None

    # A request for reasoning is never a lookup, even when it names a metric.
    if REASONING_PATTERN.search(text):
        return None

    numbers = list(re.finditer(NUMBER, text))

    # A change: needs a verb that implies one, a field, and a number.
    if SET_PATTERN.search(text) and numbers:
        field = _find_field(text)
        if field is not None:
            match = numbers[-1]
            parsed = parse_number(match.group(1), match.group(2))
            value = normalise_for_field(field, parsed)
            try:
                return Plan(steps=(SetField(field=field, value=value),))
            except ValueError:
                return None

    # "25% down" and "7% rate" read as changes without a verb, because a bare
    # percent next to a field name has no other plausible meaning.
    if numbers:
        for match in numbers:
            tail = text[match.start() : match.end() + 24]
            head = text[max(0, match.start() - 24) : match.end()]
            field = _find_field(tail) or _find_field(head)
            if field is not None and match.group(2) == "%":
                parsed = parse_number(match.group(1), match.group(2))
                return Plan(
                    steps=(
                        SetField(field=field, value=normalise_for_field(field, parsed)),
                    )
                )

    if COMPS_PATTERN.search(text):
        return Plan(steps=(ShowComps(),))

    if VALUE_PATTERN.search(text):
        return Plan(steps=(ValueFromComps(),))

    metric = _find_metric(text)
    if metric is not None:
        return Plan(steps=(ShowMetric(metric=metric),))

    if SUMMARY_PATTERN.search(text):
        return Plan(steps=(ShowSummary(),))

    return None


def describe(step: Step) -> str:
    """One line naming what a step will do, for a confirmation line."""
    if isinstance(step, SetField):
        return f"set {step.label}"
    if isinstance(step, ShowMetric):
        return f"report {step.label}"
    if isinstance(step, ShowSummary):
        return "summarise the deal"
    if isinstance(step, ValueFromComps):
        return "estimate value from comps"
    if isinstance(step, ShowComps):
        return "list comparable sales"
    return "ask a clarifying question"
