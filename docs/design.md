# Groundly — Project Spec

Real estate deal analyzer. A numbers-first tool that takes a property or a rough deal and returns estimated value, financing math, cash flow, and risk flags. All math runs in Python. The LLM's only job is understanding the request and narrating the numbers back — it never invents a figure.

Designed in the same style as CDRT (deterministic core, LLM only for ambiguity resolution and narration).

## Core idea

A user drops in a property address, or a rough deal (price, location, strategy). The system returns a grounded analysis with a confidence range on any estimate, not a single hard number.

## Architecture

**Deal Scope** — a typed object holding the current deal (address, price, down payment %, rate, term, rehab budget, strategy). Every calculation reads from it. Every mutation updates it in one place.

**Router** — a cheap, deterministic first pass that matches the message against known patterns ("change rate to 7%," "show comps," "what's my cash-on-cash") before touching an LLM.

**Planner** — reached only when the router can't confidently match a pattern. Uses native tool-calling. Its output is a list of typed calls validated against the tool schemas, never a parsed text envelope.

**Resolver** — turns "this property," "the last one," or a plain address into a concrete property record and comp set.

**Executor** — runs resolved steps against the tools. No LLM. Fully testable with no network call.

**Finance engine** — pure Python, no model in the loop.

- Cap rate = NOI / purchase price
- Cash-on-cash return = annual cash flow / total cash invested
- Mortgage P&I from rate/down payment/term
- DSCR (debt service coverage ratio)
- 70% rule check for flips
- Break-even rent

Changing one field in the scope re-derives everything with no LLM round trip.

**Valuation model, phased**

- **v1** — weighted nearest-comp model (distance, recency, similarity weighting). No training needed, fully explainable, and stays on as a sanity-check baseline even after v2 exists.
- **v2** — gradient-boosted regressor (XGBoost or LightGBM), trained on real per-property sold comps, once real comp data is confirmed for the target counties.
- Either way, output carries a confidence range, never a single point value. When comps are too sparse or too far away to trust, return the closest available comps flagged as low confidence rather than a falsely precise number.

**Narrator** — a thin, optional LLM call that turns computed numbers into a sentence. Never computes anything.

## Data plan — validate this first

Zillow and Redfin's free bulk downloads are aggregated by zip/metro, not per-property sale prices. Training the v2 regressor needs real transaction-level records, usually from county assessor or deed data, and coverage varies a lot by county.

Before writing model code — pick one or two counties with genuinely good open sale-price data and confirm real comps can actually be pulled. Scope v1 to those counties only. This is the biggest external risk in the whole plan.

## Tool surface (typed, allow-listed)

- `get_property(address)` — assessor lookup, characteristics
- `get_comps(property_id, radius)` — cached comps from the DB
- `get_market_rates()` — cached FRED data
- `estimate_rehab_cost(sqft, condition)` — heuristic or ML-based
- `geocode(address)`
- `calculate_deal_metrics(scope)` — runs the finance engine

Any listing description or MLS remarks pulled into context is framed as untrusted data, never instructions, since it's free text written by a stranger.

## Eval harness (build early)

A set of golden deal-scopes with known correct cap rate, DSCR, and cash-on-cash values, checked automatically on every change. Cheap here since the finance engine is pure functions from day one.

## Repo structure

Files marked ✓ exist today; the rest are planned. The ingestion layer deviates
from the original sketch: assessor schemas are entirely county-specific, so
each county gets its own adapter module rather than a branch inside a shared
`assessor_puller.py`, with shared record types, the adapter protocol, and each
transport factored out alongside them.

```
groundly/
  pyproject.toml               ✓ (pytest config, pythonpath)
  backend/
    analyze_deal.py            ✓ (CLI over the finance engine)
    validate_county_data.py    ✓ (step 2 go/no-go)
    load_comps.py              ✓ (cache county comps for the API)
    backtest_comp_model.py     ✓ (measure the comp model on real sales)
    app/
      main.py                  ✓ (FastAPI: /api/analyze, /api/value, /api/chat, /api/health)
      schemas.py               ✓ (pydantic models at the HTTP edge only)
      explain.py               ✓ (rule-based explanations; the default voice)
      agent.py                 ✓ (optional LLM assistant: tool loop, provenance guard)
      plan.py                  ✓ (typed steps; the executor's allow-list)
      router.py                ✓ (deterministic fast path for exact commands)
      planner.py               ✓ (older step-picking planner; no-key path)
      resolver.py              ✓ (coordinates, addresses, "this property")
      executor.py              ✓ (runs typed steps; the only thing that changes the deal)
      chat.py                  ✓ (the pipeline: resolve, route, plan, execute, narrate)
      llm.py                   ✓ (the one module that imports the SDK; protocol + fake)
      state.py                 ✓ (typed DealScope, frozen, validated)
      narrator.py              ✓ (reword only; discarded if it invents a number)
      finance/
        engine.py              ✓ (cap rate, DSCR, cash-on-cash, mortgage math, 70% rule)
      tools/
        contract.py            ✓ (CompStore Protocol, in-memory and empty stores)
        comps.py               ✓ (find comps, value a property)
        rates.py
        rehab.py
        geocode.py
      models/
        baseline_comp_model.py ✓ (v1, weighted nearest comp, always a range)
        backtest.py            ✓ (accuracy and calibration, pure)
        arv_regressor.py          (v2, XGBoost, trained offline)
        rent_regressor.py         (v2)
      ingestion/
        records.py             ✓ (county-neutral CompSale, Sale, field parsers)
        adapter.py             ✓ (the CountyAdapter Protocol both counties satisfy)
        socrata.py             ✓ (SoQL transport behind a Protocol, plus a fake)
        carto.py               ✓ (Carto SQL transport, plus a fake)
        cook_county.py         ✓ (Cook County IL: 4 Socrata datasets joined on PIN)
        philadelphia.py        ✓ (Philadelphia PA: 1 Carto table, no join)
        coverage.py            ✓ (go/no-go scoring, county-neutral and pure)
        fred_puller.py
        scheduler.py
    tests/
      conftest.py              ✓ (golden-scope loader, shared fixtures)
      golden_scopes/           ✓ (6 eval harness fixtures)
      test_finance_engine.py   ✓
      test_golden_scopes.py    ✓ (the eval harness itself)
      test_ingestion.py        ✓ (Cook parsing, joining, verdicts; no network)
      test_philadelphia.py     ✓ (Philadelphia adapter and the shared boundary)
      test_api.py              ✓ (in-process TestClient; no server, no network)
      test_comp_model.py       ✓ (weighting, trimming, refusals, confidence)
      test_backtest.py         ✓ (no-leakage rules and scoring)
      test_valuation_api.py    ✓ (comp store, /api/value, honest refusals)
      test_router.py           ✓ (68: what it matches, and what it must decline)
      test_explain.py          ✓ (matching, thresholds, and provenance of every answer)
      test_agent.py            ✓ (tool loop, history, the provenance guard)
      test_chat.py             ✓ (pipeline, injection fencing, engine-only numbers)
      test_chat_api.py         ✓ (/api/chat with no key configured)
  frontend/                    ✓ (Vite + React + TypeScript)
    src/
      components/
        ChatPanel.tsx            ✓ (talk to the deal; badge per reply says what answered)
        Guide.tsx                ✓ (collapsible how-this-page-works, for first-timers)
        DealDashboard.tsx        ✓ (headline tiles, 70% rule verdict)
        CompTable.tsx            ✓ (the comps used, and how much each counted)
        CompMap.tsx                (deferred; needs a mapping dependency)
        ValuationPanel.tsx       ✓ (estimate from comps, or say why not)
        CashFlowBreakdown.tsx    ✓ (income statement, line by line)
        ScopeSliders.tsx         ✓ (the what-if controls)
      api/
        client.ts                ✓ (typed client; computes nothing)
      App.tsx                    ✓ (holds scope, debounces, aborts stale requests)
  infra/
    docker-compose.yml           (local Postgres)
    deploy-notes.md              (Vercel, Render, Supabase)
  docs/
    design.md                  ✓ (this document; the spec)
    data-validation.md         ✓ (step 2 county findings)
    valuation-model.md         ✓ (step 4 model, measured accuracy, choices)
    chat-layer.md              ✓ (step 5 pipeline, allow-list, narrator guard)
    running.md                 ✓ (how to run the stack locally)
    disclaimer.md
```

## Roadmap

**Phase 1** — deterministic finance engine plus the weighted-comp baseline, scoped to one or two validated counties. Chat panel plus dashboard with sliders that update instantly, no LLM in that loop. Deploy free tier, disclaimer included, get first real users from r/realestateinvesting or BiggerPockets.

**Phase 2** — XGBoost ARV regressor once comp volume is proven, rent regression, narrator LLM layer turned on.

**Phase 3** — full agent and tool-calling layer for open-ended questions, expanded eval harness, more counties.

## Build sequence

Five steps, ordered so that each one ends in something runnable and checkable
by hand. Two properties of this ordering are deliberate: no step needs an LLM
or an API key until step 5, and network access is confined to data ingestion,
so the entire test suite runs offline at every stage.

| Step | What | Status |
|---|---|---|
| 1 | Deterministic finance engine and golden-scope eval harness | **Done** |
| 2 | County data go/no-go — validate real sale data before modelling | **Done** (Cook County IL, Philadelphia PA) |
| 3 | FastAPI layer and dashboard with live sliders, no LLM in the loop | **Done** |
| 4 | Weighted nearest-comp baseline (v1) on validated county data | **Done** |
| 5 | Chat: rule-based explanations by default, an LLM assistant as an optional upgrade | **Done** (works fully with no key) |

**Step 1 — finance engine.** Pure functions plus the typed deal scope. No web
server, no data source, no model. Ends when the golden-scope harness passes.

**Step 2 — county data go/no-go.** Before any model code, confirm a county can
actually supply transaction-level sale prices joined to usable property
characteristics. This is the biggest external risk in the plan, and it comes
early precisely because a NO-GO would invalidate later work. Two counties
cleared it — Cook County, Illinois and Philadelphia, Pennsylvania — and they
were chosen to be mechanically unalike so that passing both proves the adapter
boundary rather than one county's quirks. Findings live in
`docs/data-validation.md`.

**Step 3 — API and dashboard.** FastAPI over the step 1 engine, with sliders
that re-derive everything on change. Deal inputs are entered by hand; no
address lookup yet. This is the first thing that behaves like a product, and
it is already useful to an investor without a single model call.

The dashboard sends the whole scope on every change and renders what comes
back. It never computes a financial figure locally. A localhost round trip
costs one to two milliseconds, well under the threshold where a drag stops
feeling live, so there is no reason to keep a second copy of the math in
TypeScript — and a second copy is exactly how the browser and the engine start
disagreeing. `POST /api/analyze` echoes the scope alongside the metrics, so the
client always displays what the server actually computed rather than what it
believed it had sent.

**Step 4 — comp baseline.** The weighted nearest-comp model over validated
county data, behind a typed `CompStore` tool contract.

Backtested on real sales in both counties: high-confidence estimates land at a
median error of 12.6% (Cook) and 13.0% (Philadelphia), with interval coverage
at 50.1% and 45.4% against a 50% target for an interquartile band. Overall
median error is near 20%, which is not a number to act on — the value of the
model is that it grades its own output and the grade holds up, with
low-confidence estimates near 30% error and labelled as such. Details in
`docs/valuation-model.md`.

Comps are cached to a flat file and held in memory rather than in Postgres.
That is a deliberate stopping point: a county's recent single-family sales fit
in memory comfortably, and the in-memory store and a future Postgres store
satisfy the same protocol, so the swap reaches neither the model nor the API.

**Step 5 — LLM layer.** Deterministic router first, then narrator, then the
planner. The planner is the hardest component and the least load-bearing,
which is why it is last.

The default voice is a rule-based explanation layer: what each metric means,
whether this deal's number is good against the bands investors use, what the
weakest part is, what would help. Templates filled with the engine's real
figures. It answers a beginner's questions with no model and no cost per
message, which is what lets the product ship to strangers without funding
their conversations.

An LLM assistant is an optional upgrade on top. With a key configured, every
message that is not an exact field change goes to an agent loop: the model
calls tools (read the deal, change a field, value from comps, list comps),
sees their results, calls more if needed, and writes a reply grounded in them.
It handles what rules cannot: novel questions, comparisons, back-and-forth.

The rule that survives is provenance, not silence. Every dollar amount and
percentage in a reply must trace to a tool result or the deal; a reply that
cites an unproduced figure gets one correction round and is otherwise
replaced by the engine's own summary and flagged. The router is kept for exact
changes so a slider moves in a millisecond rather than a model round trip.
Details in `docs/chat-layer.md`.

## Where things stand (2026-09-20)

All five build steps are done and pushed. The product runs fully with no
API key: finance engine, two validated counties, a backtested comp model, a
dashboard with live sliders, and a chat that explains the deal from rules.
514 tests pass in about two seconds with no network. `docs/running.md` says
how to start it.

**Open items, roughly in the order they are worth doing:**

- **First real user test.** Joseph has used the chat once as a beginner and
  found three issues, all fixed. More sessions like that are the best next
  input; wording that does not land for a first-timer is a bug.
- **Live model run.** The LLM assistant (`app/agent.py`) is tested only
  against a scripted fake. Nobody has run it against a real key. The first
  live session should be a short eval of a dozen real questions.
- **Chat does not carry square footage** to valuation, so "what is it worth"
  in chat values on raw price. The valuation panel, which does carry it, is
  sharper.
- **No geocoder.** Coordinates and "this property" resolve; a typed street
  address does not.
- **No frontend tests.** The React layer is checked by `tsc` and by eye.
- **No rent estimation.** Sale prices only. Phase 2's rent regressor needs a
  data source that does not yet exist.
- **No Postgres, no CompMap, no FRED puller.** Deliberate deferrals; see
  `docs/valuation-model.md`.
- The older step-picking planner and narrator (`planner.py`, `narrator.py`)
  are superseded by `explain.py` and `agent.py` and could be retired.

## Testing approach

**The suite runs after every change, not at the end of a task.** It is
deliberately fast — under a second, no network, no database — so there is no
reason to batch edits and test them together. `python -m pytest` from the repo
root.

Three layers, each earning its place:

**Anchors** tie the code to the outside world. The mortgage tests assert
published payment figures ($100k at 6% for 30 years is $599.55) that can be
confirmed on any public calculator. Everything else in the suite is internal
consistency; only these say reality agrees.

**Property tests** assert relationships that hold for any deal rather than
specific numbers: break-even rent fed back into the engine must produce exactly
zero cash flow; changing the loan must never move the cap rate. These catch
bugs nobody thought to write a number for.

**Golden scopes** are committed fixtures in `backend/tests/golden_scopes/`,
one JSON file per deal shape, each with its full expected output. Adding
coverage means adding a file, not editing test code.

Two standing rules:

- **Expected values are derived independently of the engine** — by hand, by
  spreadsheet, or by a separate script that does not import it. Values
  generated by running the engine can only ever confirm it still does what it
  did, bugs included.
- **When a test fails, decide which side is wrong before editing either.**
  Rewriting a fixture to match new output is how a suite quietly stops
  protecting anything.

Per step, the check that actually settles it:

| Step | How it is verified |
|---|---|
| 1 | Golden scopes pass; mortgage payment matches a public calculator to the cent |
| 2 | A county clears explicit volume and completeness thresholds against live data; parsing and joins are tested offline against a fake client |
| 3 | Drag a slider and watch every number re-derive correctly and instantly; API responses asserted equal to the engine's own output |
| 4 | Backtest against held-out sales with no leakage: high-confidence median error within 15%, interval coverage between 40% and 60% for an interquartile band, and high-confidence estimates measurably better than low-confidence ones |
| 5 | Agent loop tested against a scripted model: tools run and results round-trip, history is carried, an invented figure is caught, corrected once, then replaced; the whole layer passes with no API key |

Step 4's second check matters as much as the first. A model that is accurate
but dishonest about its uncertainty is worse than one that is rough and
truthful, given this project promises a range rather than a point value.

## Network boundary

`backend/validate_county_data.py` is the only script that touches the network,
and `socrata.py` and `carto.py` are the only modules that import an HTTP
client. Each takes a transport Protocol and is exercised in tests against a
fake. This mirrors the executor rule from the architecture above: the parts
that can be pure are pure, and the parts that cannot are pushed to the edge
behind a protocol with a fake.

## County adapters

Every county satisfies one `CountyAdapter` protocol — `name`, `count_sales`,
`fetch_comp_sales` — and emits the same county-neutral `CompSale`. Anything
county-specific, such as Cook County's assessment year and township or
Philadelphia's zip scoping, lives in an adapter's constructor rather than in
the protocol.

The two validated counties were picked to be unalike: Socrata against Carto,
four joined datasets against one denormalized table, publisher-supplied
arms-length flags against none. Adding the second required no change to the
coverage scoring or the report, which is the evidence the boundary is real.
Adding a third county should mean one new module and no edits to shared code.

## What the project does, technical version

A typed deal-scope object holds the current state of a deal. A router matches incoming messages against known deterministic patterns first, falling back to an LLM planner (native tool-calling, not text parsing) only for genuinely ambiguous language. The planner's output is a validated list of typed tool calls, run by a plain executor with no further LLM involvement. All financial math lives in pure Python functions. Property valuation runs through a weighted nearest-comp baseline first, with an optional gradient-boosted regressor layered on once enough transaction-level comp data is validated, and every estimate carries an explicit confidence interval. The LLM's only role is narrating computed results and resolving ambiguous references. It never computes or invents a number.

## What the project does, plain version

Tell the app about a property, maybe just the address, maybe the price and down payment. It works out the real math behind the deal using actual formulas, not guesses. For figuring out what the home is worth, it looks at similar homes that sold nearby and gives a range rather than one falsely exact number. Ask questions in plain English, like "what if I put down 25% instead," and it updates everything instantly. The AI part is the translator. It understands the question and explains the numbers in a sentence, but never makes the numbers up. If there isn't enough good data nearby to trust an estimate, it says so honestly instead of pretending.
