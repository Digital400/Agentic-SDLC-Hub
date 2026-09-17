"""Reference and criterion contracts shared by WorkPacket — see
app/agent_runtime/__init__.py.

RepositoryReference, ArtifactReference, and KnowledgeReference are all
REFERENCES, not payloads: none of them duplicates an upstream artifact's
full content (hard rule 3). A concrete runtime integration is expected to
resolve a reference against this application's own read APIs at execution
time (see docs/architecture/universal-agent-runtime-baseline.md section 11
for the existing endpoints such a resolver would call) rather than this
package ever carrying that content itself.
"""

import uuid

from pydantic import Field

from app.agent_runtime.base import AgentRuntimeModel


class RepositoryReference(AgentRuntimeModel):
    """Which repository, and which exact commit, a task's scope applies
    to. Deliberately vendor-neutral: `provider` is a free-text label (e.g.
    "github"), not a closed enum tied to this codebase's current sole
    integration (app/services/github_integration.py) — Phase 01 defines
    the contract shape, not a commitment to GitHub specifically.

    HARD RULE: `base_commit_sha` is REQUIRED and immutable once the
    WorkPacket is created — a runtime must pin its scope to a known
    commit, never "the current tip of base_branch at execution time,"
    so a delayed or retried execution can't silently pick up unrelated
    intervening changes. `base_branch` is carried alongside purely for
    human/audit readability (e.g. rendering "based on main @ <sha>"); the
    SHA, not the branch name, is what actually pins scope.
    """

    provider: str = Field(..., min_length=1, description="Free-text VCS provider label, e.g. 'github'. Not a closed enum — see class docstring.")
    owner: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    base_branch: str = Field(..., min_length=1, description="Human-readable only — base_commit_sha is what actually pins scope.")
    base_commit_sha: str = Field(..., min_length=7, description="Immutable, required. The exact commit this task's scope is pinned to.")


class ArtifactReference(AgentRuntimeModel):
    """A pointer to an approved upstream artifact — never its full content
    (hard rule 3). Mirrors the existing approved_artifact_content/
    approved_artifact_summaries split in
    app/services/ai_generation.py's build_prioritized_context: `summary`
    here plays the same role as agent_context_summary there (a bounded,
    pre-computed stand-in for the full document), and is the ONLY textual
    content this reference may carry — there is deliberately no
    `full_content` field on this model.
    """

    artifact_id: uuid.UUID
    artifact_version_id: uuid.UUID | None = Field(None, description="None means 'the artifact's current version' — resolved at execution time, not pinned.")
    artifact_type: str = Field(..., min_length=1, description="e.g. 'hld_document', 'story_backlog', 'lld_document'.")
    status: str = Field(..., min_length=1, description="The artifact's status at reference time, e.g. 'APPROVED' — for audit/display only, not re-validated by this contract.")
    summary: str | None = Field(None, max_length=4000, description="A bounded context summary (see app/services/artifact_summary.py) — never the full document body.")


class KnowledgeReference(AgentRuntimeModel):
    """A pointer to one retrieved Knowledge Base chunk — mirrors
    app/services/retrieval.py's RetrievedChunk. Unlike ArtifactReference,
    a knowledge chunk IS already a bounded excerpt by construction (RAG
    chunks are pre-sized for exactly this purpose), so `snippet` carrying
    that excerpt does not violate hard rule 3 the way embedding a full
    artifact would.
    """

    chunk_id: str = Field(..., min_length=1)
    source_id: str = Field(..., min_length=1)
    source_title: str = Field(..., min_length=1)
    content_type: str = Field(..., min_length=1, description="e.g. 'COMPANY_STANDARD', 'PAST_ARTIFACT' — see KnowledgeContentType.")
    similarity: float = Field(..., ge=0.0, le=1.0)
    snippet: str = Field(..., max_length=4000, description="The retrieved chunk's own bounded content — not a full document.")
    stage: str | None = None


class AcceptanceCriterion(AgentRuntimeModel):
    """One checkable condition a completed WorkPacket is expected to
    satisfy — mirrors ImplementationTask.acceptance_criteria, promoted to
    a structured type here so RequiredCheck/TestEvidence can reference a
    specific criterion by id rather than re-matching free text."""

    id: str = Field(..., min_length=1, description="Stable within one WorkPacket — e.g. 'AC-1'. Referenced by TestEvidence.criterion_id.")
    description: str = Field(..., min_length=1)
    verification_method: str | None = Field(
        None, description="Free-text hint for how this should be checked, e.g. 'unit test' or 'manual QA' — advisory only, not enforced by this contract."
    )
