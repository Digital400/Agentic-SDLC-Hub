from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.services.repo_context_builder import RepoContextPreviewResult


class RelevantFileRead(BaseModel):
    path: str
    entry_type: str
    size: int | None
    score: int
    reasons: list[str]
    content_mode: str
    snippet: str | None


class SuggestedEditScopeEntry(BaseModel):
    path: str
    status: str  # "existing" | "new"


class RepoContextPreviewRead(BaseModel):
    """The full "what will be sent to the coding agent" preview — see
    app/services/repo_context_builder.py's RepoContextBuilderService. Every
    field here is meant to be shown to a human before any agent run."""

    relevant_folders: list[str]
    relevant_files: list[RelevantFileRead]
    architecture_summary: str
    dependency_notes: list[str]
    suggested_edit_scope: list[SuggestedEditScopeEntry]
    token_budget_report: dict[str, Any]
    files_considered: int
    files_included: int

    @classmethod
    def from_result(cls, result: RepoContextPreviewResult) -> "RepoContextPreviewRead":
        return cls(
            relevant_folders=result.relevant_folders,
            relevant_files=[
                RelevantFileRead(
                    path=f.path, entry_type=f.entry_type, size=f.size, score=f.score,
                    reasons=f.reasons, content_mode=f.content_mode, snippet=f.snippet,
                )
                for f in result.relevant_files
            ],
            architecture_summary=result.architecture_summary,
            dependency_notes=result.dependency_notes,
            suggested_edit_scope=[SuggestedEditScopeEntry(**e) for e in result.suggested_edit_scope],
            token_budget_report=result.token_budget_report,
            files_considered=result.files_considered,
            files_included=result.files_included,
        )
