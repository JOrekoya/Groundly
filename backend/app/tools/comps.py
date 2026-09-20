"""The comp tool: find comparable sales and value a property from them.

Sits between the HTTP layer and the model. The model stays a pure function of
the sales it is handed; this is the piece that knows where to get them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.ingestion.records import PropertyType
from app.models.baseline_comp_model import (
    MAX_AGE_MONTHS,
    MAX_DISTANCE_MILES,
    InsufficientComps,
    SubjectProperty,
    ValuationEstimate,
    estimate_value,
)
from app.tools.contract import CompStore

#: How many sales to hand the model. It applies its own, stricter radius and
#: keeps only the heaviest handful, so this is a ceiling rather than a target.
CANDIDATE_LIMIT = 500


@dataclass(frozen=True)
class CompStoreStatus:
    """What the loaded comp data covers, so a caller can say so plainly."""

    loaded: bool
    count: int
    counties: tuple[str, ...] = ()
    earliest_sale: date | None = None
    latest_sale: date | None = None


def value_property(
    store: CompStore,
    subject: SubjectProperty,
    *,
    as_of: date | None = None,
    radius_miles: float = MAX_DISTANCE_MILES,
    max_age_months: float = MAX_AGE_MONTHS,
    property_type: PropertyType | None = "single_family",
) -> ValuationEstimate | InsufficientComps:
    """Value a property from whatever comparable sales the store can supply.

    The store is asked for a wider net than the model will use — its own
    distance and recency weighting does the narrowing — so that the model sees
    enough candidates to have something to reject.
    """
    as_of = as_of or date.today()
    since = as_of - timedelta(days=round(max_age_months * 30.44))

    comps = store.comps_near(
        subject.latitude,
        subject.longitude,
        radius_miles=radius_miles,
        since=since,
        property_type=property_type,
        limit=CANDIDATE_LIMIT,
    )
    return estimate_value(
        subject, comps, as_of=as_of, max_distance_miles=radius_miles
    )
