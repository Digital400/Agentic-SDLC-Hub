"""One-off generator for this directory's pinned example payloads — run
manually when a NEW schema_version is introduced (never to rewrite an
existing version's fixture; those must stay byte-for-byte pinned, see
test_agent_runtime_schema_versioning.py). Not collected by pytest (no
test_ prefix), and not imported by any non-test code.

    cd apps/api && .venv/Scripts/python.exe -m tests.fixtures.agent_runtime._generate_fixtures
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.agent_runtime import (
    AcceptanceCriterion,
    ApprovalPolicy,
    ArtifactReference,
    BudgetPolicy,
    ClarificationRequest,
    CommandEvidence,
    ExecutionResult,
    ExecutionState,
    FileChangeEvidence,
    KnowledgeReference,
    RepositoryReference,
    RequiredCheck,
    RuntimeInstructionPackage,
    ScopePolicy,
    TestEvidence,
    TestResult,
    ToolPolicy,
    UsageEvidence,
    WorkObjective,
    WorkPacket,
    WorkPacketTaskType,
)

OUT_DIR = Path(__file__).parent
FIXED_TIME = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
FIXED_PACKET_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
FIXED_PROJECT_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
FIXED_STORY_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
FIXED_USER_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")
FIXED_ARTIFACT_ID = uuid.UUID("55555555-5555-4555-8555-555555555555")
FIXED_RESULT_ID = uuid.UUID("66666666-6666-4666-8666-666666666666")


def _work_packet() -> WorkPacket:
    return WorkPacket(
        packet_id=FIXED_PACKET_ID,
        task_type=WorkPacketTaskType.IMPLEMENT_STORY,
        project_id=FIXED_PROJECT_ID,
        story_id=FIXED_STORY_ID,
        objective=WorkObjective(
            goal="REDACTED — implement the approved Story LLD for one backend endpoint.",
            success_definition="REDACTED — the endpoint exists, matches the LLD's API Changes section, and required_checks all pass.",
            non_goals=["REDACTED — no frontend changes in this packet."],
        ),
        acceptance_criteria=[
            AcceptanceCriterion(id="AC-1", description="REDACTED criterion one.", verification_method="unit test"),
        ],
        repository=RepositoryReference(
            provider="github", owner="REDACTED-ORG", name="REDACTED-REPO",
            base_branch="main", base_commit_sha="0123456789abcdef0123456789abcdef01234567",
        ),
        upstream_artifacts=[
            ArtifactReference(
                artifact_id=FIXED_ARTIFACT_ID, artifact_type="story_lld", status="APPROVED",
                summary="REDACTED bounded summary of the approved Story LLD.",
            ),
        ],
        knowledge_context=[
            KnowledgeReference(
                chunk_id="chunk-1", source_id="source-1", source_title="REDACTED Coding Standard",
                content_type="COMPANY_STANDARD", similarity=0.31, snippet="REDACTED standard excerpt.",
            ),
        ],
        scope_policy=ScopePolicy(allowed_paths=["apps/api/app/services/**"], denied_paths=["apps/api/.env"], max_files_changed=5),
        tool_policy=ToolPolicy(allowed_tools=["read_file", "write_file", "run_tests"], denied_tools=["delete_repository"]),
        required_checks=[RequiredCheck(name="backend-test-suite", description="pytest must pass.", command="cd apps/api && pytest -q")],
        budget_policy=BudgetPolicy(max_cost_usd=2.0, max_wall_clock_seconds=600, max_llm_calls=10, max_tool_calls=30, max_repair_attempts=3),
        approval_policy=ApprovalPolicy(requires_human_approval=True, approval_roles=["TECH_LEAD"]),
        created_at=FIXED_TIME,
        created_by_user_id=FIXED_USER_ID,
        extensions={"internal": {"note": "REDACTED example extension value"}},
    )


def _runtime_instruction_package() -> RuntimeInstructionPackage:
    return RuntimeInstructionPackage(
        packet=_work_packet(),
        system_instructions="REDACTED rendered system instructions for a runtime to follow.",
        response_contract_description="A single JSON object matching the ExecutionResult schema (schema_version 1.0.0).",
        expected_output_schema_ref="ExecutionResult.schema.json",
        generated_at=FIXED_TIME,
        generator_metadata={"template_version": "REDACTED-1"},
    )


def _execution_result() -> ExecutionResult:
    return ExecutionResult(
        result_id=FIXED_RESULT_ID,
        packet_id=FIXED_PACKET_ID,
        state=ExecutionState.COMPLETED,
        summary="REDACTED short outcome summary.",
        file_changes=[
            FileChangeEvidence(path="apps/api/app/services/REDACTED.py", change_type="modify", summary="REDACTED change summary.", diff="REDACTED unified diff."),
        ],
        commands=[
            CommandEvidence(command="cd apps/api && pytest -q", real_execution=False, exit_code=None, stdout_excerpt="REDACTED — reasoning-based assessment, not a real run."),
        ],
        test_evidence=[
            TestEvidence(name="test_redacted_example", result=TestResult.PASS_, real_execution=False, criterion_id="AC-1", notes="REDACTED reasoning-based assessment."),
        ],
        usage=UsageEvidence(prompt_tokens=1200, completion_tokens=400, total_tokens=1600, cost_usd=0.02, llm_calls_made=1, repair_attempts_used=0),
        started_at=FIXED_TIME,
        completed_at=FIXED_TIME,
    )


def _clarification_result() -> ExecutionResult:
    return ExecutionResult(
        result_id=uuid.UUID("77777777-7777-4777-8777-777777777777"),
        packet_id=FIXED_PACKET_ID,
        state=ExecutionState.CLARIFICATION_REQUIRED,
        clarification=ClarificationRequest(questions=["REDACTED clarifying question one?"], blocking=True),
        started_at=FIXED_TIME,
    )


def main() -> None:
    fixtures = {
        "work_packet_v1_0_0.json": _work_packet(),
        "runtime_instruction_package_v1_0_0.json": _runtime_instruction_package(),
        "execution_result_completed_v1_0_0.json": _execution_result(),
        "execution_result_clarification_v1_0_0.json": _clarification_result(),
    }
    for filename, model in fixtures.items():
        path = OUT_DIR / filename
        path.write_text(json.dumps(json.loads(model.model_dump_json()), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
