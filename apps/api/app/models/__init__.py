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
    ApprovalRecommendation,
    ArtifactStatus,
    ImplementationRunReviewStatus,
    ImplementationRunStatus,
    ImplementationTaskArea,
    ImplementationTaskRiskLevel,
    ImplementationTaskStatus,
    IntegrationProvider,
    IntegrationStatus,
    KnowledgeContentType,
    KnowledgeSourceStatus,
    KnowledgeSourceType,
    LoopStatus,
    LoopStepType,
    JiraSourceType,
    MaintenanceRunStatus,
    StoryType,
    StoryStatus,
    StoryDeliveryLaneStatus,
    StoryDeliveryNodeStatus,
    SprintStatus,
    SprintStoryStatus,
    ReleaseStatus,
    PRReviewRecommendation,
    PRReviewRunStatus,
    ProjectRole,
    ProjectStatus,
    PullRequestStatus,
    RepositoryFileEntryType,
    ReviewStatus,
    TestAgentType,
    TestRunStatus,
    UserRole,
    WorkflowAction,
    WorkflowStatus,
)
from app.models.user import User
from app.models.project import Project, ProjectMember
from app.models.workflow import WorkflowEdge, WorkflowNode
from app.models.artifact import Artifact, ArtifactVersion
from app.models.sprint import Sprint
from app.models.sprint_story import SprintStory
from app.models.release import Release
from app.models.release_story import ReleaseStory
from app.models.story import Story
from app.models.story_assignee import StoryAssignee
from app.models.story_delivery_node import StoryDeliveryNode
from app.models.story_delivery_edge import StoryDeliveryEdge
from app.models.story_delivery_lane import StoryDeliveryLane
from app.models.story_artifact import StoryArtifact
from app.models.story_activity_log import StoryActivityLog
from app.models.review import Review, ReviewComment
from app.models.agent import AgentDefinition, AgentPrompt, AgentRun, AgentRunLoopEvent
from app.models.validator import ValidatorDefinition
from app.models.implementation_task import ImplementationTask
from app.models.implementation_run import ImplementationRun
from app.models.pull_request_link import PullRequestLink
from app.models.test_run import TestRun
from app.models.pr_review_run import PRReviewRun
from app.models.maintenance_run import MaintenanceRun
from app.models.jira_project_link import JiraProjectLink
from app.models.jira_issue_link import JiraIssueLink
from app.models.confluence_space_link import ConfluenceSpaceLink
from app.models.confluence_page_link import ConfluencePageLink
from app.models.knowledge import KnowledgeChunk, KnowledgeSource
from app.models.integration import Integration
from app.models.integration_connection import IntegrationConnection
from app.models.repository import Repository, RepositoryFileIndex, RepositorySnapshot
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
    "ApprovalRecommendation",
    "ImplementationTaskArea",
    "ImplementationTaskRiskLevel",
    "ImplementationTaskStatus",
    "ImplementationRun",
    "ImplementationRunStatus",
    "ImplementationRunReviewStatus",
    "PullRequestLink",
    "PullRequestStatus",
    "TestRun",
    "TestRunStatus",
    "TestAgentType",
    "PRReviewRun",
    "PRReviewRunStatus",
    "PRReviewRecommendation",
    "MaintenanceRun",
    "MaintenanceRunStatus",
    "Story",
    "StoryType",
    "StoryStatus",
    "StoryAssignee",
    "StoryDeliveryLane",
    "StoryDeliveryLaneStatus",
    "StoryDeliveryNode",
    "StoryDeliveryNodeStatus",
    "StoryDeliveryEdge",
    "StoryArtifact",
    "StoryActivityLog",
    "Sprint",
    "SprintStatus",
    "SprintStory",
    "SprintStoryStatus",
    "Release",
    "ReleaseStatus",
    "ReleaseStory",
    "JiraProjectLink",
    "JiraIssueLink",
    "JiraSourceType",
    "ConfluenceSpaceLink",
    "ConfluencePageLink",
    "RepositoryFileEntryType",
    "LoopStatus",
    "LoopStepType",
    "KnowledgeSourceType",
    "KnowledgeSourceStatus",
    "KnowledgeContentType",
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
    "ValidatorDefinition",
    "ImplementationTask",
    "KnowledgeSource",
    "KnowledgeChunk",
    "Integration",
    "IntegrationConnection",
    "Repository",
    "RepositorySnapshot",
    "RepositoryFileIndex",
    "AuditLog",
]
