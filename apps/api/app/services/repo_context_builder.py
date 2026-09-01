"""RepoContextBuilderService — turns an ImplementationTask + a repository
snapshot into the bounded slice of repo context a coding agent should
actually see, and exposes it as a human-reviewable preview before any
agent runs (see app/api/routes/projects.py's repo-context-preview
endpoint).

Sibling to app/services/context_builder.py (ContextBuilderService), which
does the same job for stage-artifact context: same primitives
(ContextBlock, TokenBudgetService — see app/services/token_budget.py),
same "structured, directly testable, real output fields, not just a flat
prompt string" shape, same "usable as a standalone preview, independent
of running a real model call" intent.

The hard rule this whole module exists to enforce: **never hand a coding
agent the whole repository**. A RepositorySnapshot's RepositoryFileIndex
can list thousands of paths; this service scores every one of them
against one specific task, keeps only the relevant few (bounded by
`max_file_count`), and further bounds their *content* by a token budget —
large or low-priority files get a path-only summary rather than their
full body. Everything this produces is meant to be shown to a human
before it's ever sent to an agent, per the product's human-in-the-loop
principle (see docs/product-vision.md).

No LLM call anywhere here — purely deterministic scoring/heuristics
(unlike validator_agent.py/implementation_planner.py, which have a
real-AI path). There's nothing here an LLM would do better: relevance
scoring against a task's own declared expected_paths/area/keywords is a
mechanical match, not a judgment call.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models import ImplementationTask, KnowledgeContentType, Project, RepositoryFileEntryType, RepositorySnapshot, WorkflowNode
from app.services import github_integration as github_api
from app.services.github_integration import GitHubIntegrationError, MAX_PREVIEWABLE_FILE_SIZE_BYTES
from app.services.graph_engine import GraphEngineService
from app.services.markdown_sections import find_section
from app.services.retrieval import RetrievedChunk, retrieve_relevant_chunks
from app.services.token_budget import ContextBlock, TokenBudgetService

logger = logging.getLogger(__name__)

# Requirement 5's two caps — sane defaults, overridable per call (e.g. from
# query params on the preview endpoint).
DEFAULT_MAX_FILE_COUNT = 40
DEFAULT_MAX_CONTEXT_TOKENS = 6000

# Bounds how many live GitHub reads one preview can trigger — the highest-
# scored files only, everything else is metadata-only ("summary"). Keeps
# preview latency predictable regardless of how big max_file_count is.
TOP_CONTENT_FILES = 8

_CODING_STANDARD_CONTENT_TYPES = [
    KnowledgeContentType.COMPANY_STANDARD,
    KnowledgeContentType.ARCHITECTURE_RULE,
    KnowledgeContentType.TESTING_STANDARD,
]

# Area -> path/extension signals that make a file conventionally "of this
# area" — a light heuristic bump, not a filter (see module docstring: no
# file is excluded purely for not matching its task's area).
_AREA_PATH_HINTS: dict[str, tuple[str, ...]] = {
    "BACKEND": (".py",),
    "FRONTEND": (".ts", ".tsx", ".js", ".jsx"),
    "DATABASE": ("alembic/", "migrations/", "/models/"),
    "TESTING": ("tests/", "test_", "_test."),
    "INFRA": ("dockerfile", ".yml", ".yaml", ".github/"),
    "DOCS": (".md",),
}

_LARGE_FILE_PENALTY_THRESHOLD_BYTES = 100_000


class RepoContextBuilderError(Exception):
    """Caller error — a task/snapshot that don't make sense together, not a
    runtime/provider failure."""


@dataclass
class RelevantFile:
    path: str
    entry_type: str  # always "FILE" — folders are reported via relevant_folders, not this list
    size: int | None
    score: int
    reasons: list[str] = field(default_factory=list)
    content_mode: str = "summary"  # "full" | "summary" | "omitted"
    snippet: str | None = None


@dataclass
class RepoContextPreviewResult:
    relevant_folders: list[str] = field(default_factory=list)
    relevant_files: list[RelevantFile] = field(default_factory=list)
    architecture_summary: str = ""
    dependency_notes: list[str] = field(default_factory=list)
    suggested_edit_scope: list[dict[str, str]] = field(default_factory=list)
    token_budget_report: dict[str, Any] = field(default_factory=dict)
    files_considered: int = 0
    files_included: int = 0


def _task_keywords(task: ImplementationTask) -> set[str]:
    text = " ".join(
        filter(None, [task.title, task.description, task.linked_lld_section, task.area.value])
    ).lower()
    # Keep tokens of at least 3 chars — shorter ones (e.g. "id", "ui") are
    # too noisy to score a path match against reliably.
    return {tok for tok in _split_words(text) if len(tok) >= 3}


def _split_words(text: str) -> list[str]:
    word = []
    words = []
    for ch in text:
        if ch.isalnum():
            word.append(ch)
        elif word:
            words.append("".join(word))
            word = []
    if word:
        words.append("".join(word))
    return words


def _expected_path_prefixes(task: ImplementationTask) -> list[str]:
    return [p.rstrip("/").lower() for p in task.expected_paths if p]


def _matches_expected_path(path_lower: str, expected: list[str]) -> bool:
    return any(path_lower == p or path_lower.startswith(p + "/") for p in expected)


def _shares_parent_with_expected(path_lower: str, expected: list[str]) -> bool:
    parent = "/".join(path_lower.split("/")[:-1])
    return any(parent == e or parent.startswith(e + "/") or e.startswith(parent + "/") for e in expected if parent)


def _score_file(path: str, size: int | None, task: ImplementationTask, keywords: set[str], expected: list[str]) -> tuple[int, list[str]]:
    path_lower = path.lower()
    score = 0
    reasons: list[str] = []

    if expected and _matches_expected_path(path_lower, expected):
        score += 10
        reasons.append("matches an expected path")
    elif expected and _shares_parent_with_expected(path_lower, expected):
        score += 5
        reasons.append("shares a folder with an expected path")

    keyword_hits = [kw for kw in keywords if kw in path_lower]
    if keyword_hits:
        score += 3 * len(keyword_hits)
        reasons.append(f"path matches task keyword(s): {', '.join(sorted(keyword_hits)[:5])}")

    hints = _AREA_PATH_HINTS.get(task.area.value, ())
    if any(hint in path_lower for hint in hints):
        score += 2
        reasons.append(f"matches {task.area.value} file convention")

    if size is not None and size > _LARGE_FILE_PENALTY_THRESHOLD_BYTES:
        score -= 1

    return score, reasons


def fetch_coding_standards_chunks(db: Session, project: Project, task: ImplementationTask) -> list[RetrievedChunk]:
    """Best-effort — coding standards are additive input, never a reason a
    preview should fail. Falls back to the task's own workflow node if the
    project has no `implementation` stage node (e.g. a custom workflow
    template), and swallows any retrieval failure (no pgvector in this
    environment, embedding failure, etc.)."""
    try:
        node = (
            db.query(WorkflowNode)
            .filter(WorkflowNode.project_id == project.id, WorkflowNode.node_key == "implementation")
            .first()
        ) or task.workflow_node
        if node is None:
            return []
        return retrieve_relevant_chunks(
            db,
            project=project,
            node=node,
            freeform_context={"query": f"{task.title} {task.description}"},
            approved_inputs={},
            content_types=_CODING_STANDARD_CONTENT_TYPES,
            top_k=5,
        )
    except Exception as exc:  # noqa: BLE001 — RAG input is additive, never blocks a preview
        logger.warning("Coding-standards retrieval failed (%s); continuing without it.", exc)
        return []


def _build_architecture_summary(
    *, task: ImplementationTask, relevant_folders: list[str], lld_excerpt: str, standards_count: int
) -> str:
    folders_text = ", ".join(relevant_folders) if relevant_folders else "no matching folders were found in the snapshot"
    parts = [
        f"Task \"{task.title}\" ({task.area.value}) primarily touches: {folders_text}.",
    ]
    if lld_excerpt:
        parts.append(f"LLD context: {lld_excerpt.strip()[:300]}")
    if standards_count:
        parts.append(f"{standards_count} relevant coding-standard excerpt(s) from the Knowledge Base were included.")
    else:
        parts.append("No matching coding standards were found in the Knowledge Base for this task.")
    return "\n".join(parts)


def _build_dependency_notes(task: ImplementationTask, standards_chunks: list[RetrievedChunk]) -> list[str]:
    notes = [f"Depends on completion of: {dep}" for dep in task.dependencies]
    notes += [f"Follow standard: {chunk.source_title}" for chunk in standards_chunks]
    return notes


class RepoContextBuilderService:
    def __init__(self, db: Session, *, max_file_count: int = DEFAULT_MAX_FILE_COUNT, max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS):
        self.db = db
        self.max_file_count = max_file_count
        self.max_context_tokens = max_context_tokens

    def build(
        self,
        *,
        task: ImplementationTask,
        snapshot: RepositorySnapshot,
        github_token: str | None,
        transport: httpx.BaseTransport | None = None,
    ) -> RepoContextPreviewResult:
        project = task.project

        # 1. Approved LLD input (same GraphEngineService call
        # implementation_planner.py's route already relies on).
        lld_content = ""
        if task.workflow_node is not None:
            inputs = GraphEngineService(self.db).resolve_required_inputs(
                project=project, node=task.workflow_node, freeform_context={}
            )
            lld_content = inputs.approved_artifact_content.get("lld_document", "")
        lld_section_content = lld_content
        if task.linked_lld_section:
            section = find_section(lld_content, task.linked_lld_section)
            if section is not None:
                lld_section_content = section["content"]

        # 2. Coding standards from RAG.
        standards_chunks = fetch_coding_standards_chunks(self.db, project, task)

        # 3. Score every file in the snapshot.
        keywords = _task_keywords(task)
        expected = _expected_path_prefixes(task)
        file_rows = [f for f in snapshot.files if f.entry_type == RepositoryFileEntryType.FILE]

        scored: list[RelevantFile] = []
        for row in file_rows:
            score, reasons = _score_file(row.path, row.size, task, keywords, expected)
            scored.append(RelevantFile(path=row.path, entry_type="FILE", size=row.size, score=score, reasons=reasons))

        # 4. Selection — expected-path matches always survive (up to the
        # cap), then fill remaining slots by score, dropping score <= 0.
        always_include = [f for f in scored if "matches an expected path" in f.reasons]
        rest = sorted((f for f in scored if f not in always_include and f.score > 0), key=lambda f: f.score, reverse=True)
        included = (always_include + rest)[: self.max_file_count]
        included.sort(key=lambda f: f.score, reverse=True)

        # 5. Relevant folders.
        relevant_folders = sorted({"/".join(f.path.split("/")[:-1]) for f in included if "/" in f.path})

        # 6. Suggested edit scope — existing vs new, cross-referenced
        # against the snapshot's own file index.
        snapshot_paths_lower = {row.path.lower() for row in snapshot.files}
        suggested_edit_scope = [
            {"path": p, "status": "existing" if p.rstrip("/").lower() in snapshot_paths_lower else "new"}
            for p in task.expected_paths
        ]

        # 7. Content fetch, capped to the top TOP_CONTENT_FILES.
        content_candidates = sorted(included, key=lambda f: f.score, reverse=True)[:TOP_CONTENT_FILES]
        content_candidate_paths = {f.path for f in content_candidates}
        for f in included:
            if f.path not in content_candidate_paths:
                continue
            if not github_token or (f.size or 0) > MAX_PREVIEWABLE_FILE_SIZE_BYTES:
                continue
            try:
                file_content = github_api.read_file(
                    github_token, snapshot.repository.owner, snapshot.repository.name, f.path, snapshot.ref, transport=transport
                )
            except GitHubIntegrationError as exc:
                f.content_mode = "omitted"
                f.reasons.append(f"could not fetch content: {exc}")
                continue
            if file_content.content is None:
                f.content_mode = "omitted"
                f.reasons.append("binary or too large to preview")
                continue
            f.content_mode = "full"
            f.snippet = file_content.content

        # 8. Token budget.
        blocks: list[ContextBlock] = []
        if task.description or lld_section_content:
            blocks.append(
                ContextBlock(
                    priority="P0",
                    label="task_and_lld",
                    compressible=True,
                    content=f"# Task: {task.title}\n{task.description}\n\n# LLD context\n{lld_section_content}",
                )
            )
        for chunk in standards_chunks:
            blocks.append(
                ContextBlock(
                    priority="P1", label=f"standard:{chunk.chunk_id}",
                    content=f"## Standard: {chunk.source_title}\n{chunk.content}",
                )
            )
        file_content_labels: dict[str, str] = {}
        for f in included:
            if f.content_mode != "full" or not f.snippet:
                continue
            label = f"file:{f.path}"
            file_content_labels[f.path] = label
            blocks.append(ContextBlock(priority="P2", label=label, compressible=True, content=f"## File: {f.path}\n{f.snippet}"))
        file_list_text = "\n".join(f"- {f.path} ({f.size or 0} bytes)" for f in included)
        if file_list_text:
            blocks.append(ContextBlock(priority="P3", label="file_list", content=file_list_text))

        budget_result = TokenBudgetService(context_token_budget=self.max_context_tokens, output_token_budget=0).build(blocks)
        fitted_by_label = {b.label: b for b in budget_result.blocks}
        for f in included:
            label = file_content_labels.get(f.path)
            if label is None:
                continue
            fitted = fitted_by_label.get(label)
            if fitted is None or not fitted.included:
                f.content_mode = "omitted"
                f.snippet = None
                f.reasons.append("dropped to fit the token budget")
            elif fitted.truncated:
                f.snippet = fitted.content

        # 9 + 10. Architecture summary + dependency notes.
        architecture_summary = _build_architecture_summary(
            task=task, relevant_folders=relevant_folders, lld_excerpt=lld_section_content, standards_count=len(standards_chunks)
        )
        dependency_notes = _build_dependency_notes(task, standards_chunks)

        return RepoContextPreviewResult(
            relevant_folders=relevant_folders,
            relevant_files=included,
            architecture_summary=architecture_summary,
            dependency_notes=dependency_notes,
            suggested_edit_scope=suggested_edit_scope,
            token_budget_report=budget_result.to_report_dict(),
            files_considered=len(file_rows),
            files_included=len(included),
        )
