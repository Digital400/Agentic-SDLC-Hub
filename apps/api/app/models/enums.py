"""Enums shared across models.

`WorkflowStatus` and `WorkflowAction` intentionally mirror
`packages/shared/src/workflow.ts` — keep the two in sync if either changes.
"""

import enum


class WorkflowStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    APPROVED = "APPROVED"
    NEEDS_CHANGES = "NEEDS_CHANGES"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class WorkflowAction(str, enum.Enum):
    DRAFT = "draft"
    EDIT = "edit"
    SUBMIT_FOR_REVIEW = "submit_for_review"
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"
    COMPLETE = "complete"


class ProjectStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class ProjectRole(str, enum.Enum):
    """A user's role within a single project (not a global/system role)."""

    OWNER = "OWNER"
    CONTRIBUTOR = "CONTRIBUTOR"
    APPROVER = "APPROVER"
    VIEWER = "VIEWER"


class ReviewStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    NEEDS_CHANGES = "NEEDS_CHANGES"
    REJECTED = "REJECTED"


class AgentPromptRole(str, enum.Enum):
    """The three roles agents may play, per the product's AI agent principle."""

    DRAFT = "draft"
    IMPROVE = "improve"
    VALIDATE = "validate"


class AgentRunStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
