"""Schema-version compatibility tests for app/agent_runtime.

Two concerns, kept separate:
  1. JSON Schema generation (schema_export.py) actually produces valid,
     current schemas for every registered contract, and the committed
     files under app/agent_runtime/json_schema/ are not stale relative to
     the current model definitions.
  2. The pinned example payloads under tests/fixtures/agent_runtime/ —
     real, schema_version="1.0.0" JSON documents — still validate against
     today's contracts. These fixtures are the actual backward-compatibility
     guard: if a future contract change breaks one of them, this test
     fails, which is the point — a genuinely breaking change to "1.0.0"
     should never happen; it should introduce a new SchemaVersion literal
     instead (see app/agent_runtime/base.py).
"""

import json
from pathlib import Path

import pytest

from app.agent_runtime import ExecutionResult, RuntimeInstructionPackage, WorkPacket
from app.agent_runtime.base import CURRENT_SCHEMA_VERSION
from app.agent_runtime.registry import CONTRACT_REGISTRY, TOP_LEVEL_CONTRACTS
from app.agent_runtime.schema_export import DEFAULT_OUTPUT_DIR, generate_json_schemas

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "agent_runtime"


# --- JSON Schema generation -----------------------------------------------------------


@pytest.mark.parametrize("name,model", CONTRACT_REGISTRY.items(), ids=list(CONTRACT_REGISTRY.keys()))
def test_model_json_schema_generates_without_error(name, model):
    schema = model.model_json_schema()
    assert schema["title"] == name
    assert schema["type"] == "object"


def test_committed_schema_files_exist_for_every_registered_contract():
    for name in CONTRACT_REGISTRY:
        path = DEFAULT_OUTPUT_DIR / f"{name}.schema.json"
        assert path.exists(), f"missing committed schema file for {name} — run `python -m app.agent_runtime.schema_export`"


def test_committed_schema_files_match_current_models(tmp_path):
    """Fails if a contract was changed without regenerating the committed
    .schema.json files — same discipline as a migration accompanying a
    model change. Regenerates into a temp dir and diffs against the
    committed files rather than overwriting them as a side effect of
    running the test suite."""
    regenerated = generate_json_schemas(output_dir=tmp_path)
    for name, path in regenerated.items():
        committed_path = DEFAULT_OUTPUT_DIR / f"{name}.schema.json"
        assert committed_path.exists(), f"missing committed schema file for {name}"
        assert path.read_text(encoding="utf-8") == committed_path.read_text(encoding="utf-8"), (
            f"{name}.schema.json is stale — run `cd apps/api && .venv/Scripts/python.exe -m "
            "app.agent_runtime.schema_export` and commit the diff."
        )


# --- Pinned example payload compatibility ----------------------------------------------


def _load_fixture(filename: str) -> dict:
    return json.loads((FIXTURES_DIR / filename).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "filename,model",
    [
        ("work_packet_v1_0_0.json", WorkPacket),
        ("runtime_instruction_package_v1_0_0.json", RuntimeInstructionPackage),
        ("execution_result_completed_v1_0_0.json", ExecutionResult),
        ("execution_result_clarification_v1_0_0.json", ExecutionResult),
    ],
)
def test_pinned_v1_0_0_fixture_still_validates(filename, model):
    payload = _load_fixture(filename)
    assert payload["schema_version"] == "1.0.0"
    instance = model.model_validate(payload)
    assert instance.schema_version == "1.0.0"


def test_pinned_fixtures_round_trip_through_serialization():
    """model_validate(payload) -> model_dump(mode='json') should reproduce
    the same logical document — guards against a field silently changing
    serialized shape (e.g. an enum starting to serialize by name instead
    of value) without a version bump."""
    for filename, model in [
        ("work_packet_v1_0_0.json", WorkPacket),
        ("execution_result_completed_v1_0_0.json", ExecutionResult),
    ]:
        payload = _load_fixture(filename)
        instance = model.model_validate(payload)
        round_tripped = json.loads(instance.model_dump_json())
        assert round_tripped == payload, f"{filename} does not round-trip byte-for-byte through {model.__name__}"


def test_current_schema_version_constant_matches_fixture_versions():
    for filename in [
        "work_packet_v1_0_0.json",
        "runtime_instruction_package_v1_0_0.json",
        "execution_result_completed_v1_0_0.json",
        "execution_result_clarification_v1_0_0.json",
    ]:
        assert _load_fixture(filename)["schema_version"] == CURRENT_SCHEMA_VERSION


def test_every_top_level_contract_has_at_least_one_pinned_fixture():
    """Every envelope contract this phase versions must have a real
    example payload guarding it — a new top-level contract added later
    without a fixture is a gap this test catches."""
    covered = {"WorkPacket", "RuntimeInstructionPackage", "ExecutionResult"}
    assert set(TOP_LEVEL_CONTRACTS) <= covered
