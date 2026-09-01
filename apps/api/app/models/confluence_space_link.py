from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ConfluenceSpaceLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One this-app Project's configured Confluence space — see
    app/services/confluence_integration.py (the read/create REST client)
    and app/models/integration_connection.py (which credential this
    link's calls authenticate with). Mirrors app/models/jira_project_link.py's
    JiraProjectLink exactly, plus the two root-page fields below.

    `root_page_id`/`root_page_url` are the project's page-hierarchy root
    (requirement 5) — created once, eagerly, atomically with this row: see
    app/api/routes/confluence_integration.py's create_confluence_space_link,
    which only saves this row if the root page was actually created. Every
    artifact published for this project becomes a child page under
    `root_page_id` (see app/models/confluence_page_link.py).
    """

    __tablename__ = "confluence_space_links"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("integration_connections.id"), nullable=False)
    space_key: Mapped[str] = mapped_column(String(50), nullable=False)
    # Populated/refreshed from Confluence's own GET /wiki/rest/api/space/{key}
    # response at connect time — not user-entered.
    space_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    root_page_id: Mapped[str] = mapped_column(String(100), nullable=False)
    root_page_url: Mapped[str] = mapped_column(String(500), nullable=False)

    project: Mapped["Project"] = relationship("Project")
    connection: Mapped["IntegrationConnection"] = relationship("IntegrationConnection")
