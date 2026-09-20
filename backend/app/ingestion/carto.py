"""Minimal Carto SQL client, behind a Protocol so adapters stay testable.

Philadelphia serves its property data through Carto, which accepts real
PostgreSQL rather than Socrata's SoQL. That means PostGIS functions are
available — coordinates come out of a geometry column via ``ST_Y``/``ST_X``
rather than sitting in their own fields — and it means values arrive already
typed instead of as strings.

As with :mod:`app.ingestion.socrata`, the network lives here and nowhere else.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Protocol


def quote_literal(value: str) -> str:
    """Escape a string for inclusion in a SQL literal.

    Every value this module interpolates is a constant chosen in code rather
    than user input, but escaping is cheap and the day someone passes a parcel
    number straight through should not be the day it matters.
    """
    return value.replace("'", "''")


class CartoClient(Protocol):
    """What a Carto-backed county adapter needs from the outside world."""

    def query(self, sql: str) -> list[dict[str, Any]]:
        """Run ``sql`` and return the rows."""
        ...


class HttpCartoClient:
    """Live client against a Carto SQL endpoint."""

    def __init__(self, domain: str, *, timeout: float = 90.0) -> None:
        self.domain = domain
        self.timeout = timeout

    def query(self, sql: str) -> list[dict[str, Any]]:
        url = (
            f"https://{self.domain}/api/v2/sql?"
            + urllib.parse.urlencode({"q": sql})
        )
        with urllib.request.urlopen(url, timeout=self.timeout) as response:
            payload = json.loads(response.read())
        if "error" in payload:
            raise ValueError(f"Carto rejected the query: {payload['error']}")
        return payload.get("rows", [])


class FakeCartoClient:
    """In-memory stand-in for tests.

    Returns prepared rows and records the SQL it was asked for, so a test can
    assert on the filter an adapter built with no network call. Rows can be a
    flat list, or a callable that inspects the SQL and decides what to return.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        responder: Any = None,
    ) -> None:
        self.rows = rows or []
        self.responder = responder
        self.queries: list[str] = []

    def query(self, sql: str) -> list[dict[str, Any]]:
        self.queries.append(sql)
        if self.responder is not None:
            return self.responder(sql)
        return list(self.rows)
