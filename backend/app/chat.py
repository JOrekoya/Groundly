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

from app import agent as agent_module
from app import narrator as narrator_module
from app import router as router_module
from app.executor import Session, execute
from app.llm import LlmClient
from app.plan import Clarify, Plan, SetField, StepResult, ValueFromComps
from app.resolver import Geocoder, ResolvedLocation, resolve
from app.tools.contract import CompStore, EmptyCompStore


@dataclass(frozen=True)
class ChatTurn:
    """Everything one message produced.

    ``used_llm`` says whether a model was involved at all, and
    ``tool_calls`` names what it did. "No model was involved in this answer"
    is a claim worth being able to make precisely, so it is reported per
    message rather than assumed.
    """

    reply: str
    plan: Plan
    results: tuple[StepResult, ...]
    used_llm: bool
    tool_calls: tuple[str, ...] = ()
    provenance_ok: bool = True
    rejected_figures: tuple[str, ...] = ()
    resolved_location: ResolvedLocation | None = None

    # Kept for callers that predate the agent. Both mean "a model ran".
    @property
    def used_llm_for_planning(self) -> bool:
        return self.used_llm

    @property
    def used_llm_for_narration(self) -> bool:
        return self.used_llm


NO_PLANNER_REPLY = (
    "No model is configured, so I can only handle direct commands right now. "
    "Try \"change the rate to 7%\", \"what is my cash-on-cash\", or "
    "\"what is it worth\". Set ANTHROPIC_API_KEY to turn on the assistant."
)


def handle_message(
    session: Session,
    message: str,
    *,
    store: CompStore | None = None,
    llm: LlmClient | None = None,
    geocoder: Geocoder | None = None,
    narrate: bool = True,
    history: list[dict[str, str]] | None = None,
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

    # With a model available, the model is the interface. The router keeps
    # one job: exact field changes, which are unambiguous and should move a
    # slider in a millisecond rather than a model round trip. Everything else
    # — questions, explanations, valuations, anything conversational — goes
    # to the agent, which can call tools and talk about what it finds.
    if llm is not None:
        fast_path = plan is not None and all(
            isinstance(step, SetField) for step in plan.steps
        )
        if not fast_path:
            turn = agent_module.run(
                session,
                message,
                llm=llm,
                store=store,
                transcript=agent_module.Transcript(history or []),
            )
            return ChatTurn(
                reply=turn.reply,
                plan=Plan(steps=(), source="planner"),
                results=(),
                used_llm=True,
                tool_calls=tuple(o.call.name for o in turn.tool_calls),
                provenance_ok=turn.provenance_ok,
                rejected_figures=turn.rejected_figures,
                resolved_location=resolved,
            )

    # No model, or an exact command: the deterministic path.
    if plan is None and resolved is not None and resolved.source != "session":
        # The message named a property and asked for nothing else. Pasting an
        # address or a coordinate pair has one obvious meaning.
        plan = Plan(steps=(ValueFromComps(),))

    if plan is None:
        plan = Plan(steps=(Clarify(question=NO_PLANNER_REPLY),), source="router")

    results = execute(session, plan, store=store)
    reply = narrator_module.deterministic_narration(results)

    return ChatTurn(
        reply=reply,
        plan=plan,
        results=tuple(results),
        used_llm=False,
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
