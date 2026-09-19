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

```
groundly/
  backend/
    app/
      main.py                  (FastAPI entrypoint)
      router.py                (fast deterministic intent matching)
      planner.py                (LLM planner, native tool-calling, ambiguous input only)
      resolver.py               (property and deictic reference resolution)
      executor.py               (runs typed Plan steps, no LLM)
      state.py                  (per-session deal scope, typed)
      narrator.py               (thin narrate-only LLM call)
      finance/
        engine.py                (cap rate, DSCR, cash-on-cash, mortgage math, 70% rule)
      tools/
        contract.py              (typed tool Protocol + fake for tests)
        comps.py
        rates.py
        rehab.py
        geocode.py
      models/
        baseline_comp_model.py    (v1, weighted nearest comp)
        arv_regressor.py          (v2, XGBoost, trained offline)
        rent_regressor.py         (v2)
      ingestion/
        assessor_puller.py
        zillow_puller.py
        redfin_puller.py
        fred_puller.py
        scheduler.py
    tests/
      golden_scopes/             (eval harness fixtures)
      test_finance_engine.py
      test_resolver.py
      test_router.py
  frontend/
    src/
      components/
        ChatPanel.tsx
        DealDashboard.tsx
        CompTable.tsx
        CompMap.tsx
        CashFlowBreakdown.tsx
        ScopeSliders.tsx
      api/
        client.ts
      App.tsx
  infra/
    docker-compose.yml           (local Postgres)
    deploy-notes.md              (Vercel, Render, Supabase)
  docs/
    design.md
    disclaimer.md
```

## Roadmap

**Phase 1** — deterministic finance engine plus the weighted-comp baseline, scoped to one or two validated counties. Chat panel plus dashboard with sliders that update instantly, no LLM in that loop. Deploy free tier, disclaimer included, get first real users from r/realestateinvesting or BiggerPockets.

**Phase 2** — XGBoost ARV regressor once comp volume is proven, rent regression, narrator LLM layer turned on.

**Phase 3** — full agent and tool-calling layer for open-ended questions, expanded eval harness, more counties.

## What the project does, technical version

A typed deal-scope object holds the current state of a deal. A router matches incoming messages against known deterministic patterns first, falling back to an LLM planner (native tool-calling, not text parsing) only for genuinely ambiguous language. The planner's output is a validated list of typed tool calls, run by a plain executor with no further LLM involvement. All financial math lives in pure Python functions. Property valuation runs through a weighted nearest-comp baseline first, with an optional gradient-boosted regressor layered on once enough transaction-level comp data is validated, and every estimate carries an explicit confidence interval. The LLM's only role is narrating computed results and resolving ambiguous references. It never computes or invents a number.

## What the project does, plain version

Tell the app about a property, maybe just the address, maybe the price and down payment. It works out the real math behind the deal using actual formulas, not guesses. For figuring out what the home is worth, it looks at similar homes that sold nearby and gives a range rather than one falsely exact number. Ask questions in plain English, like "what if I put down 25% instead," and it updates everything instantly. The AI part is the translator. It understands the question and explains the numbers in a sentence, but never makes the numbers up. If there isn't enough good data nearby to trust an estimate, it says so honestly instead of pretending.
