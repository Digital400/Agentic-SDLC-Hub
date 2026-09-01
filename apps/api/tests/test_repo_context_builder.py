"""Tests for the Repo Context Builder:
  1. RepoContextBuilderService exists and is directly callable.
  4. File relevance scoring.
  5. Max file count / max token budget caps are respected.
  6. Large/low-priority files come back as content_mode="summary", not "full".
  7. Preview API — 404s and a success shape, with no DB write / audit log.

Follows test_github_integration.py's conventions: httpx.MockTransport for
GitHub calls (no real network), plain functions, direct calls into the real
route function (no TestClient exists in this repo).
"""

import uuid

import httpx
import pytest

from app.api.routes.projects import preview_repo_context
from app.models import (
    AuditLog,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Repository,
    RepositoryFileEntryType,
    RepositoryFileIndex,
    RepositorySnapshot,
)
from app.models.enums import ImplementationTaskArea
from app.services import repo_context_builder
from app.services.repo_context_builder import RepoContextBuilderService, _score_file, _task_keywords
from tests.conftest import make_approved_artifact, make_node

SAMPLE_LLD = (
    "## Feature Overview\n\nLets a user reset their password via email.\n\n"
    "## Password Reset Endpoint\n\nAdd a POST /auth/password-reset endpoint that emails a reset token.\n"
)


@pytest.fixture(autouse=True)
def _no_rag(monkeypatch):
    # Sidesteps the pgvector-only KnowledgeChunk table (not in TEST_TABLES,
    # same reasoning conftest.py documents) — RAG input is additive per the
    # module's own contract, so an empty list is a legitimate stand-in.
    monkeypatch.setattr(repo_context_builder, "fetch_coding_standards_chunks", lambda db, project, task: [])


def _make_task(db, project, node, artifact, **overrides):
    from app.models import ImplementationTask, ImplementationTaskRiskLevel, ImplementationTaskStatus

    defaults = dict(
        project_id=project.id,
        workflow_node_id=node.id,
        artifact_id=artifact.id,
        artifact_version_id=artifact.current_version_id,
        title="Add password reset endpoint",
        description="Add a POST /auth/password-reset endpoint.",
        linked_lld_section="Password Reset Endpoint",
        area=ImplementationTaskArea.BACKEND,
        expected_paths=["apps/api/app/api/routes/auth.py"],
        dependencies=[],
        acceptance_criteria=[],
        test_expectation="",
        risk_level=ImplementationTaskRiskLevel.MEDIUM,
        assigned_agent_type="backend-coding-agent",
        status=ImplementationTaskStatus.PENDING,
        order_index=0,
    )
    defaults.update(overrides)
    task = ImplementationTask(**defaults)
    db.add(task)
    db.flush()
    db.refresh(task)
    return task


def _make_repository_with_snapshot(db, project, actor, *, files: list[tuple[str, int]]):
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration,
        # Deliberately NOT a valid Fernet ciphertext — route-level tests
        # exercise the "no usable token" path (decrypt fails closed, the
        # preview degrades to summary-only) so they never attempt a real
        # network call. Tests that need real fetched content call
        # RepoContextBuilderService.build directly with an explicit
        # github_token + a MockTransport instead (see below).
        access_token_encrypted="not-a-real-fernet-token",
        token_last_four="7890",
        github_username="octocat",
        status=IntegrationStatus.CONNECTED,
    )
    db.add(connection)
    db.flush()
    repository = Repository(project=project, connection=connection, owner="octocat", name="hello-world", default_branch="main")
    db.add(repository)
    db.flush()
    snapshot = RepositorySnapshot(repository=repository, ref="main", commit_sha="abc123", file_count=len(files), truncated=False)
    db.add(snapshot)
    db.flush()
    for path, size in files:
        db.add(RepositoryFileIndex(snapshot=snapshot, path=path, entry_type=RepositoryFileEntryType.FILE, size=size, sha="deadbeef"))
    db.flush()
    db.refresh(snapshot)
    return repository, snapshot


def _setup(db, project, actor, *, files):
    lld_node = make_node(db, project, node_key="lld", order_index=1, output_artifact_type="lld_document")
    make_approved_artifact(db, project, lld_node, actor, content=SAMPLE_LLD)
    plan_node = make_node(
        db, project, node_key="implementation_planning", order_index=2,
        required_inputs=["lld_document"], output_artifact_type="implementation_plan",
    )
    plan_artifact = make_approved_artifact(db, project, plan_node, actor, content="# Implementation Plan\n")
    task = _make_task(db, project, plan_node, plan_artifact)
    repository, snapshot = _make_repository_with_snapshot(db, project, actor, files=files)
    return task, repository, snapshot


# --- Scoring (requirement 4) ---------------------------------------------------------


def test_expected_path_match_scores_highest():
    from app.models import ImplementationTask

    task = ImplementationTask(
        title="Add password reset endpoint", description="password reset", area=ImplementationTaskArea.BACKEND,
        expected_paths=["apps/api/app/api/routes/auth.py"], dependencies=[], acceptance_criteria=[],
    )
    keywords = _task_keywords(task)
    expected = [p.lower() for p in task.expected_paths]

    matching_score, matching_reasons = _score_file("apps/api/app/api/routes/auth.py", 1000, task, keywords, expected)
    unrelated_score, _ = _score_file("apps/web/components/foo.tsx", 1000, task, keywords, expected)

    assert "matches an expected path" in matching_reasons
    assert matching_score > unrelated_score


def test_area_convention_gives_a_small_bonus():
    from app.models import ImplementationTask

    task = ImplementationTask(
        title="Add password reset endpoint", description="password reset", area=ImplementationTaskArea.BACKEND,
        expected_paths=[], dependencies=[], acceptance_criteria=[],
    )
    keywords = _task_keywords(task)
    py_score, py_reasons = _score_file("apps/api/app/services/unrelated.py", 500, task, keywords, [])
    ts_score, _ = _score_file("apps/web/components/unrelated.tsx", 500, task, keywords, [])

    assert any("convention" in r for r in py_reasons)
    assert py_score > ts_score


# --- RepoContextBuilderService.build (requirements 2, 3, 5, 6) -----------------------


def test_build_returns_relevant_files_folders_and_dependency_notes(db, project, actor):
    task, repository, snapshot = _setup(
        db, project, actor,
        files=[
            ("apps/api/app/api/routes/auth.py", 2000),
            ("apps/web/components/unrelated.tsx", 2000),
        ],
    )
    task.dependencies = ["Add password_resets table migration"]
    db.flush()

    service = RepoContextBuilderService(db)
    result = service.build(task=task, snapshot=snapshot, github_token=None)

    included_paths = {f.path for f in result.relevant_files}
    assert "apps/api/app/api/routes/auth.py" in included_paths
    assert "apps/api/app/api/routes" in result.relevant_folders
    assert any("Depends on completion of: Add password_resets table migration" in n for n in result.dependency_notes)
    assert result.suggested_edit_scope == [{"path": "apps/api/app/api/routes/auth.py", "status": "existing"}]
    assert "Password Reset Endpoint" in result.architecture_summary or "password reset" in result.architecture_summary.lower()


def test_max_file_count_is_respected(db, project, actor):
    files = [(f"apps/api/app/services/auth_service_{i}.py", 100) for i in range(20)]
    task, repository, snapshot = _setup(db, project, actor, files=files)

    service = RepoContextBuilderService(db, max_file_count=5)
    result = service.build(task=task, snapshot=snapshot, github_token=None)

    assert len(result.relevant_files) <= 5
    assert result.files_included <= 5
    assert result.files_considered == len(files)


def test_low_rank_and_no_token_files_are_summary_only(db, project, actor):
    task, repository, snapshot = _setup(
        db, project, actor, files=[("apps/api/app/api/routes/auth.py", 2000)]
    )

    service = RepoContextBuilderService(db)
    result = service.build(task=task, snapshot=snapshot, github_token=None)

    assert all(f.content_mode == "summary" for f in result.relevant_files)
    assert all(f.snippet is None for f in result.relevant_files)


def test_content_is_fetched_for_top_files_when_a_token_is_available(db, project, actor):
    task, repository, snapshot = _setup(
        db, project, actor, files=[("apps/api/app/api/routes/auth.py", 200)]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "auth.py" in request.url.path
        import base64
        content = base64.b64encode(b"def reset_password(): ...").decode()
        return httpx.Response(200, json={"sha": "abc", "size": 26, "content": content})

    service = RepoContextBuilderService(db)
    result = service.build(
        task=task, snapshot=snapshot, github_token="ghp_faketoken", transport=httpx.MockTransport(handler)
    )

    matching = [f for f in result.relevant_files if f.path == "apps/api/app/api/routes/auth.py"]
    assert matching[0].content_mode == "full"
    assert "reset_password" in matching[0].snippet


def test_token_budget_report_is_shaped_correctly(db, project, actor):
    task, repository, snapshot = _setup(db, project, actor, files=[("apps/api/app/api/routes/auth.py", 200)])

    service = RepoContextBuilderService(db, max_context_tokens=500)
    result = service.build(task=task, snapshot=snapshot, github_token=None)

    report = result.token_budget_report
    assert report["context_token_budget"] == 500
    assert "blocks" in report
    assert all({"priority", "label", "estimated_tokens", "included", "truncated"} <= set(b.keys()) for b in report["blocks"])


def test_very_small_token_budget_drops_full_content_to_omitted(db, project, actor):
    task, repository, snapshot = _setup(db, project, actor, files=[("apps/api/app/api/routes/auth.py", 200)])

    def handler(request: httpx.Request) -> httpx.Response:
        import base64
        content = base64.b64encode(b"x" * 5000).decode()
        return httpx.Response(200, json={"sha": "abc", "size": 5000, "content": content})

    # A budget far too small for the full 5000-byte file forces it to be
    # truncated (per token_budget.py's MIN_COMPRESSED_TOKENS floor, a
    # compressible block is cut down rather than dropped entirely) — this
    # truncation IS the "prefer summaries for large files" behavior; it
    # never claims a full, untruncated body for content this large.
    service = RepoContextBuilderService(db, max_context_tokens=1)
    result = service.build(
        task=task, snapshot=snapshot, github_token="ghp_faketoken", transport=httpx.MockTransport(handler)
    )

    matching = [f for f in result.relevant_files if f.path == "apps/api/app/api/routes/auth.py"]
    assert matching[0].content_mode == "full"
    assert len(matching[0].snippet) < 5000
    assert "truncated" in matching[0].snippet


# --- Route (requirement 7) ------------------------------------------------------------


def test_preview_404s_when_no_repository_is_configured(db, project, actor):
    lld_node = make_node(db, project, node_key="lld", order_index=1, output_artifact_type="lld_document")
    make_approved_artifact(db, project, lld_node, actor, content=SAMPLE_LLD)
    plan_node = make_node(
        db, project, node_key="implementation_planning", order_index=2,
        required_inputs=["lld_document"], output_artifact_type="implementation_plan",
    )
    plan_artifact = make_approved_artifact(db, project, plan_node, actor)
    task = _make_task(db, project, plan_node, plan_artifact)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        preview_repo_context(project.id, task.id, db)
    assert exc_info.value.status_code == 404


def test_preview_404s_for_a_task_from_a_different_project(db, project, actor):
    from app.models import Project, ProjectStatus

    task, repository, snapshot = _setup(db, project, actor, files=[("apps/api/app/api/routes/auth.py", 200)])
    other_project = Project(
        name="Other", business_owner="Owner", workflow_template_id="sdlc-workflow",
        workflow_template_version="test", current_stage="node_a", status=ProjectStatus.ACTIVE, created_by_id=actor.id,
    )
    db.add(other_project)
    db.flush()

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        preview_repo_context(other_project.id, task.id, db)
    assert exc_info.value.status_code == 404


def test_preview_succeeds_and_writes_nothing(db, project, actor):
    task, repository, snapshot = _setup(db, project, actor, files=[("apps/api/app/api/routes/auth.py", 200)])
    audit_count_before = db.query(AuditLog).count()

    # Called directly as a plain function (no TestClient in this repo, per
    # convention) — Query(...) defaults aren't resolved outside a real
    # FastAPI request, so every Query-backed param is passed explicitly.
    result = preview_repo_context(project.id, task.id, db, snapshot_id=snapshot.id, max_files=40, max_tokens=6000)

    assert result.files_considered == 1
    assert db.query(AuditLog).count() == audit_count_before


def test_preview_404s_for_unknown_snapshot_id(db, project, actor):
    task, repository, snapshot = _setup(db, project, actor, files=[("apps/api/app/api/routes/auth.py", 200)])

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        preview_repo_context(project.id, task.id, db, snapshot_id=uuid.uuid4())
    assert exc_info.value.status_code == 404
