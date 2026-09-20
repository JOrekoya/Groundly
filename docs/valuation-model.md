# v1 valuation — weighted nearest comps

**Status: working, with measured accuracy and calibration.** No training, no
model file, no fitting step. Every estimate is a weighted median of real nearby
sales, and the weights are arithmetic a reader can check by hand.

## How it works

Three factors decide how much a comparable sale counts, multiplied together:

| Factor | Shape | Half-life |
|---|---|---|
| Distance | half-life decay, hard cut at 3 miles | 0.5 miles |
| Recency | half-life decay, hard cut at 24 months | 9 months |
| Similarity | size dominant, beds/baths/age adjust | 25% size difference |

Half-life decay rather than buckets, so nothing falls off a cliff: a comp at
0.51 miles is worth fractionally less than one at 0.49, not half as much.

The model values **price per square foot**, then multiplies back up by the
subject's size. A 900 sq ft house and a 2,400 sq ft house on the same street
have wildly different prices but similar rates, and the rate is the more stable
thing to average.

Middle, low and high come from weighted quantiles (50th, 25th, 75th) rather
than a weighted mean, so one mispriced sale cannot drag the answer.

## Measured results

Backtested against real sales: each price hidden in turn and predicted from the
sales that had already happened. A property is never a comp for itself, and
only sales **strictly before** the subject's sale date are used.

| | Cook County, IL | Philadelphia, PA |
|---|---|---|
| Sales backtested | 467 | 456 |
| **High-confidence median error** | **12.6%** | **13.0%** |
| Medium-confidence median error | 17.2% | 19.8% |
| Low-confidence median error | 25.5% | 32.2% |
| Overall median error | 18.2% | 21.7% |
| Interval coverage | 50.1% | 45.4% |

**The confidence grade is the useful output.** Overall median error near 20% is
not a number to act on. But the model grades itself, and the grade is strongly
predictive — high-confidence estimates land near 13% while low-confidence ones
are near 30%. The acceptance bar in `backtest_comp_model.py` is therefore set
on high-confidence accuracy plus calibration, not on the blended average of the
two, because the blend describes nobody's actual experience.

**Calibration.** The published range is a weighted interquartile band, so a
well-calibrated one contains the true price near half the time. Both counties
land at 45–50%, meaning the range is honest: neither uselessly wide nor lying
about its own uncertainty. This is checked on every backtest, because a model
that is accurate but dishonest about its uncertainty is worse than one that is
rough and truthful.

## Why not better than 13%

Assessor characteristics carry no condition, no finish quality, no renovation
history and no photographs. Two identical-on-paper houses on the same block can
differ 40% in price because one has been gutted and the other has not, and
nothing in this data distinguishes them. That is a ceiling on any model built
on these fields, not a defect of this one — and it is the reason the spec
requires a range rather than a point value.

## Choices made by measurement, not taste

**Twelve comps, not twenty-five.** Swept on Philadelphia: 12 gives a median
error of 21.9% against 23.7% at 25 and 24.2% at 5. Too few and one odd sale
swings the answer; too many and distant, dissimilar sales dilute it.

**Outlier trimming is mandatory, not optional.** Philadelphia's livable-area
field is unusable for roughly 4% of records — a $780,000 sale recorded against
183 sq ft. Sales implying under $20 or over $1,500 per square foot are dropped
before weighting. Loading real data drops 2.0% of Philadelphia records and 0.2%
of Cook County's, matching what `docs/data-validation.md` predicted.

**The interval has a floor of ±5%.** Where every comp agrees, the
interquartile band collapses to zero width — a point value wearing the clothing
of a range. Since even high-confidence estimates are 13% off at the median, the
model is never as certain as perfectly agreeing comps make it look. Adding the
floor moved Cook County coverage from 48.0% to 50.1%, so it improved
calibration rather than merely widening the band.

## When it refuses

The model returns `InsufficientComps` — and the API returns
`{"estimated": false}` with a reason at HTTP 200 — when fewer than three usable
comps exist within the radius. A refusal is a successful response, not an
error: a property with no neighbours that sold has no comp-based value, and
dressing that up as a 404 would teach callers to route around it.

Every downgraded estimate also says why: *"comps average 1.2 miles away"*,
*"comparable sales disagree widely"*, *"comps are a median 14 months old"*.

## Running it

```
python backend/load_comps.py --county all --limit 6000
GROUNDLY_COMPS_FILE=comps.json python -m uvicorn app.main:app --app-dir backend
python backend/backtest_comp_model.py --county all
```

The backtest exits 0 only if high-confidence error is within 15%, overall error
within 30%, interval coverage is between 40% and 60%, and high-confidence
estimates actually beat low-confidence ones.

## Not done

- **No CompMap.** The spec lists one; it needs a mapping dependency, and the
  comp table carries the same information in a form that can be read.
- **No Postgres.** Comps are cached to a flat file and held in memory.
  `InMemoryCompStore` and a future Postgres store satisfy the same `CompStore`
  protocol, so that swap reaches neither the model nor the API.
- **No rent estimation.** Sale prices only. The rent field is still typed by
  hand, and phase 2's rent regressor needs a data source that does not yet
  exist.
- **Condition is unmodelled**, as above.
