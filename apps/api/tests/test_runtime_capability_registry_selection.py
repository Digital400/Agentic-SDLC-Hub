"""Phase 02: Runtime Capability Registry — RuntimePolicyEvaluator /
RuntimeSelectionRequest/Decision tests. See app/coding_runtime/selection.py.
"""

import uuid
from datetime import datetime, timezone

from app.agent_runtime import WorkObjective, WorkPacket, WorkPacketTaskType
from app.agent_runtime.capability import RuntimeCapability, RuntimeCapabilityManifest
from app.coding_runtime.enums import ExecutionLocation, RuntimeKind
from app.coding_runtime.manifest import RuntimeDefinition
from app.coding_runtime.selection import RuntimePolicyEvaluator, RuntimeSelectionRequest


def _work_packet(task_type=WorkPacketTaskType.IMPLEMENT_STORY) -> WorkPacket:
    return WorkPacket(
        packet_id=uuid.uuid4(), task_type=task_type, project_id=uuid.uuid4(),
        objective=WorkObjective(goal="Implement the endpoint.", success_definition="Tests pass."),
        created_at=datetime.now(timezone.utc),
    )


def _runtime(
    name, *, kind=RuntimeKind.CODING, capabilities=None, execution_location=ExecutionLocation.COMPANY_SANDBOX,
) -> RuntimeDefinition:
    return RuntimeDefinition(
        name=name, kind=kind, execution_location=execution_location,
        capability=RuntimeCapabilityManifest(
            runtime_name=name, max_context_tokens=10000, max_output_tokens=4000,
            capabilities=capabilities or [],
        ),
    )


def _request(**overrides) -> RuntimeSelectionRequest:
    defaults = dict(
        packet=_work_packet(), task_type=WorkPacketTaskType.IMPLEMENT_STORY,
        required_capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION],
    )
    defaults.update(overrides)
    return RuntimeSelectionRequest(**defaults)


def test_selects_the_only_candidate_that_has_every_required_capability():
    evaluator = RuntimePolicyEvaluator()
    weak = _runtime("weak", capabilities=[RuntimeCapability.REPOSITORY_READ])
    strong = _runtime("strong", capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION])

    decision = evaluator.evaluate(_request(), [weak, strong])

    assert decision.selected_runtime == "strong"
    assert "weak" in decision.rejected
    assert "Missing required capabilities" in decision.rejected["weak"]


def test_rejects_a_candidate_of_the_wrong_kind():
    evaluator = RuntimePolicyEvaluator()
    document_runtime = _runtime("doc", kind=RuntimeKind.DOCUMENT, capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION])

    decision = evaluator.evaluate(_request(), [document_runtime])

    assert decision.selected_runtime is None
    assert "kind" in decision.rejected["doc"].lower()


def test_user_preference_is_selected_when_it_satisfies_every_requirement():
    evaluator = RuntimePolicyEvaluator()
    a = _runtime("a", capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION])
    b = _runtime("b", capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION])

    decision = evaluator.evaluate(_request(user_preference="b"), [a, b])

    assert decision.selected_runtime == "b"
    assert decision.fallback_reason is None


def test_falls_back_with_an_explicit_reason_when_preference_is_unavailable():
    evaluator = RuntimePolicyEvaluator()
    only_candidate = _runtime("only-one", capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION])
    unavailable = _runtime("wanted", capabilities=[RuntimeCapability.REPOSITORY_READ])  # missing PATCH_GENERATION

    decision = evaluator.evaluate(_request(user_preference="wanted"), [unavailable, only_candidate])

    assert decision.requested_runtime == "wanted"
    assert decision.selected_runtime == "only-one"
    assert decision.fallback_reason is not None
    assert "Missing required capabilities" in decision.fallback_reason


def test_company_allowlist_rejects_a_capable_but_unlisted_runtime():
    evaluator = RuntimePolicyEvaluator()
    capable = _runtime("capable", capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION])

    decision = evaluator.evaluate(_request(company_allowlist=["some-other-runtime"]), [capable])

    assert decision.selected_runtime is None
    assert "allowlist" in decision.rejected["capable"].lower()


def test_project_execution_location_restriction_is_enforced():
    evaluator = RuntimePolicyEvaluator()
    external = _runtime(
        "external", execution_location=ExecutionLocation.EXTERNAL_MANAGED,
        capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION],
    )

    decision = evaluator.evaluate(
        _request(project_allowed_execution_locations=[ExecutionLocation.COMPANY_SANDBOX]), [external],
    )

    assert decision.selected_runtime is None
    assert "execution location" in decision.rejected["external"].lower()


def test_no_survivors_reports_every_rejection_reason():
    evaluator = RuntimePolicyEvaluator()
    a = _runtime("a", capabilities=[])
    b = _runtime("b", kind=RuntimeKind.DOCUMENT, capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION])

    decision = evaluator.evaluate(_request(), [a, b])

    assert decision.selected_runtime is None
    assert set(decision.rejected) == {"a", "b"}
