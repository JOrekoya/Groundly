"""The eval harness: every committed golden scope, checked field by field.

Each fixture in ``golden_scopes/`` pairs a deal with the metrics it must
produce. Expected values were derived independently of the engine, so a
regression in either one shows up as a diff here rather than passing silently.

Adding coverage means dropping in one more JSON file. No test code changes.
"""

from __future__ import annotations

import pytest

from app.finance.engine import calculate_deal_metrics
from conftest import load_golden_scopes, scope_from_dict

GOLDEN_SCOPES = load_golden_scopes()

#: Money is compared to the tenth of a cent; ratios to eight decimals.
MONEY_TOLERANCE = 1e-4
RATIO_TOLERANCE = 1e-8

RATIO_FIELDS = {
    "cap_rate",
    "cash_on_cash_return",
    "debt_service_coverage_ratio",
}


def _ids(scopes):
    return [scope["name"] for scope in scopes]


@pytest.mark.parametrize("golden", GOLDEN_SCOPES, ids=_ids(GOLDEN_SCOPES))
def test_golden_scope_metrics(golden):
    metrics = calculate_deal_metrics(scope_from_dict(golden["scope"]))

    mismatches = []
    for field, expected in golden["expected"].items():
        actual = getattr(metrics, field)

        if expected is None or isinstance(expected, bool):
            if actual != expected:
                mismatches.append(f"  {field}: expected {expected!r}, got {actual!r}")
            continue

        tolerance = RATIO_TOLERANCE if field in RATIO_FIELDS else MONEY_TOLERANCE
        if actual is None or abs(actual - expected) > tolerance:
            mismatches.append(f"  {field}: expected {expected!r}, got {actual!r}")

    assert not mismatches, "\n".join(
        [f"{golden['name']} drifted from its golden values:", *mismatches]
    )


@pytest.mark.parametrize("golden", GOLDEN_SCOPES, ids=_ids(GOLDEN_SCOPES))
def test_golden_scope_lines_reconcile(golden):
    """Beyond matching stored numbers, each fixture must be internally consistent."""
    m = calculate_deal_metrics(scope_from_dict(golden["scope"]))

    assert m.net_operating_income == pytest.approx(
        m.effective_gross_income - m.operating_expenses
    )
    assert m.annual_cash_flow == pytest.approx(
        m.net_operating_income - m.capex_reserve - m.annual_debt_service
    )
    assert m.cap_rate == pytest.approx(
        m.net_operating_income / golden["scope"]["purchase_price"]
    )


def test_every_fixture_is_documented():
    """A fixture nobody can explain is a fixture nobody will maintain."""
    for golden in GOLDEN_SCOPES:
        assert golden.get("description", "").strip(), (
            f"{golden['name']} needs a description saying what it guards"
        )


def test_harness_covers_the_paths_that_matter():
    """Guards the harness itself: these branches must stay represented."""
    metrics = {
        g["name"]: calculate_deal_metrics(scope_from_dict(g["scope"]))
        for g in GOLDEN_SCOPES
    }
    assert any(m.debt_service_coverage_ratio is None for m in metrics.values()), (
        "need an all-cash fixture"
    )
    assert any(m.annual_cash_flow < 0 for m in metrics.values()), (
        "need a negative-cash-flow fixture"
    )
    assert any(m.passes_seventy_percent_rule is True for m in metrics.values())
    assert any(m.passes_seventy_percent_rule is False for m in metrics.values())
