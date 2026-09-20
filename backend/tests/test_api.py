"""API tests. In-process via TestClient, so no server and no network.

The API's whole job is to be a faithful, validating wrapper over the finance
engine. These tests check exactly that: that it does not change the numbers,
that it rejects bad input as client error rather than crashing, and that the
slider path behaves.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.finance.engine import calculate_deal_metrics
from app.main import app
from app.state import DealScope, OperatingExpenses

BASELINE = {
    "address": "1420 Elmwood Ave, Springfield",
    "purchase_price": 300_000,
    "down_payment_rate": 0.20,
    "interest_rate": 0.07,
    "term_years": 30,
    "closing_cost_rate": 0.02,
    "monthly_rent": 2800,
    "vacancy_rate": 0.05,
    "expenses": {"property_taxes_annual": 3600, "insurance_annual": 1200},
}


@pytest.fixture
def client():
    return TestClient(app)


def analyze(client, **overrides):
    response = client.post("/api/analyze", json={**BASELINE, **overrides})
    assert response.status_code == 200, response.text
    return response.json()


class TestHealth:
    def test_reports_ok(self, client):
        assert client.get("/api/health").json()["status"] == "ok"

    def test_states_there_is_no_model_in_the_path(self, client):
        """A claim worth asserting, since it is the project's core promise."""
        assert client.get("/api/health").json()["llm_in_request_path"] is False


class TestAnalyze:
    def test_matches_the_engine_exactly(self, client):
        """The API must not become a second implementation of the math."""
        scope = DealScope(
            purchase_price=300_000,
            down_payment_rate=0.20,
            interest_rate=0.07,
            term_years=30,
            closing_cost_rate=0.02,
            monthly_rent=2800,
            vacancy_rate=0.05,
            expenses=OperatingExpenses(
                property_taxes_annual=3600, insurance_annual=1200
            ),
        )
        expected = calculate_deal_metrics(scope)
        actual = analyze(client)["metrics"]

        for name, value in expected.__dict__.items():
            if isinstance(value, float):
                assert actual[name] == pytest.approx(value), name
            else:
                assert actual[name] == value, name

    def test_returns_the_published_payment(self, client):
        # $240k at 7% over 30 years. The same anchor the engine tests use.
        assert analyze(client)["metrics"]["monthly_payment"] == pytest.approx(
            1596.73, abs=0.01
        )

    def test_echoes_the_scope_back(self, client):
        """The client renders what the server computed, not what it thinks it
        sent, so defaults the server filled in are visible."""
        body = analyze(client)
        assert body["scope"]["purchase_price"] == 300_000
        assert body["scope"]["strategy"] == "rental"
        assert body["scope"]["expenses"]["maintenance_rate"] == 0.05

    def test_includes_the_parts_of_cash_to_close(self, client):
        metrics = analyze(client)["metrics"]
        assert metrics["down_payment"] == pytest.approx(60_000)
        assert metrics["closing_costs"] == pytest.approx(6_000)
        assert metrics["total_cash_invested"] == pytest.approx(66_000)

    def test_undefined_ratios_are_null_not_zero(self, client):
        """All cash: no debt to cover, so DSCR is not applicable."""
        metrics = analyze(client, down_payment_rate=1.0)["metrics"]
        assert metrics["debt_service_coverage_ratio"] is None
        assert metrics["loan_amount"] == 0

    def test_seventy_percent_rule_when_an_arv_is_supplied(self, client):
        metrics = analyze(
            client, purchase_price=165_000, after_repair_value=300_000,
            rehab_budget=45_000,
        )["metrics"]
        assert metrics["max_allowable_offer"] == pytest.approx(165_000)
        assert metrics["passes_seventy_percent_rule"] is True

    def test_flip_rule_absent_without_an_arv(self, client):
        assert analyze(client)["metrics"]["passes_seventy_percent_rule"] is None

    def test_reports_compute_time(self, client):
        response = client.post("/api/analyze", json=BASELINE)
        assert float(response.headers["X-Compute-Ms"]) >= 0


class TestSliderPath:
    """What the dashboard actually does: resend the whole scope on each change."""

    def test_changing_down_payment_moves_the_right_numbers(self, client):
        base = analyze(client)["metrics"]
        more = analyze(client, down_payment_rate=0.25)["metrics"]

        assert more["annual_cash_flow"] > base["annual_cash_flow"]
        assert more["total_cash_invested"] > base["total_cash_invested"]
        assert more["cap_rate"] == pytest.approx(base["cap_rate"])

    def test_cap_rate_never_moves_with_financing(self, client):
        base = analyze(client)["metrics"]
        cheap = analyze(client, interest_rate=0.03, down_payment_rate=0.5)["metrics"]
        assert cheap["cap_rate"] == pytest.approx(base["cap_rate"])

    def test_repeated_identical_requests_are_identical(self, client):
        """No hidden state between requests."""
        assert analyze(client) == analyze(client)


class TestValidation:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("purchase_price", 0),
            ("purchase_price", -1),
            ("down_payment_rate", 1.5),
            ("down_payment_rate", -0.1),
            ("vacancy_rate", 1.4),
            ("term_years", 0),
            ("monthly_rent", -100),
            ("rehab_budget", -1),
            # 7 rather than 0.07 is a unit slip, not a 700% loan.
            ("interest_rate", 7),
        ],
    )
    def test_rejects_bad_fields_as_client_error(self, client, field, value):
        response = client.post("/api/analyze", json={**BASELINE, field: value})
        assert response.status_code == 422

    def test_rejects_an_unknown_field(self, client):
        """A typo in a field name must fail loudly rather than be ignored."""
        response = client.post("/api/analyze", json={**BASELINE, "down_payment": 0.2})
        assert response.status_code == 422

    def test_rejects_expense_rates_that_exceed_all_income(self, client):
        """A domain rule pydantic cannot express: each rate is individually
        valid, but together they consume more than the rent."""
        response = client.post(
            "/api/analyze",
            json={
                **BASELINE,
                "expenses": {
                    "maintenance_rate": 0.5,
                    "management_rate": 0.4,
                    "capex_reserve_rate": 0.2,
                },
            },
        )
        assert response.status_code == 422
        assert "below 1.0" in response.json()["detail"]

    def test_price_is_required(self, client):
        response = client.post("/api/analyze", json={"monthly_rent": 2000})
        assert response.status_code == 422


class TestContract:
    def test_openapi_schema_is_served(self, client):
        """The dashboard's client is written against this."""
        schema = client.get("/openapi.json").json()
        assert "/api/analyze" in schema["paths"]

    def test_cors_allows_the_dev_server(self, client):
        response = client.post(
            "/api/analyze",
            json=BASELINE,
            headers={"Origin": "http://localhost:5173"},
        )
        assert (
            response.headers["access-control-allow-origin"]
            == "http://localhost:5173"
        )
