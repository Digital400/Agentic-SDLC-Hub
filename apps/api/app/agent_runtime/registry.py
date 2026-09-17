"""The full set of named contracts in this package, in one place — used by
schema_export.py to generate JSON Schemas, and by
tests/test_agent_runtime_contracts.py to assert every contract satisfies
this package's hard rules without having to enumerate them twice.
"""

from app.agent_runtime.capability import RuntimeCapabilityManifest
from app.agent_runtime.common import AcceptanceCriterion, ArtifactReference, KnowledgeReference, RepositoryReference
from app.agent_runtime.execution_result import (
    ClarificationRequest,
    CommandEvidence,
    ExecutionResult,
    FileChangeEvidence,
    RuntimeFailure,
    TestEvidence,
    UsageEvidence,
)
from app.agent_runtime.policies import ApprovalPolicy, BudgetPolicy, RequiredCheck, ScopePolicy, ToolPolicy
from app.agent_runtime.work_packet import RuntimeInstructionPackage, WorkObjective, WorkPacket

# name -> model class, for every contract this phase's instructions name
# explicitly. Order here is the order schema_export writes files in — kept
# alphabetical for a stable, reviewable diff.
CONTRACT_REGISTRY = {
    "AcceptanceCriterion": AcceptanceCriterion,
    "ApprovalPolicy": ApprovalPolicy,
    "ArtifactReference": ArtifactReference,
    "BudgetPolicy": BudgetPolicy,
    "ClarificationRequest": ClarificationRequest,
    "CommandEvidence": CommandEvidence,
    "ExecutionResult": ExecutionResult,
    "FileChangeEvidence": FileChangeEvidence,
    "KnowledgeReference": KnowledgeReference,
    "RepositoryReference": RepositoryReference,
    "RequiredCheck": RequiredCheck,
    "RuntimeCapabilityManifest": RuntimeCapabilityManifest,
    "RuntimeFailure": RuntimeFailure,
    "RuntimeInstructionPackage": RuntimeInstructionPackage,
    "ScopePolicy": ScopePolicy,
    "TestEvidence": TestEvidence,
    "ToolPolicy": ToolPolicy,
    "UsageEvidence": UsageEvidence,
    "WorkObjective": WorkObjective,
    "WorkPacket": WorkPacket,
}

# The three envelope contracts that carry a pinned schema_version field —
# see app/agent_runtime/base.py. Every other contract in CONTRACT_REGISTRY
# is a component of one of these and versions implicitly alongside it.
TOP_LEVEL_CONTRACTS = ("WorkPacket", "RuntimeInstructionPackage", "ExecutionResult")
