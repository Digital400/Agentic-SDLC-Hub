"""Universal Work Packet and Result contracts (Phase 01).

This package is the vendor-neutral "wire format" boundary between this
codebase's own workflow/graph-engine state (see app/services/graph_engine.py,
app/services/loop_engine.py — see docs/architecture/universal-agent-runtime-
baseline.md for the Phase 00 inspection this package builds on) and any
external agent runtime that might execute a unit of work on its behalf.

HARD RULES (Phase 01 scope — enforced by construction, not merely documented):
  1. No contract here contains a vendor-specific MANDATORY field. A concrete
     runtime's own configuration (model name, provider-specific sampling
     params, vendor session ids, ...) belongs in the namespaced `extensions`
     dict every top-level contract carries — see `AgentRuntimeModel`.
  2. Every model here sets `extra="forbid"` (via `AgentRuntimeModel`), so an
     integrator literally cannot bolt an undeclared field onto a contract
     instead of using `extensions` — this is structural, not a convention.
  3. Upstream context is passed BY REFERENCE (`ArtifactReference`,
     `KnowledgeReference`) — an artifact's full content is never duplicated
     into a WorkPacket; the runtime that ultimately executes the packet is
     expected to resolve a reference against this application's own APIs
     (see docs/architecture/universal-agent-runtime-baseline.md section 11
     for the existing endpoints a resolver would call).
  4. No secret-shaped field exists anywhere in this package (no token, key,
     password, credential, or authorization-header field on any contract) —
     see `test_agent_runtime_contracts.py::test_no_contract_field_is_secret_shaped`
     for the enforcement of this rule.
  5. This package defines DATA ONLY. It does not call any provider, does not
     import app.services.ai_generation, and does not execute anything — see
     the Phase 01 instruction "do not connect an external runtime yet."

Versioning: every top-level envelope contract (`WorkPacket`,
`RuntimeInstructionPackage`, `ExecutionResult`) carries a pinned
`schema_version` field (currently `"1.0.0"`, see `SCHEMA_VERSION` below). A
breaking change to any contract's shape must introduce a new
`SchemaVersion` literal value alongside the old one (additive), not silently
repoint the existing version at different data — see
`test_agent_runtime_schema_versioning.py`.
"""

from app.agent_runtime.capability import RuntimeCapability, RuntimeCapabilityManifest
from app.agent_runtime.common import (
    AcceptanceCriterion,
    ArtifactReference,
    KnowledgeReference,
    RepositoryReference,
)
from app.agent_runtime.enums import (
    ChangeType,
    CheckSeverity,
    ExecutionState,
    FailureCategory,
    TestResult,
    WorkPacketTaskType,
)
from app.agent_runtime.execution_result import (
    ClarificationRequest,
    CommandEvidence,
    ExecutionResult,
    FileChangeEvidence,
    RuntimeFailure,
    TestEvidence,
    UsageEvidence,
)
from app.agent_runtime.policies import (
    ApprovalPolicy,
    BudgetPolicy,
    RequiredCheck,
    ScopePolicy,
    ToolPolicy,
)
from app.agent_runtime.work_packet import (
    RuntimeInstructionPackage,
    WorkObjective,
    WorkPacket,
)

# The current, single supported contract version — see module docstring's
# "Versioning" note. Bump only by adding a new literal to
# app.agent_runtime.base.SchemaVersion alongside this one, never by
# repointing it.
SCHEMA_VERSION = "1.0.0"

__all__ = [
    "SCHEMA_VERSION",
    # common
    "AcceptanceCriterion",
    "ArtifactReference",
    "KnowledgeReference",
    "RepositoryReference",
    "RuntimeCapability",
    "RuntimeCapabilityManifest",
    # enums
    "ChangeType",
    "CheckSeverity",
    "ExecutionState",
    "FailureCategory",
    "TestResult",
    "WorkPacketTaskType",
    # policies
    "ApprovalPolicy",
    "BudgetPolicy",
    "RequiredCheck",
    "ScopePolicy",
    "ToolPolicy",
    # work packet
    "RuntimeInstructionPackage",
    "WorkObjective",
    "WorkPacket",
    # execution result
    "ClarificationRequest",
    "CommandEvidence",
    "ExecutionResult",
    "FileChangeEvidence",
    "RuntimeFailure",
    "TestEvidence",
    "UsageEvidence",
]
