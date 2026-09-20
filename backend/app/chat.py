"""The conversation pipeline: route, maybe plan, execute, narrate.

One function ties the layers together, and the ordering is the whole design:

1. **Resolve** any property reference in the message. Deterministic.
2. **Route.** If a known pattern matches, the plan is made with no model call.
3. **Plan.** Only if the router declined, and only if a model is configured.
4. **Execute.** Always deterministic. The only thing that changes the deal.
5. **Narrate.** Optional polish over numbers Python already computed.

The load-bearing property is that steps 1, 2 and 4 involve no model at all.
Most messages never reach step 3, and a system with no API key configured still
answers everything the router understands — which, for a numbers tool, is most
of what people actually type.
"""

from __future__ import annotations

from dataclasses import dataclass

from app import narrator as narrator_module
from app import planner as planner_module
from app import router as router_module
from app.executor import Session, execute
from app.llm import LlmClient
from app.plan import Clarify, Plan, StepResult, ValueFromComps
from app.resolver import Geocoder, ResolvedLocation, resolve
from app.tools.contract import CompStore, EmptyCompStore


@dataclass(frozen=True)
class ChatTurn:
    """Everything one message produced.

    ``used_llm_for_planning`` and ``used_llm_for_narration`` are reported
    separately and shown in the UI, because "no model was involved in this
    answer" is a claim worth being able to make precisely.
    """

    reply: str
    plan: Plan
    results: tuple[StepResult, ...]
    used_llm_for_planning: bool
    used_llm_for_narration: bool
    resolved_location: ResolvedLocation | None = None


NO_PLANNER_REPLY = (
    "I did not recognise that as a change or a question I can answer directly. "
    "Try something like \"change the rate to 7%\", \"what is my cash-on-cash\", "
    "or \"what is it worth\"."
)


def handle_message(
    session: Session,
    message: str,
    *,
    store: CompStore | None = None,
    llm: LlmClient | None = None,
    geocoder: Geocoder | None = None,
    narrate: bool = True,
) -> ChatTurn:
    """Run one turn of conversation.

    ``llm`` is optional throughout. Without it the router still answers, and a
    message the router cannot match gets an honest explanation rather than a
    silent failure.
    """
    store = store or EmptyCompStore()
    session.history.append(message)

    located = resolve(
        message,
        current=(
            (session.latitude, session.longitude)
            if session.latitude is not None and session.longitude is not None
            else None
        ),
        geocoder=geocoder,
    )
    resolved = located if isinstance(located, ResolvedLocation) else None
    if resolved is not None and resolved.source != "session":
        session.latitude = resolved.latitude
        session.longitude = resolved.longitude

    plan = router_module.route(message)
    used_llm_for_planning = False

    if plan is None and resolved is not None and resolved.source != "session":
        # The message named a property and asked for nothing else. Pasting an
        # address or a coordinate pair has one obvious meaning, so handle it
        # here rather than spending a model call on it.
        plan = Plan(steps=(ValueFromComps(),))

    if plan is None:
        if llm is not None:
            plan = planner_module.plan(llm, message, context=_context(session))
            used_llm_for_planning = True
        else:
            plan = Plan(steps=(Clarify(question=NO_PLANNER_REPLY),), source="router")

    results = execute(session, plan, store=store)

    reply, used_llm_for_narration = narrator_module.narrate(
        llm if narrate else None, results
    )

    return ChatTurn(
        reply=reply,
        plan=plan,
        results=tuple(results),
        used_llm_for_planning=used_llm_for_planning,
        used_llm_for_narration=used_llm_for_narration,
        resolved_location=resolved,
    )


def _context(session: Session) -> dict:
    """The deal, as the planner sees it.

    Only the fields a planner could reasonably act on. It is passed as
    untrusted data, and it exists so the model can tell "raise the rent" from
    "raise the rate" — not so it can reason about numbers.
    """
    scope = session.scope
    return {
        "purchase_price": scope.purchase_price,
        "monthly_rent": scope.monthly_rent,
        "down_payment_rate": scope.down_payment_rate,
        "interest_rate": scope.interest_rate,
        "term_years": scope.term_years,
        "strategy": scope.strategy.value,
        "has_location": session.latitude is not None,
        "has_valuation": session.last_valuation is not None,
    }
