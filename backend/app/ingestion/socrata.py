"""Minimal Socrata client, behind a Protocol so everything above it is testable.

The network lives here and nowhere else. County adapters take a ``SocrataClient``
and never import ``urllib`` themselves, which is what lets the whole ingestion
layer be exercised offline against :class:`FakeSocrataClient`.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Protocol

#: SoQL parameters take a ``$`` prefix; plain filters do not.
_SOQL_PARAMS = frozenset(
    {"select", "where", "group", "order", "limit", "offset", "q"}
)

#: Socrata caps a single page at 50,000 rows.
MAX_PAGE_SIZE = 50_000


def build_query(params: dict[str, Any]) -> str:
    """Encode query parameters, prefixing SoQL clauses with ``$``."""
    encoded = {
        (f"${key}" if key in _SOQL_PARAMS else key): value
        for key, value in params.items()
    }
    return urllib.parse.urlencode(encoded)


class SocrataClient(Protocol):
    """What a county adapter needs from the outside world."""

    def query(self, dataset: str, **params: Any) -> list[dict[str, Any]]:
        """Return rows from ``dataset`` matching ``params``."""
        ...


class HttpSocrataClient:
    """Live client against a Socrata domain.

    An app token is optional. Without one the host throttles aggressively, which
    is fine for a validation spike but not for a scheduled ingestion job.
    """

    def __init__(
        self,
        domain: str,
        *,
        app_token: str | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.domain = domain
        self.app_token = app_token
        self.timeout = timeout

    def query(self, dataset: str, **params: Any) -> list[dict[str, Any]]:
        url = f"https://{self.domain}/resource/{dataset}.json?{build_query(params)}"
        request = urllib.request.Request(url)
        if self.app_token:
            request.add_header("X-App-Token", self.app_token)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read())

    def query_all(
        self, dataset: str, *, page_size: int = MAX_PAGE_SIZE, **params: Any
    ) -> list[dict[str, Any]]:
        """Page through every matching row.

        Socrata needs a deterministic sort for stable paging, so callers should
        pass an ``order`` that is unique enough to avoid rows shifting between
        pages.
        """
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.query(dataset, limit=page_size, offset=offset, **params)
            rows.extend(page)
            if len(page) < page_size:
                return rows
            offset += page_size


class FakeSocrataClient:
    """In-memory stand-in for tests.

    Holds prepared rows per dataset and records the queries it was asked for,
    so a test can assert on the filter an adapter built without a network call.
    """

    def __init__(self, rows_by_dataset: dict[str, list[dict[str, Any]]]) -> None:
        self.rows_by_dataset = rows_by_dataset
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def query(self, dataset: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((dataset, params))
        rows = self.rows_by_dataset.get(dataset, [])
        limit = params.get("limit")
        return rows[:limit] if limit else list(rows)
