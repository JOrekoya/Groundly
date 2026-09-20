"""Typed tool contracts, plus fakes for tests.

The spec's tool surface is allow-listed and typed: the executor may only call
these, and their signatures are the contract. Defining them as Protocols keeps
the layers above — the executor, and later the planner — testable with no
network and no database, exactly as the ingestion layer already is.

Anything a tool returns that originated as free text written by a stranger,
such as a listing description or MLS remarks, is data and never instruction.
No tool here returns such text today; the rule is stated so it survives the
first tool that does.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from app.ingestion.records import CompSale, PropertyType


@runtime_checkable
class CompStore(Protocol):
    """Where comparable sales come from.

    The comp model never fetches anything itself. It is handed a list of sales
    and weighs them, which is what lets it be tested against fixtures and
    backtested against history through the same code path.
    """

    def comps_near(
        self,
        latitude: float,
        longitude: float,
        *,
        radius_miles: float,
        since: date,
        property_type: PropertyType | None = None,
        limit: int = 500,
    ) -> list[CompSale]:
        """Sales within ``radius_miles``, no older than ``since``."""
        ...


class InMemoryCompStore:
    """A comp store over a list already in memory.

    This is what backs the API today. Sales are pulled once at startup and
    filtered in Python, which is honest at the current scale — a county's
    worth of recent single-family sales is tens of thousands of rows — and is
    the same interface a Postgres-backed store will satisfy later.
    """

    def __init__(self, comps: list[CompSale]) -> None:
        self.comps = comps

    def comps_near(
        self,
        latitude: float,
        longitude: float,
        *,
        radius_miles: float,
        since: date,
        property_type: PropertyType | None = None,
        limit: int = 500,
    ) -> list[CompSale]:
        # Imported here to keep this module free of model logic.
        from app.models.baseline_comp_model import haversine_miles

        found: list[tuple[float, CompSale]] = []
        for comp in self.comps:
            if comp.sale_date < since:
                continue
            if property_type and comp.property_type != property_type:
                continue
            distance = haversine_miles(
                latitude, longitude, comp.latitude, comp.longitude
            )
            if distance <= radius_miles:
                found.append((distance, comp))

        found.sort(key=lambda pair: pair[0])
        return [comp for _, comp in found[:limit]]


class EmptyCompStore:
    """A store with nothing in it.

    Not a test double — this is what the API uses before any county data has
    been loaded, so that the valuation endpoint answers "no comps" honestly
    rather than failing to start.
    """

    def comps_near(
        self,
        latitude: float,
        longitude: float,
        *,
        radius_miles: float,
        since: date,
        property_type: PropertyType | None = None,
        limit: int = 500,
    ) -> list[CompSale]:
        return []
