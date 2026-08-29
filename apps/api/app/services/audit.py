"""Small helper for writing AuditLog rows from route handlers.

Kept separate from the models so route code doesn't construct AuditLog
directly — one place to change if the audit log's shape ever grows (e.g.
request IDs, IP addresses).
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def record_audit_log(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_agent_run_id: uuid.UUID | None = None,
    extra_data: dict[str, Any] | None = None,
) -> AuditLog:
    """Create and register (but don't commit) one audit log entry."""
    entry = AuditLog(
        project_id=project_id,
        actor_user_id=actor_user_id,
        actor_agent_run_id=actor_agent_run_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        extra_data=extra_data,
    )
    db.add(entry)
    return entry
