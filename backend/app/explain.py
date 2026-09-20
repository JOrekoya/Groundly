"""Plain-English explanations of a deal, with no model involved.

For a numbers tool, most of what people want to ask is answerable by rules:
what does this metric mean, is mine any good, what is the weakest part of the
deal, what would help. Each answer here is a template filled with the user's
real figures and judged against the thresholds investors actually use — which
makes it more trustworthy than a model, not less, because it cannot be wrong
about the arithmetic and cannot invent a benchmark.

Written for someone who has never seen these terms. Every explanation says
what the number means and why it matters before it says what the number is.

This is the product's default voice. The LLM layer in ``app.agent`` is an
optional upgrade on top of it for anyone who configures a key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal

from app.executor import Session

Level = Literal["strong", "good", "ok", "weak", "bad"]

_ORDER: dict[Level, int] = {"strong": 4, "good": 3, "ok": 2, "weak": 1, "bad": 0}


def _money(value: float) -> str:
    return f"${value:,.0f}"


def _pct(value: float) -> str:
    """Two decimals, matching the dashboard tiles, so chat and screen agree."""
    return f"{value * 100:.2f}%"


def _cap(text: str) -> str:
    """Capitalise the first letter only. str.capitalize turns DSCR into Dscr."""
    return text[:1].upper() + text[1:] if text else text


@dataclass(frozen=True)
class Assessment:
    """One metric, judged."""

    metric: str
    label: str
    value_text: str
    level: Level
    headline: str
    detail: str


# --------------------------------------------------------------------------
# What each metric means. Written for a first-time reader.
# --------------------------------------------------------------------------

MEANINGS: dict[str, dict[str, str]] = {
    "monthly_cash_flow": {
        "label": "monthly cash flow",
        "meaning": (
            "the money left in your pocket each month after the rent comes in "
            "and everything is paid: the mortgage, taxes, insurance, "
            "maintenance, and a reserve for big repairs"
        ),
        "why": "it is the single most direct answer to 'does this property pay me or cost me every month'",
        "moves": "rent up or expenses down raise it; a higher rate, a bigger loan, or more vacancy lower it",
    },
    "cap_rate": {
        "label": "cap rate",
        "meaning": (
            "the property's yearly profit before any mortgage, divided by what "
            "you paid for it. It says how well the building itself earns, "
            "ignoring how you financed it"
        ),
        "why": "it lets you compare two properties fairly, since financing differs from buyer to buyer",
        "moves": "only the price, the rent, and the operating costs move it. Your loan does not",
    },
    "cash_on_cash_return": {
        "label": "cash-on-cash return",
        "meaning": (
            "your yearly cash flow divided by the cash you actually put in "
            "(down payment, closing costs, any rehab you paid for). It is the "
            "return on your own money, like the interest rate on a savings account"
        ),
        "why": "it tells you what your money is earning here versus anywhere else you could put it",
        "moves": "a smaller down payment can raise it if the loan is cheap enough, and lower it if the loan is expensive",
    },
    "debt_service_coverage_ratio": {
        "label": "DSCR",
        "meaning": (
            "how many times over the property's profit covers the mortgage "
            "payments. 1.0 means it covers them exactly; 1.5 means there is "
            "half again as much profit as debt"
        ),
        "why": "it is the number lenders look at hardest, and a thin one means a small dip in rent puts you underwater",
        "moves": "more profit or a smaller loan raises it; a higher rate or more debt lowers it",
    },
    "break_even_monthly_rent": {
        "label": "break-even rent",
        "meaning": (
            "the lowest monthly rent at which the property neither makes nor "
            "loses money. Everything above it is your cash flow"
        ),
        "why": "the gap between this and your actual rent is your safety margin if rents fall or a tenant leaves",
        "moves": "any cost, including the mortgage, pushes it up",
    },
    "monthly_payment": {
        "label": "monthly payment",
        "meaning": "the principal and interest you owe the lender each month",
        "why": "it is usually the biggest single cost, so it drives everything else",
        "moves": "the loan size, the rate, and the term",
    },
    "net_operating_income": {
        "label": "net operating income",
        "meaning": (
            "yearly rent after vacancy, minus the costs of running the "
            "property, before any mortgage. The building's own profit"
        ),
        "why": "it is what the cap rate and DSCR are built from",
        "moves": "rent, vacancy, and operating costs",
    },
    "total_cash_invested": {
        "label": "cash to close",
        "meaning": "everything you hand over on day one: down payment, closing costs, and rehab you pay for yourself",
        "why": "it is the amount you are risking, and the denominator of your return",
        "moves": "the down payment percentage, closing costs, and whether rehab is financed",
    },
    "passes_seventy_percent_rule": {
        "label": "the 70% rule",
        "meaning": (
            "a flipper's rule of thumb: never pay more than 70% of what the "
            "house will be worth after repairs, minus the cost of those "
            "repairs. The 30% gap is your profit and your cushion"
        ),
        "why": "it is a quick screen for whether a flip has room to make money after surprises",
        "moves": "the purchase price, the after-repair value, and the rehab budget",
    },
}


# --------------------------------------------------------------------------
# Judging each metric against the thresholds investors actually use.
# --------------------------------------------------------------------------


def assess_cash_flow(session: Session) -> Assessment:
    value = session.metrics.monthly_cash_flow
    text = _money(value) + " a month"
    if value < 0:
        return Assessment(
            "monthly_cash_flow", "monthly cash flow", text, "bad",
            f"This property costs you {_money(-value)} a month.",
            "The rent does not cover the mortgage and the running costs. You would be "
            "paying to own it every month, and betting that appreciation makes up for it.",
        )
    if value < 100:
        return Assessment(
            "monthly_cash_flow", "monthly cash flow", text, "weak",
            f"It barely breaks even, at {_money(value)} a month.",
            "One surprise repair or a month without a tenant wipes out a year of this.",
        )
    if value < 300:
        return Assessment(
            "monthly_cash_flow", "monthly cash flow", text, "ok",
            f"It clears {_money(value)} a month.",
            "Positive, but thin. Most investors want more cushion than this per unit.",
        )
    return Assessment(
        "monthly_cash_flow", "monthly cash flow", text, "good",
        f"It clears a healthy {_money(value)} a month.",
        "Enough to absorb a repair or a vacancy without going negative.",
    )


def assess_cap_rate(session: Session) -> Assessment:
    value = session.metrics.cap_rate
    text = _pct(value)
    if value < 0.04:
        return Assessment(
            "cap_rate", "cap rate", text, "weak",
            f"A {text} cap rate is low.",
            "The building earns little for its price. Typical of expensive markets "
            "where buyers are paying for appreciation rather than income.",
        )
    if value < 0.06:
        return Assessment(
            "cap_rate", "cap rate", text, "ok",
            f"A {text} cap rate is modest.",
            "Common in stable, desirable areas. The income is real but not generous.",
        )
    if value < 0.085:
        return Assessment(
            "cap_rate", "cap rate", text, "good",
            f"A {text} cap rate is solid.",
            "The building earns well for its price. This is the range most rental "
            "investors aim for.",
        )
    return Assessment(
        "cap_rate", "cap rate", text, "strong",
        f"A {text} cap rate is high.",
        "Very strong income for the price. Worth asking why: high cap rates often "
        "come with rougher areas, older buildings, or harder-to-keep tenants.",
    )


def assess_cash_on_cash(session: Session) -> Assessment:
    value = session.metrics.cash_on_cash_return
    if value is None:
        return Assessment(
            "cash_on_cash_return", "cash-on-cash return", "n/a", "ok",
            "Cash-on-cash does not apply.", "No cash was invested, so there is no return to measure it against.",
        )
    text = _pct(value)
    if value < 0:
        return Assessment(
            "cash_on_cash_return", "cash-on-cash return", text, "bad",
            f"Your cash-on-cash is negative, at {text}.",
            "Your own money is losing value here every year in cash terms.",
        )
    if value < 0.05:
        return Assessment(
            "cash_on_cash_return", "cash-on-cash return", text, "weak",
            f"A {text} cash-on-cash return is weak.",
            "A savings account or an index fund would do about as well with far "
            "less work and risk.",
        )
    if value < 0.08:
        return Assessment(
            "cash_on_cash_return", "cash-on-cash return", text, "ok",
            f"A {text} cash-on-cash return is acceptable.",
            "Better than leaving the money idle, but not compelling on its own.",
        )
    if value < 0.12:
        return Assessment(
            "cash_on_cash_return", "cash-on-cash return", text, "good",
            f"A {text} cash-on-cash return is good.",
            "This is the range most rental investors are happy with.",
        )
    return Assessment(
        "cash_on_cash_return", "cash-on-cash return", text, "strong",
        f"A {text} cash-on-cash return is strong.",
        "Your money is working hard here.",
    )


def assess_dscr(session: Session) -> Assessment:
    value = session.metrics.debt_service_coverage_ratio
    if value is None:
        return Assessment(
            "debt_service_coverage_ratio", "DSCR", "n/a", "strong",
            "DSCR does not apply.", "There is no mortgage, so there is nothing to cover.",
        )
    text = f"{value:.2f}"
    if value < 1.0:
        return Assessment(
            "debt_service_coverage_ratio", "DSCR", text, "bad",
            f"A DSCR of {text} means the property cannot cover its own mortgage.",
            "You would be paying part of the loan out of pocket. Most lenders will "
            "not finance this.",
        )
    if value < 1.2:
        return Assessment(
            "debt_service_coverage_ratio", "DSCR", text, "weak",
            f"A DSCR of {text} is thin.",
            "It covers the mortgage, but only just. Many lenders want more before "
            "they will lend, and a small rent dip would put you under.",
        )
    if value < 1.25:
        return Assessment(
            "debt_service_coverage_ratio", "DSCR", text, "ok",
            f"A DSCR of {text} is right at the floor lenders usually want.",
            "Financeable, but with little room for things to go wrong.",
        )
    if value < 1.5:
        return Assessment(
            "debt_service_coverage_ratio", "DSCR", text, "good",
            f"A DSCR of {text} is comfortable.",
            "The property covers its mortgage with room to spare.",
        )
    return Assessment(
        "debt_service_coverage_ratio", "DSCR", text, "strong",
        f"A DSCR of {text} is strong.",
        "The mortgage is a small share of what the property earns.",
    )


def assess_margin(session: Session) -> Assessment:
    m = session.metrics
    rent = session.scope.monthly_rent
    breakeven = m.break_even_monthly_rent
    if breakeven is None or rent <= 0:
        return Assessment(
            "break_even_monthly_rent", "rent cushion", "n/a", "ok",
            "There is no rent set, so there is no cushion to measure.", "",
        )
    margin = (rent - breakeven) / rent
    text = f"{_money(breakeven)} against {_money(rent)} in rent"
    if margin < 0:
        return Assessment(
            "break_even_monthly_rent", "rent cushion", text, "bad",
            f"Your rent of {_money(rent)} is below the {_money(breakeven)} break-even.",
            "The property loses money at the current rent.",
        )
    if margin < 0.05:
        return Assessment(
            "break_even_monthly_rent", "rent cushion", text, "weak",
            f"Your rent is only {_pct(margin)} above break-even.",
            "Almost no cushion. A modest rent cut or a vacant month tips it negative.",
        )
    if margin < 0.15:
        return Assessment(
            "break_even_monthly_rent", "rent cushion", text, "ok",
            f"Your rent sits {_pct(margin)} above break-even.",
            "Some cushion against a soft rental market, though not a lot.",
        )
    return Assessment(
        "break_even_monthly_rent", "rent cushion", text, "good",
        f"Your rent sits a comfortable {_pct(margin)} above break-even.",
        "Rents could fall noticeably before this stopped paying.",
    )


def assess_leverage(session: Session) -> Assessment | None:
    """Is borrowing helping or hurting?

    When the property earns more than the loan costs (cap rate above the loan
    constant), borrowing more raises your return. When the loan costs more
    than the property earns, every borrowed dollar loses money. Investors call
    this positive and negative leverage, and it is the single least intuitive
    thing about financing a rental.
    """
    m = session.metrics
    if m.loan_amount <= 0:
        return None
    loan_constant = m.annual_debt_service / m.loan_amount
    cap = m.cap_rate
    if cap >= loan_constant:
        return Assessment(
            "leverage", "leverage", f"{_pct(cap)} cap rate vs {_pct(loan_constant)} loan cost", "good",
            "Your loan is helping you.",
            f"The property earns {_pct(cap)} a year on its price, and the loan costs "
            f"{_pct(loan_constant)} a year. Borrowing at a rate below what the "
            "property earns means each borrowed dollar makes you money, so a smaller "
            "down payment would actually raise your return.",
        )
    return Assessment(
        "leverage", "leverage", f"{_pct(cap)} cap rate vs {_pct(loan_constant)} loan cost", "weak",
        "Your loan is working against you.",
        f"The property earns {_pct(cap)} a year on its price, but the loan costs "
        f"{_pct(loan_constant)} a year. Borrowing at a rate above what the property "
        "earns means each borrowed dollar loses money, so putting more down would "
        "raise your return. A lower rate would fix this.",
    )


def assess_seventy(session: Session) -> Assessment | None:
    m = session.metrics
    if m.max_allowable_offer is None or m.passes_seventy_percent_rule is None:
        return None
    price = session.scope.purchase_price
    ceiling = m.max_allowable_offer
    if m.passes_seventy_percent_rule:
        return Assessment(
            "passes_seventy_percent_rule", "the 70% rule", "passes", "good",
            f"At {_money(price)} this passes the 70% rule, with a ceiling of {_money(ceiling)}.",
            "There is room to profit on a flip after repairs and surprises.",
        )
    return Assessment(
        "passes_seventy_percent_rule", "the 70% rule", "fails", "weak",
        f"At {_money(price)} this fails the 70% rule; the ceiling is {_money(ceiling)}.",
        f"You are paying {_money(price - ceiling)} over what the rule allows. As a flip, "
        "the margin for error is gone.",
    )


#: Above this, the numbers stop describing a real property. Rentals almost
#: never earn more than 15-20% of their price a year; a figure like 90% means
#: the price slider is far too low for the rent, not that the deal is superb.
IMPLAUSIBLE_CAP_RATE = 0.20


def sanity_check(session: Session) -> str | None:
    """Say so when the inputs cannot be a real property.

    Judging nonsense as if it were real is worse than useless for a beginner:
    it teaches them that a 90% cap rate is a great deal. This runs before any
    verdict and replaces it when it fires.
    """
    s, m = session.scope, session.metrics
    if m.cap_rate > IMPLAUSIBLE_CAP_RATE:
        return (
            f"These numbers do not look like a real property. A {_pct(m.cap_rate)} cap "
            f"rate would mean the building earns back {_pct(m.cap_rate)} of its price "
            f"every year, and real rentals almost never manage more than a fifth of "
            f"that. Either the price ({_money(s.purchase_price)}) is far too low for "
            f"the rent ({_money(s.monthly_rent)} a month) or the rent is far too high "
            f"for the price. Check those two sliders before judging the deal."
        )
    return None


ASSESSORS: dict[str, Callable[[Session], Assessment | None]] = {
    "monthly_cash_flow": assess_cash_flow,
    "cap_rate": assess_cap_rate,
    "cash_on_cash_return": assess_cash_on_cash,
    "debt_service_coverage_ratio": assess_dscr,
    "break_even_monthly_rent": assess_margin,
    "leverage": assess_leverage,
    "passes_seventy_percent_rule": assess_seventy,
}


def all_assessments(session: Session) -> list[Assessment]:
    found = [fn(session) for fn in ASSESSORS.values()]
    return [a for a in found if a is not None]


# --------------------------------------------------------------------------
# Whole-deal answers
# --------------------------------------------------------------------------


def explain_deal(session: Session) -> str:
    """The walkthrough for someone who has never seen this page."""
    s, m = session.scope, session.metrics
    warning = sanity_check(session)
    lines = [warning, ""] if warning else []
    lines += [
        f"Here is what you are looking at. You would buy this for {_money(s.purchase_price)}, "
        f"putting {s.down_payment_rate:.0%} down ({_money(s.down_payment)}) and borrowing "
        f"{_money(m.loan_amount)} at {s.interest_rate * 100:.2f}% over {s.term_years} years. "
        f"The mortgage comes to {_money(m.monthly_payment)} a month.",
        "",
        f"It rents for {_money(s.monthly_rent)} a month. After allowing for empty months, "
        f"taxes, insurance, upkeep, management, and a reserve for big repairs, the "
        f"property earns {_money(m.net_operating_income)} a year before the mortgage; "
        f"that is its net operating income.",
        "",
    ]
    cf = assess_cash_flow(session)
    lines.append(f"{cf.headline} {cf.detail}")
    lines.append("")
    lines.append(
        "The tiles at the top are different ways of judging whether that is good:"
    )
    for a in (assess_cap_rate(session), assess_cash_on_cash(session), assess_dscr(session)):
        meaning = MEANINGS[a.metric]["meaning"]
        lines.append(f"- {_cap(a.label)} ({a.value_text}) is {meaning}. {a.headline}")
    margin = assess_margin(session)
    lines.append(f"- Break-even rent is {MEANINGS['break_even_monthly_rent']['meaning']}. {margin.headline}")
    lev = assess_leverage(session)
    if lev is not None:
        lines.append("")
        lines.append(f"{lev.headline} {lev.detail}")
    lines.append("")
    lines.append(
        "Ask about any of these by name, ask \"is this a good deal\", or ask "
        "\"what is the weakest part\"."
    )
    return "\n".join(lines)


def assess_deal(session: Session) -> str:
    """A verdict, with the reasons."""
    warning = sanity_check(session)
    if warning:
        return warning

    found = [a for a in all_assessments(session) if a.metric != "leverage"]
    worst = min(found, key=lambda a: _ORDER[a.level])
    best = max(found, key=lambda a: _ORDER[a.level])
    score = sum(_ORDER[a.level] for a in found) / len(found)

    if worst.level == "bad":
        verdict = "As it stands, this is not a deal I would take."
    elif score >= 3.0:
        verdict = "This looks like a good deal on the numbers."
    elif score >= 2.0:
        verdict = "This is a workable deal, but not a strong one."
    else:
        verdict = "This is a weak deal as it stands."

    lines = [verdict, ""]
    lines.append(f"The best thing about it: {best.headline} {best.detail}")
    if worst.level in ("good", "strong"):
        lines.append(
            f"Nothing here is a real concern. Even the lowest-scoring part, "
            f"{worst.label}, is fine: {worst.headline.lower()}"
        )
    else:
        lines.append(f"The biggest concern: {worst.headline} {worst.detail}")
    lev = assess_leverage(session)
    if lev is not None and lev.level == "weak":
        lines.append("")
        lines.append(f"Also: {lev.headline.lower()} {lev.detail}")
    lines.append("")
    lines.append(
        "Remember this only judges the numbers you entered. It knows nothing about "
        "the building's condition, the neighbourhood, or whether the rent is realistic."
    )
    return "\n".join(lines)


def weakest(session: Session) -> str:
    warning = sanity_check(session)
    if warning:
        return warning
    found = all_assessments(session)
    worst = min(found, key=lambda a: _ORDER[a.level])
    if worst.level in ("good", "strong"):
        return (
            f"Nothing here is weak. The lowest-scoring part is {worst.label}: "
            f"{worst.headline} {worst.detail}"
        )
    return (
        f"The weakest part is {worst.label}. {worst.headline} {worst.detail}\n\n"
        f"What moves it: {MEANINGS.get(worst.metric, {}).get('moves', 'the inputs above')}."
    )


def improve(session: Session) -> str:
    """Concrete levers, aimed at the weakest metric."""
    warning = sanity_check(session)
    if warning:
        return warning
    s, m = session.scope, session.metrics
    found = all_assessments(session)
    worst = min(found, key=lambda a: _ORDER[a.level])
    lines = [f"The thing most worth improving is {worst.label} ({worst.value_text}). Levers, in order of how much they usually help:", ""]

    if worst.metric in ("monthly_cash_flow", "debt_service_coverage_ratio", "break_even_monthly_rent", "cash_on_cash_return"):
        lines.append(
            f"1. Negotiate the price. Every dollar off {_money(s.purchase_price)} shrinks "
            "the loan and the payment. Try dragging the price slider down to see how far "
            "it needs to move."
        )
        lines.append(
            f"2. Get a better rate. You are at {s.interest_rate * 100:.2f}%. The mortgage is "
            f"{_money(m.monthly_payment)} a month, and it is usually the biggest cost."
        )
        lev = assess_leverage(session)
        if lev is not None and lev.level == "weak":
            lines.append(
                "3. Put more down. Because the loan currently costs more than the property "
                "earns, a bigger down payment raises your return as well as your cash flow."
            )
        else:
            lines.append(
                "3. Check the rent. If comparable units get more than "
                f"{_money(s.monthly_rent)}, that is the fastest fix. If they get less, "
                "the deal is worse than it looks."
            )
        lines.append(
            "4. Question the expenses. Taxes and insurance are fixed, but if you will "
            "manage it yourself, the management line goes away."
        )
    elif worst.metric == "cap_rate":
        lines.append(
            "1. Pay less. The cap rate is profit over price, so price is the lever you "
            "control most directly."
        )
        lines.append("2. Raise the rent if the market supports it.")
        lines.append("3. Cut operating costs, though taxes and insurance rarely move.")
    elif worst.metric == "passes_seventy_percent_rule":
        assert m.max_allowable_offer is not None
        lines.append(
            f"1. Offer no more than {_money(m.max_allowable_offer)}. That is the ceiling "
            "the rule allows at this after-repair value and rehab budget."
        )
        lines.append("2. Trim the rehab budget, if you can do it without cutting corners that hurt resale.")
        lines.append("3. Re-check the after-repair value against real comps; if it is higher than you assumed, the ceiling rises.")
    else:
        lines.append("Try the sliders: price, rate and down payment move almost everything.")

    lines.append("")
    lines.append("Every slider on the left re-runs all of this instantly, so the fastest way to explore is to drag one and watch the tiles.")
    return "\n".join(lines)


def explain_metric(name: str, session: Session) -> str:
    info = MEANINGS[name]
    assessor = ASSESSORS.get(name)
    result = assessor(session) if assessor else None
    lines = [
        f"{_cap(info['label'])} is {info['meaning']}.",
        "",
        f"Why it matters: {info['why']}.",
        f"What moves it: {info['moves']}.",
    ]
    if result is not None:
        lines += ["", f"Yours: {result.headline} {result.detail}"]
    elif name in ("monthly_payment", "net_operating_income", "total_cash_invested"):
        value = getattr(session.metrics, name)
        lines += ["", f"Yours is {_money(value)}."]
    elif name == "passes_seventy_percent_rule":
        lines += [
            "",
            "Yours: not checked yet. Enter an after-repair value under Flip "
            "check and it will be.",
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Matching questions
# --------------------------------------------------------------------------

Intent = Literal[
    "explain_deal", "assess_deal", "weakest", "improve", "explain_metric",
    "assess_metric", "leverage", "help", "location",
]

METRIC_WORDS: list[tuple[str, str]] = [
    (r"cash[\s-]*on[\s-]*cash|coc\b|return on (my )?(cash|money)", "cash_on_cash_return"),
    (r"cap\s*rate", "cap_rate"),
    (r"dscr|debt\s*service|coverage\s*ratio", "debt_service_coverage_ratio"),
    (r"break[\s-]*even", "break_even_monthly_rent"),
    (r"cash\s*flow", "monthly_cash_flow"),
    (r"\bnoi\b|net\s*operating", "net_operating_income"),
    (r"mortgage|monthly\s*payment|p\s*&\s*i", "monthly_payment"),
    (r"cash\s*to\s*close|cash\s*invested|out\s*of\s*pocket|money\s*down", "total_cash_invested"),
    (r"70\s*%?\s*rule|seventy", "passes_seventy_percent_rule"),
]

PATTERNS: list[tuple[str, Intent]] = [
    (r"\b(where is (this|it|the property)|which (city|county|state|area)|"
     r"what (city|county|area)|chicago|philadelphia|philly|cook county|"
     r"location|address|neighbou?rhood)\b", "location"),
    (r"\b(leverage|is (the|my) loan (helping|hurting)|more down or less|smaller down payment)\b", "leverage"),
    (r"\b(weakest|weak\s*(spot|point|part)|biggest (risk|concern|problem)|what.{0,12}wrong|risks?\b|worst part|red flag)", "weakest"),
    (r"\b(improve|fix|make (it|this) better|what (should|could|can) i (change|do)|how (do|can|would) i (fix|improve|help)|levers?)\b", "improve"),
    (r"\b(good deal|bad deal|should i (buy|do|take) (it|this)|worth (buying|doing|it)|how does (this|it) look|is (this|it) (any )?good|verdict|thoughts|what do you think|would you (buy|take))\b", "assess_deal"),
    (r"\b(explain|walk me through|what am i looking at|what does (all )?(this|these|it)|what is (all )?this|help me understand|confused|overview|summar|what do (all )?(these|the) numbers mean|break (it|this) down)", "explain_deal"),
    (r"^\s*(help|\?|what can (you|i) (do|ask))\s*\??\s*$", "help"),
]

HELP_TEXT = (
    "You can ask me to explain the deal, ask whether it is a good deal, ask what "
    "the weakest part is or how to improve it, or ask what any number means: "
    "cap rate, cash-on-cash, DSCR, cash flow, break-even rent, the 70% rule. You "
    "can also change things by typing, like \"change the rate to 6.5%\" or \"what "
    "if I put down 25%\". And you can ask what the property is worth from "
    "comparable sales."
)


def _metric_in(text: str) -> str | None:
    for pattern, name in METRIC_WORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return None


def match(message: str) -> tuple[Intent, str | None] | None:
    """Classify a question. Returns the intent and, where relevant, a metric."""
    text = message.strip()
    if not text:
        return None

    metric = _metric_in(text)

    # A metric named alongside a judgement word is "is my X ok".
    if metric and re.search(
        r"\b(is (my|the|this|that)|ok\b|okay|good|bad|fine|healthy|normal|acceptable|"
        r"decent|too (low|high)|high enough|low enough|any good|reasonable)\b",
        text, re.IGNORECASE,
    ):
        return ("assess_metric", metric)

    # A metric named alongside a meaning word is "what does X mean".
    if metric and re.search(
        r"\b(what('s| is| does)|mean|explain|define|definition|tell me about|"
        r"understand|how (is|does).{0,20}(work|calculated|computed))\b",
        text, re.IGNORECASE,
    ):
        return ("explain_metric", metric)

    for pattern, intent in PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return (intent, None)

    # A bare metric name on its own is a request to explain it.
    if metric and len(text.split()) <= 4:
        return ("explain_metric", metric)

    return None


def answer(session: Session, message: str) -> str | None:
    """Answer a question about the deal, or None if it is not one we handle."""
    matched = match(message)
    if matched is None:
        return None
    intent, metric = matched

    if intent == "explain_deal":
        return explain_deal(session)
    if intent == "assess_deal":
        return assess_deal(session)
    if intent == "weakest":
        return weakest(session)
    if intent == "improve":
        return improve(session)
    if intent == "leverage":
        lev = assess_leverage(session)
        if lev is None:
            return "There is no loan on this deal, so leverage does not come into it."
        return f"{lev.headline} {lev.detail}"
    if intent == "help":
        return HELP_TEXT
    if intent == "location":
        if session.latitude is None or session.longitude is None:
            return (
                "This deal has no location yet. The sliders describe a hypothetical "
                "property anywhere. To tie it to a real place, paste coordinates like "
                "\"41.9484, -87.6553\" here, or use the panel on the right to pick "
                "Chicago or Philadelphia and estimate from nearby sales."
            )
        county = "Cook County, Illinois (Chicago area)" if session.latitude > 41 else (
            "Philadelphia, Pennsylvania"
        )
        return (
            f"The property is at {session.latitude:.4f}, {session.longitude:.4f}, "
            f"which is in {county}."
        )
    if intent == "explain_metric" and metric:
        return explain_metric(metric, session)
    if intent == "assess_metric" and metric:
        assessor = ASSESSORS.get(metric)
        if assessor is None:
            return explain_metric(metric, session)
        result = assessor(session)
        if result is None:
            return explain_metric(metric, session)
        # Lead with what the thing is. Someone asking "is my DSCR ok" may not
        # know what DSCR measures, and the verdict means little without that.
        info = MEANINGS[metric]
        meaning = f"{_cap(info['label'])} is {info['meaning']}."
        return meaning + "\n\n" + f"{result.headline} {result.detail}"
    return None
