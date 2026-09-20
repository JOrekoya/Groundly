"""The contract every county adapter satisfies.

Counties differ in almost every respect that is not this interface: Cook County
serves SoQL over four Socrata datasets keyed by a 14-digit PIN, Philadelphia
serves real SQL over one denormalized Carto table keyed by an OPA account
number. Anything county-specific — an assessment year to join against, a
township to narrow to — belongs in an adapter's constructor, not in this
protocol, so the coverage report and the CLI stay county-neutral.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

from app.ingestion.records import CompSale, PropertyType


@runtime_checkable
class CountyAdapter(Protocol):
    """What the validator and, later, the ingestion scheduler need."""

    @property
    def name(self) -> str:
        """Human-readable county name, used in reports."""
        ...

    def count_sales(
        self, since: date, *, property_type: PropertyType | None = None
    ) -> int:
        """Arms-length sales in the window, before any join.

        Counted separately from the pull so a join rate is always measured
        against the population it was sampled from.
        """
        ...

    def fetch_comp_sales(
        self,
        since: date,
        *,
        limit: int,
        property_type: PropertyType | None = None,
        stratify: bool = True,
    ) -> list[CompSale]:
        """Pull sales and return them joined to characteristics and location."""
        ...
