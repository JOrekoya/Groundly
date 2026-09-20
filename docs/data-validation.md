# County data validation — step 2 findings

**Verdict: GO for both validated counties, scoped to single-family and small
multi-family — Cook County, Illinois and Philadelphia, Pennsylvania.
Condominiums are out of scope for v1 in Cook County.**

This is the check the project spec called its biggest external risk: before
writing any model code, confirm that real transaction-level sale prices with
usable property characteristics can actually be pulled for a target county.
They can. The numbers below come from live queries run on 2026-09-19 and are
reproducible with `backend/validate_county_data.py`.

## Why Cook County

Illinois is a **disclosure state**, so recorded sale prices are public record.
This is the single most important filter when picking a county, and it
eliminates a lot of otherwise attractive markets. Texas, Idaho, Kansas,
Louisiana, Mississippi, Montana, New Mexico, North Dakota, Utah, Wyoming and
Alaska are **non-disclosure** states where sale prices are not public at all.
No amount of engineering recovers a price that was never recorded, so the whole
of Texas — Travis, Harris, Dallas, Bexar — is unavailable regardless of how good
its open data portals otherwise are.

Beyond disclosure, Cook County publishes something rarer: assessor property
characteristics keyed to the same parcel number as the sales, on a public
Socrata API with no key required and no scraping involved.

## What the data looks like

Four datasets on `datacatalog.cookcountyil.gov`, joined on PIN (the 14-digit
parcel number):

| Dataset | ID | Supplies |
|---|---|---|
| Parcel Sales | `wvhk-k5uv` | Sale price, date, deed type, arms-length flags |
| Single/multi-family characteristics | `x54s-btds` | Beds, baths, building and land size, year built |
| Condominium unit characteristics | `3r7i-mrz4` | Bedrooms, unit size, year built |
| Parcel Universe | `nj4t-kc8j` | Latitude, longitude, township, census tract |

Total sales history is **2,686,086 rows**, current through **2026-07-14** — a
lag of roughly two months, which is well inside what a comp model needs.

## Volume

Arms-length sales in the trailing 18 months, after filtering out bulk
transfers, sub-$10,000 nominal transfers, non-standard deed types, and repeat
sales of the same parcel within a year:

| Scope | Sales |
|---|---|
| All property types | 70,759 |
| **Single-family and small multi-family** | **45,421** |
| Condominiums | 21,923 |
| Single-family, trailing 6 months only | 8,112 |

Against a go/no-go threshold of 2,000, single-family clears by **22×**.

Density holds up at the submarket level too, which matters more than the county
total for a nearest-comp model. The busiest townships carry 5,649, 4,239, 3,072
and 3,061 single-family sales over the window.

## Completeness — and the finding that shaped the scope

Field coverage on a 1,500-sale sample, split by property type:

| Property type | Sales | Building size | Bedrooms | Bathrooms | Coordinates |
|---|---|---|---|---|---|
| **Single-family** | 907 | **100%** | **100%** | **100%** | **100%** |
| Condominium | 515 | 39% | 57% | **0%** | 100% |

The aggregate number across both types is 78% on building size, which reads as
a marginal county. Split by type, the picture is completely different: the
single-family data is flawless, and the condo data is unusable. Cook County
records no bathroom count whatsoever for condominiums, and publishes unit
square footage for well under half of them.

**This is why v1 is scoped to single-family.** Judging the county on its blended
average would have meant either rejecting a county with 45,000 perfectly
described sales, or building a model on condo records missing most of their
features. The aggregate hid both.

Scoped to single-family, the live run reports:

```
  Arms-length sales in window          45,421
  Sampled and joined                    1,587  ( 88.2% of 1,800)
  Building size present                100.0%
  Bedrooms present                     100.0%
  Bathrooms present                    100.0%
  Coordinates present                  100.0%
  Median sale price                  $390,000
  Median price per sq ft                 $247
  VERDICT                                  GO
```

A $390,000 median against a Chicago-area market is plausible, and $247 per
square foot is in the right band. Both are sanity checks on the arms-length
filter: a broken filter shows up as a median in the low tens of thousands.

Note the join rate is 88%, not the 99% an earlier run reported. That earlier
figure was wrong, and the reason is worth recording — see the sampling trap
below.

## Traps found along the way

**Condos are in a separate table.** The single-family characteristics dataset
excludes them entirely. Querying only that table silently drops 31% of the
county's residential turnover with no error — the sales simply fail to join.
Covered by a regression test.

**The condo table has two size columns and the obvious one is wrong.**
`char_building_sf` is the size of the entire structure; `char_unit_sf` is the
unit. Using the first as a comp feature would value a 1,198 sq ft condo as if it
were a 138,960 sq ft property. Covered by a regression test.

**Characteristics are published per assessment year.** Joining against an old
year silently loses recent construction. The adapter takes the year explicitly
rather than defaulting silently.

**Every value arrives as a string**, including numbers, and bedroom counts come
through as `"3.0"`, which `int()` rejects outright.

**Sorting by date and taking the first N is not a sample of the window.** The
first version of this validator pulled sales ordered by date descending, so an
18-month window was in fact measured on the most recent 2.5 months, and
reported a 99.5% join rate. The true figure across the window is 88%.

The cause is that assessor characteristics are published per assessment year,
so a parcel's presence in the current year's file decays slightly as a sale
ages:

| Sale month | Size join | Geo join |
|---|---|---|
| 2025-04 (oldest) | 96% | 98% |
| 2025-10 (mid) | 98% | 99% |
| 2026-06 (recent) | 99% | 100% |

The gradient is mild and every point clears the thresholds, so the GO verdict
is unaffected — but the reported number was flattering rather than true. The
pull is now stratified across the calendar months of the window.

## Class codes

Cook County's 200 series is residential. Class 299 is specifically
condominium; everything else in the series is houses and small apartment
buildings. Class 211 (two-to-six unit buildings, 6,498 sales) is included
deliberately — small multi-family is core to the investor audience.

## Distribution checks

Beyond medians, a 3,977-sale pull was audited for the failure modes a median
hides:

| Field | min | p1 | p50 | p99 | max |
|---|---|---|---|---|---|
| Building sq ft | 522 | 756 | 1,543 | 6,411 | 19,948 |
| Sale price | $20,000 | $60,000 | $415,000 | $2,650,000 | $5,761,500 |
| Price per sq ft | $8 | $42 | $272 | $901 | $2,198 |

No record had zero bedrooms, zero bathrooms, or a building under 300 sq ft.
All 3,977 coordinates fell inside the Cook County bounding box; none were
null-island or transposed. Nineteen PINs appeared twice, which is a parcel
genuinely selling twice inside the window rather than a duplication bug.

The tails are thin but real: two sales above $1,500 per square foot and a
minimum of $8. Those are plausible for a teardown or an unusual parcel, and at
roughly 0.05% of records they do not threaten the verdict, but the v1 comp
model should trim extreme price-per-square-foot outliers rather than let them
drag a weighted average.

## Philadelphia, Pennsylvania

Pennsylvania is a disclosure state. Philadelphia's Office of Property
Assessment publishes `opa_properties_public` through Carto: **one denormalized
table** carrying the last sale price and date alongside full characteristics, a
street address, and a geometry column. Nothing needs joining.

```
  Philadelphia, PA - trailing 18 months
  Arms-length sales in window          19,925
  Sampled and joined                    1,600  ( 88.9% of 1,800)
  Building size present                 99.9%
  Bedrooms present                      90.2%
  Bathrooms present                     88.6%
  Coordinates present                  100.0%
  Median sale price                  $260,000
  Median price per sq ft                 $210
  VERDICT                                  GO
```

19,925 sales clears the 2,000 threshold by 10x. Data runs to **2026-09-02**, a
lag of about two weeks — fresher than Cook County's two months.

### Why it was worth adding

Philadelphia differs from Cook County in nearly every mechanical respect, which
is what makes it a real test of the adapter boundary rather than a second copy
of the same work:

| | Cook County | Philadelphia |
|---|---|---|
| Transport | Socrata (SoQL) | Carto (PostgreSQL) |
| Shape | 4 datasets joined on PIN | 1 denormalized table |
| Parcel key | 14-digit PIN | OPA account number |
| Coordinates | `lat` / `lon` columns | PostGIS geometry column |
| Types | everything is a string | already typed |
| Arms-length screening | publisher-supplied flags | **none; built here** |
| Street address | absent | included |

Adding it required no change to the coverage scoring or the report. Both
adapters satisfy one `CountyAdapter` protocol and both emit the same `CompSale`,
so the validator drives either without knowing which it has.

### Philadelphia-specific findings

**No arms-length flags.** Cook County ships screening for bulk transfers,
nominal consideration, and unusual deed types. Philadelphia ships none. Roughly
**6,500 residential transfers** in an 18-month window record a price of $1 or
$0 — intra-family transfers, deed corrections, sheriff activity. The adapter
applies a $10,000 floor, deliberately matching the threshold Cook County's own
flag uses so the two counties stay comparable.

**Bathrooms are a decimal.** OPA writes 2.5 bathrooms as `2.5`; the adapter
splits that into two full and one half.

**Unknown build years are the string `'0000'`**, not null or empty.

**Livable area is unreliable for a minority of records.** This is the one place
Philadelphia is meaningfully worse than Cook County:

| Check | Share of records |
|---|---|
| Building under 400 sq ft | 0.4% |
| Price per sq ft over $1,000 | 1.8% |
| Price per sq ft under $30 | 2.5% |

A $780,000 sale recorded against 183 sq ft of livable area is a bad size field,
not a real transaction. Cook County's equivalent tail was roughly 0.05%;
Philadelphia's is about **4.3%**. Field *presence* is 100%, so the thresholds
pass, but presence is not accuracy.

**The v1 comp model must trim price-per-square-foot outliers before weighting,
and this is now a requirement rather than a nicety.** Untrimmed, a handful of
records with an $8,000 per square foot implied rate would distort any weighted
average built on Philadelphia data.

### Structural caveat

The OPA table holds each parcel's *most recent* sale, not a transaction
history. A property sold twice inside the window appears once, at the later
price. For comp selection that is the correct record anyway, but Philadelphia
cannot support repeat-sales analysis the way Cook County's full sales history
can.

## Limits and open questions

- **No rent data.** These are sale prices only. Rent estimation in phase 2
  needs a separate source, and it is not solved by this validation.
- **Condos need a different approach** if they are ever in scope — likely a
  price-per-unit model that does not depend on bathroom counts.
- **Two counties validated, both GO.** The adapter boundary is proven against
  genuinely different sources. A third would be additive rather than
  structural; King County, Washington (flat-file download, a third transport
  shape) is the natural next one.
- **No app token in use.** Unauthenticated Socrata requests get throttled,
  which is fine for validation but not for a scheduled ingestion job.
- **Assessor characteristics are not MLS data.** Condition, finish quality and
  renovations are largely absent, which puts a floor on achievable accuracy.
  This is an argument for shipping a confidence range rather than a point
  value, which the spec already requires.

## Reproducing this

```
python backend/validate_county_data.py --county all --property-type single_family
python backend/validate_county_data.py --county philadelphia
python backend/validate_county_data.py --county cook --township 72
```

Exit code 0 means every county checked is a GO, 1 otherwise, so this can run in
CI as a data-drift alarm. The thresholds live in
`backend/app/ingestion/coverage.py`.

## Deviation from the spec's repo layout

The spec lists `ingestion/assessor_puller.py`. Assessor schemas are entirely
county-specific, so each county gets its own module and the layout is:

| Module | Role |
|---|---|
| `records.py` | County-neutral types: `CompSale`, `Sale`, field parsers |
| `adapter.py` | The `CountyAdapter` protocol both counties satisfy |
| `socrata.py` | SoQL transport, with a fake |
| `carto.py` | Carto SQL transport, with a fake |
| `cook_county.py` | `CookCountyAdapter` |
| `philadelphia.py` | `PhiladelphiaAdapter` |
| `coverage.py` | Go/no-go scoring, county-neutral and pure |

Validating the second county is what forced this split. Before Philadelphia the
record types lived inside `cook_county.py` and were keyed on a field called
`pin`, which only exists in Illinois.
