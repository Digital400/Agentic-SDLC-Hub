"""Phase 02: Runtime Capability Registry — contract-level tests for
RuntimeCapability/RuntimeKind/ExecutionLocation and the extended
RuntimeCapabilityManifest. See app/coding_runtime/__init__.py.
"""

import pytest
from pydantic import ValidationError

from app.agent_runtime.capability import RuntimeCapability, RuntimeCapabilityManifest
from app.coding_runtime import ExecutionLocation, RuntimeKind


def test_runtime_capability_manifest_defaults_to_no_capabilities():
    manifest = RuntimeCapabilityManifest(runtime_name="x", max_context_tokens=1000, max_output_tokens=100)
    assert manifest.capabilities == []


def test_runtime_capability_manifest_accepts_the_full_capability_set():
    manifest = RuntimeCapabilityManifest(
        runtime_name="x", max_context_tokens=1000, max_output_tokens=100, capabilities=list(RuntimeCapability),
    )
    assert len(manifest.capabilities) == len(RuntimeCapability)


def test_runtime_capability_manifest_rejects_an_unknown_capability_string():
    with pytest.raises(ValidationError):
        RuntimeCapabilityManifest(runtime_name="x", max_context_tokens=1000, max_output_tokens=100, capabilities=["NOT_A_REAL_CAPABILITY"])


def test_runtime_capability_manifest_still_forbids_extra_fields():
    """Hard rule 2 (app.agent_runtime's own package docstring) still
    applies to this Phase 02 extension — no field can be bolted on
    outside `extensions`."""
    with pytest.raises(ValidationError):
        RuntimeCapabilityManifest(runtime_name="x", max_context_tokens=1000, max_output_tokens=100, some_made_up_field="nope")


def test_runtime_capability_enum_names_the_full_phase02_vocabulary():
    expected = {
        "REPOSITORY_READ", "FILE_EDIT", "PATCH_GENERATION", "SHELL_EXECUTION", "TEST_EXECUTION",
        "LSP", "MCP", "STREAMING", "HUMAN_APPROVAL", "PAUSE_RESUME", "CANCELLATION", "SANDBOX",
        "USAGE_REPORTING", "CUSTOM_MODEL_GATEWAY", "BROWSER_IMAGE_SUPPORT",
    }
    assert {c.value for c in RuntimeCapability} == expected


def test_runtime_kind_and_execution_location_name_the_phase02_vocabulary():
    assert {k.value for k in RuntimeKind} == {"DOCUMENT", "CODING", "INTEGRATION"}
    assert {e.value for e in ExecutionLocation} == {"COMPANY_SANDBOX", "DEVELOPER_LOCAL", "EXTERNAL_MANAGED"}
