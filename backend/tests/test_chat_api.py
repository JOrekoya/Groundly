"""Chat endpoint tests. In-process, no API key, no network.

With no credentials configured the service must still answer everything the
router understands — which is most of what people type at a deal tool. These
tests run in exactly that state, which is also the state a fresh checkout is in.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app, load_comps

SCOPE = {
    "purchase_price": 300_000,
    "monthly_rent": 2800,
    "down_payment_rate": 0.20,
    "interest_rate": 0.07,
    "closing_cost_rate": 0.02,
    "expenses": {"property_taxes_annual": 3600, "insurance_annual": 1200},
}


@pytest.fixture
def client():
    load_comps([])
    return TestClient(app)


def say(client, message: str, **extra):
    response = client.post(
        "/api/chat", json={"message": message, "scope": SCOPE, **extra}
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestChatEndpoint:
    def test_answers_a_metric_question_with_the_meaning(self, client):
        """A metric question gets what it means, then the figure, then a verdict."""
        body = say(client, "what is the cap rate")
        assert body["reply"].startswith("Cap rate is")
        assert "7.66%" in body["reply"]
        assert "solid" in body["reply"]
        assert body["used_llm"] is False

    def test_reports_that_no_model_was_used(self, client):
        """The project's central claim, made checkable per response."""
        body = say(client, "what is the cap rate")
        assert body["used_llm_for_planning"] is False
        assert body["used_llm_for_narration"] is False

    def test_a_change_comes_back_in_the_scope(self, client):
        body = say(client, "change the rate to 9%")
        assert body["scope"]["interest_rate"] == pytest.approx(0.09)
        assert body["metrics"]["monthly_payment"] > 1596.73

    def test_metrics_match_the_engine_after_a_change(self, client):
        body = say(client, "set the down payment to 25%")
        metrics = body["metrics"]
        assert metrics["total_cash_invested"] == pytest.approx(81_000)
        assert metrics["cap_rate"] == pytest.approx(0.076568, abs=1e-5)

    def test_names_the_steps_it_ran(self, client):
        body = say(client, "change rate to 8%")
        assert body["steps"] == ["set interest rate"]

    def test_summary_works(self, client):
        assert "cap rate" in say(client, "give me a summary")["reply"]

    def test_a_question_about_the_deal_is_answered_without_a_model(self, client):
        """Explanations come from rules and the engine, not a model."""
        body = say(client, "why does this feel like a bad deal")
        assert body["used_llm"] is False
        assert body["tool_calls"] == ["explain"]
        assert "deal" in body["reply"].lower()
        assert "cap rate" in body["reply"].lower()

    def test_a_beginner_walkthrough_uses_the_real_numbers(self, client):
        body = say(client, "explain what all these numbers mean")
        assert "$300,000" in body["reply"]
        assert "$1,597" in body["reply"]
        assert "cap rate" in body["reply"].lower()
        assert "DSCR" in body["reply"]

    def test_a_truly_unmatched_message_says_what_it_can_do(self, client):
        body = say(client, "tell me about the weather in Chicago")
        assert body["used_llm"] is False
        assert "did not follow" in body["reply"]
        assert "help" in body["reply"]

    def test_reports_whether_a_model_is_configured_at_all(self, client):
        body = say(client, "what is the cap rate")
        assert isinstance(body["llm_available"], bool)

    def test_health_reports_llm_configuration(self, client):
        body = client.get("/api/health").json()
        assert body["llm_in_request_path"] is False
        assert "llm_configured" in body


class TestValuationThroughChat:
    def test_coordinates_in_a_message_get_valued(self, client):
        from datetime import date, timedelta

        from app.ingestion.records import CompSale

        load_comps(
            [
                CompSale(
                    parcel_id=f"p{i}",
                    county="Cook County, IL",
                    sale_date=date.today() - timedelta(days=60),
                    sale_price=400_000,
                    property_type="single_family",
                    latitude=41.90 + i * 0.0005,
                    longitude=-87.60,
                    beds=3,
                    full_baths=2,
                    building_sqft=2000.0,
                    address=f"{i} Test St",
                )
                for i in range(12)
            ]
        )
        try:
            body = say(client, "value 41.90, -87.60")
            assert "Comparable sales put this between" in body["reply"]
            assert body["latitude"] == pytest.approx(41.90)
        finally:
            load_comps([])

    def test_valuation_without_comps_is_explained(self, client):
        body = say(client, "value 41.90, -87.60")
        assert "cannot value" in body["reply"]


class TestValidation:
    def test_rejects_an_empty_message(self, client):
        response = client.post("/api/chat", json={"message": "", "scope": SCOPE})
        assert response.status_code == 422

    def test_rejects_an_absurdly_long_message(self, client):
        response = client.post(
            "/api/chat", json={"message": "x" * 5000, "scope": SCOPE}
        )
        assert response.status_code == 422

    def test_rejects_a_bad_scope(self, client):
        response = client.post(
            "/api/chat",
            json={"message": "hi", "scope": {**SCOPE, "interest_rate": 7}},
        )
        assert response.status_code == 422

    def test_rejects_an_unknown_field(self, client):
        response = client.post(
            "/api/chat",
            json={"message": "hi", "scope": SCOPE, "nonsense": True},
        )
        assert response.status_code == 422


class TestStatelessness:
    def test_the_server_holds_no_conversation_state(self, client):
        """The scope travels with each message, so two clients cannot see each
        other's deals and the sliders cannot drift from the chat."""
        first = say(client, "change rate to 9%")
        assert first["scope"]["interest_rate"] == pytest.approx(0.09)

        # A second request with the original scope is unaffected by the first.
        second = say(client, "what is the monthly payment")
        assert second["metrics"]["monthly_payment"] == pytest.approx(1596.73, abs=0.01)

    def test_identical_requests_give_identical_answers(self, client):
        assert say(client, "what is the cap rate") == say(client, "what is the cap rate")
