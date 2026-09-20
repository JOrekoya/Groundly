# The chat layer — an assistant with tools

**Status: working. Built and tested against a scripted model; needs a key to
run live.**

The chat is a real assistant in the CDRT style: a model that calls the tools
it needs, sees what they return, and explains what the user is looking at. If
you ask what DSCR means and whether yours is any good, it reads your DSCR from
the engine and tells you. If you ask whether this is a good deal, it reads the
whole deal and gives an opinion grounded in the numbers.

It does not add a way for a model to touch a number. That is the invariant
that survives from the spec, and it is enforced rather than hoped for.

## How a message is handled

| Situation | What runs | Model? |
|---|---|---|
| Exact field change ("change the rate to 7%") | Deterministic router → executor | No |
| Anything else, with a key configured | **The agent loop** | Yes |
| Anything else, with no key | Router where it can; honest message where it cannot | No |

The router is kept for one job: exact changes should move a slider in a
millisecond, not a model round trip. Everything conversational goes to the
agent.

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
