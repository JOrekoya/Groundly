# County data validation — step 2 findings

**Verdict: GO for Cook County, Illinois, scoped to single-family and small
multi-family. Condominiums are out of scope for v1.**

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
  Sampled and joined                    1,493  ( 99.5% of 1,500)
  Building size present                100.0%
  Bedrooms present                     100.0%
  Bathrooms present                    100.0%
  Coordinates present                  100.0%
  Median sale price                  $419,900
  Median price per sq ft                 $274
  VERDICT                                  GO
```

A $419,900 median against a Chicago-area market is plausible, and $274 per
square foot is in the right band. Both are sanity checks on the arms-length
filter: a broken filter shows up as a median in the low tens of thousands.

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

## Class codes

Cook County's 200 series is residential. Class 299 is specifically
condominium; everything else in the series is houses and small apartment
buildings. Class 211 (two-to-six unit buildings, 6,498 sales) is included
deliberately — small multi-family is core to the investor audience.

## Limits and open questions

- **No rent data.** These are sale prices only. Rent estimation in phase 2
  needs a separate source, and it is not solved by this validation.
- **Condos need a different approach** if they are ever in scope — likely a
  price-per-unit model that does not depend on bathroom counts.
- **No second county yet.** The spec called for one or two. Cook alone clears
  the bar for v1, but a second (Philadelphia and King County, Washington are
  the strongest candidates) would prove the adapter boundary is real and not
  shaped around one county's quirks.
- **No app token in use.** Unauthenticated Socrata requests get throttled,
  which is fine for validation but not for a scheduled ingestion job.
- **Assessor characteristics are not MLS data.** Condition, finish quality and
  renovations are largely absent, which puts a floor on achievable accuracy.
  This is an argument for shipping a confidence range rather than a point
  value, which the spec already requires.

## Reproducing this

```
python backend/validate_county_data.py --property-type single_family
```

Exit code 0 means GO, 1 means NO-GO, so this can run in CI as a data-drift
alarm. The thresholds live in `backend/app/ingestion/coverage.py`.

## Deviation from the spec's repo layout

The spec lists `ingestion/assessor_puller.py`. The adapter is named
`ingestion/cook_county.py` instead, because assessor schemas are entirely
county-specific and a second county means a second module, not a branch inside
a shared one. `ingestion/socrata.py` holds the transport the adapters share.
