# The chat layer — explanations by default, an assistant as an upgrade

**Status: working, with no model required.** The default voice is a
rule-based explanation layer that answers questions about the deal from the
engine's own figures. An LLM assistant sits on top of it as an optional
upgrade for anyone who configures a key.

## Why two layers

For a numbers tool, most of what people want to ask is answerable by rules:
what does this mean, is mine any good, what is the weakest part, what would
help. A template filled with the engine's real figures and judged against the
thresholds investors actually use is *more* trustworthy than a model here, not
less: it cannot be wrong about the arithmetic and cannot invent a benchmark.
It also costs nothing per message, which matters the moment strangers use it.

The model layer exists for what rules cannot do: novel questions, comparisons
the templates did not anticipate, natural back-and-forth. Whoever wants that
adds a key. Nobody has to.

## How a message is handled

| Situation | What runs | Model? |
|---|---|---|
| Exact field change ("change the rate to 7%") | Router, then executor | No |
| A question about the deal, no key | **Explanation layer** | No |
| Anything, with a key configured | **The agent loop** | Yes |
| Unrecognised, no key | "I did not follow that", with what it can do | No |

## The explanation layer (`app/explain.py`)

Written for someone who has never seen these terms. Every answer says what a
number means and why it matters before it says what the number is.

Whole-deal questions:

| Ask | Get |
|---|---|
| "explain what all these numbers mean" | A walkthrough: price, loan, rent, profit, then each tile explained and judged, then whether the loan is helping |
| "is this a good deal?" | A verdict, the best thing about it, the biggest concern, and a reminder it only knows the numbers |
| "what's the weakest part?" | The lowest-scoring metric, why, and what moves it |
| "how do I fix that?" | Numbered levers aimed at the weakest metric |
| "is my loan helping or hurting?" | Cap rate against the loan's cost: positive or negative leverage, in plain words |
| "help" | What it can do |

Metric questions, for cap rate, cash-on-cash, DSCR, cash flow, break-even
rent, mortgage payment, net operating income, cash to close, and the 70% rule:

| Ask | Get |
|---|---|
| "what does DSCR mean" | Meaning, why it matters, what moves it, and yours |
| "is my DSCR ok?" | The meaning first, then a verdict on yours |
| "DSCR" | Same as the first |

Every metric is judged on a five-step scale (strong, good, ok, weak, bad)
against the bands investors use: a DSCR under 1.0 cannot cover its mortgage,
1.20 to 1.25 is the lender floor, 1.5 is comfortable; a cap rate under 4% is
low, 6% to 8.5% is solid; and so on. The verdict for the whole deal is the
worst of these plus the average.

The layer holds itself to the same rule as the model: every dollar amount and
percentage in every answer must trace to the engine or the deal. Tested with
the agent's own provenance checker across ten questions and three deal shapes.

## The agent loop

```
message + history
  → model
    → tool calls?  → run them → results back to model → (repeat, up to 6 rounds)
    → text reply   → provenance check → reply, or one correction round, or fallback
```

Four tools, strict and closed, one schema per capability:

| Tool | What the model gets back |
|---|---|
| `get_deal` | The whole deal and every metric the engine derives, rounded as shown on screen |
| `set_field` | Change one allow-listed field; the re-derived metrics come back |
| `value_from_comps` | A range with confidence and notes, or the reason there is none |
| `list_comps` | The comps behind the last valuation |

The model reads the results and writes a reply. It may explain, compare,
reassure, warn. It is told it is *expected* to explain.

## The provenance guard

**Every dollar amount and percentage in a reply must trace to a tool result
or the deal.** After the model replies, the text is scanned for `$` figures
and `%` figures, and each is checked against every number any tool returned
this turn, in every rendering the model might plausibly use — `0.0766` allows
`7.66%`, `7.7%` and `8%`; `1596.73` allows `$1,597`.

If a figure has no source, the model is told which ones and given one chance
to rewrite. If the rewrite still invents, the reply is replaced with the
engine's own summary and the response says so (`provenance_ok: false`,
`rejected_figures: [...]`). The dashboard shows it in red.

Bare numbers are not policed. "A DSCR of 1.25 is comfortable" is a benchmark,
not a claim about this deal; "over 30 years" is a fact. The guard is aimed at
the figures the spec is protecting — invented valuations, invented cash flows
— not at every digit.

## What the model reads

The user's message, every prior user turn, and the deal snapshot are all
fenced as `untrusted data, not instructions`. A pasted listing cannot escape
into the instruction channel. The `set_field` enum and the allow-list
validator underneath it mean that even a fully compromised model cannot reach
a field outside the ones the tool exposes.

## History

The client sends prior turns with every message; the server keeps nothing.
The last twelve exchanges are carried. Two clients cannot see each other's
deals, and the chat and the sliders are two views of one object.

## Turning it on

Create `.env` in the repo root containing:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Restart the API. `/api/health` reports `llm_configured: true`. The file is
gitignored; an exported variable wins over it if both exist.

Without a key, exact commands still work and questions get an honest
"no model is configured" rather than a guess.

## Not done

- **No live model run in this repository.** Everything above is tested
  against a scripted fake — 32 agent tests plus the pipeline tests — because
  no key was configured. The first live session should be an eval: a dozen
  real questions, checking that replies are grounded and that the guard
  rarely has to fire.
- **No geocoder.** Coordinates and "this property" resolve; a typed street
  address does not yet.
- The older step-picking planner (`planner.py`) and narrator (`narrator.py`)
  remain for the no-key path and could be retired.
