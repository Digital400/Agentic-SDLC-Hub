from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class JiraProjectLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One this-app Project's configured Jira project — see
    app/services/jira_integration.py (the read/create REST client) and
    app/models/integration_connection.py (which credential this link's
    calls authenticate with). Mirrors app/models/repository.py's
    Repository exactly: same "one external project per this-app project,
    naming which connection to use" role, just for Jira instead of GitHub.
    """

    __tablename__ = "jira_project_links"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("integration_connections.id"), nullable=False)
    jira_project_key: Mapped[str] = mapped_column(String(50), nullable=False)
    # Populated/refreshed from Jira's own GET /rest/api/3/project/{key}
    # response at connect time — not user-entered.
    jira_project_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    connection: Mapped["IntegrationConnection"] = relationship("IntegrationConnection")
