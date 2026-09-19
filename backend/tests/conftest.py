"""Shared test fixtures and the golden-scope loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.state import DealScope, OperatingExpenses, Strategy

GOLDEN_DIR = Path(__file__).parent / "golden_scopes"


def scope_from_dict(raw: dict[str, Any]) -> DealScope:
    """Build a ``DealScope`` from a plain fixture dict.

    Kept in the test layer on purpose. The API will parse request bodies with
    pydantic; this exists only so fixtures can be committed as readable JSON
    that a human can check against a mortgage calculator by hand.
    """
    data = dict(raw)
    expenses = data.pop("expenses", {})
    strategy = data.pop("strategy", None)
    if strategy is not None:
        data["strategy"] = Strategy(strategy)
    return DealScope(expenses=OperatingExpenses(**expenses), **data)


def load_golden_scopes() -> list[dict[str, Any]]:
    """Every committed golden scope, sorted by filename for stable test ids."""
    files = sorted(GOLDEN_DIR.glob("*.json"))
    if not files:
        raise AssertionError(f"no golden scopes found in {GOLDEN_DIR}")
    return [json.loads(path.read_text(encoding="utf-8")) for path in files]


@pytest.fixture
def baseline_scope() -> DealScope:
    """A plain 20%-down rental, used as the starting point for what-if tests."""
    return DealScope(
        address="1420 Elmwood Ave, Springfield",
        purchase_price=300_000,
        down_payment_rate=0.20,
        interest_rate=0.07,
        term_years=30,
        closing_cost_rate=0.02,
        monthly_rent=2800,
        vacancy_rate=0.05,
        expenses=OperatingExpenses(
            property_taxes_annual=3600,
            insurance_annual=1200,
        ),
    )
