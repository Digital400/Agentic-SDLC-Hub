"""ORM model registry.

Import order here doesn't matter for correctness (see the note in
`app/models/base.py` about string-based relationship resolution), but this
module must be imported — directly or via `app.models.base.Base.metadata`
usage — before `sqlalchemy.orm.configure_mappers()` runs, so every model
class below is registered on the shared `Base` before relationships are
resolved. Alembic's `env.py` and `app/db/seed.py` both import this module
for exactly that reason.
"""

from app.models.base import Base
from app.models.enums import (
    AgentPromptRole,
    AgentRunStatus,
    ArtifactStatus,
    IntegrationProvider,
    IntegrationStatus,
    KnowledgeSourceStatus,
    KnowledgeSourceType,
    LoopStatus,
    LoopStepType,
    ProjectRole,
    ProjectStatus,
    ReviewStatus,
    UserRole,
    WorkflowAction,
    WorkflowStatus,
)
from app.models.user import User
from app.models.project import Project, ProjectMember
from app.models.workflow import WorkflowEdge, WorkflowNode
from app.models.artifact import Artifact, ArtifactVersion
from app.models.review import Review, ReviewComment
from app.models.agent import AgentDefinition, AgentPrompt, AgentRun, AgentRunLoopEvent
from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.models.integration import Integration
from app.models.audit import AuditLog

__all__ = [
    "Base",
    "WorkflowStatus",
    "WorkflowAction",
    "ProjectStatus",
    "ProjectRole",
    "ReviewStatus",
    "ArtifactStatus",
    "AgentPromptRole",
    "AgentRunStatus",
    "LoopStatus",
    "LoopStepType",
    "KnowledgeSourceType",
    "KnowledgeSourceStatus",
    "IntegrationProvider",
    "IntegrationStatus",
    "UserRole",
    "User",
    "Project",
    "ProjectMember",
    "WorkflowNode",
    "WorkflowEdge",
    "Artifact",
    "ArtifactVersion",
    "Review",
    "ReviewComment",
    "AgentDefinition",
    "AgentPrompt",
    "AgentRun",
    "AgentRunLoopEvent",
    "KnowledgeSource",
    "KnowledgeChunk",
    "Integration",
    "AuditLog",
]
