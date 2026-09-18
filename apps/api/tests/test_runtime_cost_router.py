"""Phase 17: RuntimeCostRouter — see app/services/runtime_cost_router.py."""

from app.services.runtime_cost_router import RoutingSignals, RuntimeCostRouter


def _router() -> RuntimeCostRouter:
    return RuntimeCostRouter()


class TestStopForHuman:
    def test_stops_for_human_after_two_failed_repair_attempts(self):
        signals = RoutingSignals(task_type="coding", previous_failed_repair_attempts=2)
        decision = _router().route(signals)
        assert decision.stop_for_human is True
        assert "2 failed repair attempts" in decision.stop_reason

    def test_does_not_stop_after_only_one_failed_repair_attempt(self):
        signals = RoutingSignals(task_type="document", previous_failed_repair_attempts=1)
        decision = _router().route(signals)
        assert decision.stop_for_human is False

    def test_stops_for_human_when_every_candidate_is_rejected(self):
        signals = RoutingSignals(task_type="coding", required_capabilities=["PATCH_GENERATION"], estimated_complexity="simple", runtime_health={"opencode": False})
        decision = _router().route(signals)
        assert decision.stop_for_human is True
        assert "No candidate runtime is currently viable" in decision.stop_reason


class TestDeterministicTasks:
    def test_deterministic_task_never_calls_an_llm(self):
        signals = RoutingSignals(task_type="document", estimated_complexity="deterministic")
        decision = _router().route(signals)
        assert decision.selected_runtime == "deterministic"
        assert decision.selected_model == "none"
        assert decision.estimated_cost_usd == 0.0
        assert decision.candidates == []


class TestDocumentRouting:
    def test_simple_document_routes_to_a_small_local_model_when_a_local_runtime_is_connected(self):
        signals = RoutingSignals(task_type="document", estimated_complexity="simple", user_connection_available=True)
        decision = _router().route(signals)
        assert decision.selected_runtime == "local-runtime"

    def test_normal_document_routes_to_a_standard_low_cost_model(self):
        signals = RoutingSignals(task_type="document", estimated_complexity="normal")
        decision = _router().route(signals)
        assert decision.selected_runtime == "document-runtime"


class TestCodingRouting:
    def test_simple_coding_routes_to_opencode_with_an_affordable_model(self):
        signals = RoutingSignals(task_type="coding", required_capabilities=["PATCH_GENERATION"], estimated_complexity="simple")
        decision = _router().route(signals)
        assert decision.selected_runtime == "opencode"
        assert decision.selected_model == "affordable-coding-model"

    def test_medium_coding_also_routes_to_opencode_affordable(self):
        signals = RoutingSignals(task_type="coding", required_capabilities=["PATCH_GENERATION"], estimated_complexity="normal")
        decision = _router().route(signals)
        assert decision.selected_runtime == "opencode"
        assert decision.selected_model == "affordable-coding-model"

    def test_complex_coding_uses_the_standard_model_first_when_security_does_not_require_stronger(self):
        signals = RoutingSignals(task_type="coding", required_capabilities=["PATCH_GENERATION"], estimated_complexity="complex", security_requires_strong_model=False)
        decision = _router().route(signals)
        assert decision.selected_model == "standard-coding-model"
        assert decision.escalation_reason is None

    def test_complex_coding_escalates_directly_to_premium_when_security_requires_it(self):
        signals = RoutingSignals(task_type="coding", required_capabilities=["PATCH_GENERATION"], estimated_complexity="complex", security_requires_strong_model=True)
        decision = _router().route(signals)
        assert decision.selected_model == "premium-coding-model"
        assert "security policy requires" in decision.escalation_reason

    def test_complex_coding_escalates_to_premium_when_standard_is_rejected(self):
        signals = RoutingSignals(
            task_type="coding", required_capabilities=["PATCH_GENERATION"], estimated_complexity="complex",
            security_requires_strong_model=False, runtime_health={"opencode": False},
        )
        decision = _router().route(signals)
        assert decision.selected_runtime == "codex"
        assert "standard model candidate was rejected" in decision.escalation_reason


class TestRejectionReasons:
    def test_records_a_rejected_reason_for_an_unhealthy_candidate(self):
        signals = RoutingSignals(task_type="coding", required_capabilities=["PATCH_GENERATION"], estimated_complexity="complex", runtime_health={"opencode": False, "codex": True})
        decision = _router().route(signals)
        rejected = [c for c in decision.candidates if c.rejected_reason]
        assert any("not currently healthy" in c.rejected_reason for c in rejected)

    def test_rejects_a_candidate_that_exceeds_remaining_project_budget(self):
        signals = RoutingSignals(task_type="coding", required_capabilities=["PATCH_GENERATION"], estimated_complexity="simple", project_budget_remaining_usd=0.05)
        decision = _router().route(signals)
        assert decision.stop_for_human is True
        assert any("exceeds remaining project budget" in c.rejected_reason for c in decision.candidates)

    def test_rejects_a_local_runtime_candidate_with_no_connected_user(self):
        signals = RoutingSignals(task_type="document", estimated_complexity="simple", user_connection_available=False)
        decision = _router().route(signals)
        assert decision.candidates[0].rejected_reason == "No connected local runtime is available."

    def test_accepts_local_runtime_once_a_user_is_connected(self):
        signals = RoutingSignals(task_type="document", estimated_complexity="simple", user_connection_available=True)
        decision = _router().route(signals)
        assert decision.selected_runtime == "local-runtime"

    def test_rejects_a_candidate_with_a_poor_historical_success_rate(self):
        signals = RoutingSignals(
            task_type="coding", required_capabilities=["PATCH_GENERATION"], estimated_complexity="simple",
            runtime_success_history={"opencode": 0.2},
        )
        decision = _router().route(signals)
        assert decision.stop_for_human is True
        assert "historical success rate" in decision.candidates[0].rejected_reason


class TestRecordedFields:
    def test_records_requested_runtime_and_model_regardless_of_what_was_selected(self):
        signals = RoutingSignals(task_type="document", estimated_complexity="normal")
        decision = _router().route(signals, requested_runtime="auto", requested_model="auto")
        assert decision.requested_runtime == "auto"
        assert decision.requested_model == "auto"

    def test_records_cost_owner_from_signals(self):
        signals = RoutingSignals(task_type="document", estimated_complexity="normal", cost_owner="user")
        decision = _router().route(signals)
        assert decision.cost_owner == "user"

    def test_actual_cost_is_none_until_a_caller_fills_it_in_after_execution(self):
        signals = RoutingSignals(task_type="document", estimated_complexity="normal")
        decision = _router().route(signals)
        assert decision.actual_cost_usd is None
