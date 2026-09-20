"""Narrator — turns computed numbers into a sentence. Computes nothing.

Optional by design. Every step result already carries a plain-English message
built in Python; the narrator only joins them into something that reads less
like a log. If no model is configured, or the call fails, the deterministic
text is what the user sees, and it is complete on its own.

The one hard rule: **every number in the output must already appear in the
input.** A narrator that can introduce a figure is a narrator that can invent
one, so the output is checked against the numbers it was given and discarded if
it contains any others.
"""

from __future__ import annotations

import re

from app.llm import LlmClient, as_untrusted_data
from app.plan import StepResult

SYSTEM_PROMPT = """\
You rewrite the output of a real estate calculator into one short, plain \
paragraph for an investor.

Absolute rule: you may not introduce, alter, round or compute any number. Every \
figure in your reply must appear verbatim in the input. If a number is not in \
the input, it does not go in your reply.

Do not add advice, caveats about consulting professionals, or encouragement. Do \
not use headings or bullet points. Two or three sentences at most. If the input \
already reads well, return it close to unchanged.
"""

#: Numbers as they appear in rendered output: 1,234.56 / 7.25 / 12
NUMBER_RE = re.compile(r"\d[\d,]*\.?\d*")


def _numbers_in(text: str) -> set[str]:
    """Every number in a string, normalised so 1,200 and 1200 compare equal."""
    return {match.group(0).replace(",", "").rstrip(".") for match in NUMBER_RE.finditer(text)}


def deterministic_narration(results: list[StepResult]) -> str:
    """The narration used when no model is involved.

    This is the fallback and the baseline. It is written to be good enough to
    ship on its own, because it is what users get whenever the narrator is off,
    unavailable, or rejected.
    """
    return "\n".join(result.message for result in results if result.message)


def narrate(
    client: LlmClient | None,
    results: list[StepResult],
    *,
    verify: bool = True,
) -> tuple[str, bool]:
    """Return narration and whether a model produced it.

    Falls back to the deterministic text on any of: no client, a refusal, an
    empty reply, or a reply containing a number that was not in the input.
    """
    baseline = deterministic_narration(results)
    if client is None or not baseline:
        return baseline, False

    reply = client.complete(
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    as_untrusted_data("computed_results", baseline)
                    + "\n\nRewrite the above as one short paragraph."
                ),
            }
        ],
        max_tokens=512,
    )

    if reply.refused or not reply.text.strip():
        return baseline, False

    if verify and not _numbers_in(reply.text) <= _numbers_in(baseline):
        # The narrator introduced a figure that was never computed. Discard it
        # rather than show a number nothing in this system produced.
        return baseline, False

    return reply.text.strip(), True
