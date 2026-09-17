"""Phase 10: Story Implementation, Branch and PR Workflow — tests for
app/services/story_git_pr_flow.py and the feature-flagged
POST /implementation-runs/{id}/create-pull-request-v2 endpoint.

Reuses tests/test_pull_request_creation.py's exact fixture/mock
conventions (same _chain/_add_repository/_accepted_run/_mock_github
helpers) — this phase is a real, additive alternative to that file's own
create_pull_request, not a separate system.
"""

import pytest
from fastapi import HTTPException

from app.api.routes.implementation_runs import create_pull_request_v2, review_implementation_run, start_implementation_run
from app.core.config import get_settings
from app.models import ImplementationRun, PullRequestLink, PullRequestStatus
from app.schemas.implementation_run import CreatePullRequestRequest, ReviewImplementationRunRequest, StartImplementationRunRequest
from app.services import story_git_pr_flow
from tests.test_pull_request_creation import _accepted_run, _add_repository, _chain, _mock_github

# app.agent_runtime.common.RepositoryReference.base_commit_sha requires
# min_length=7 (Phase 01's own hard rule, predating this phase) —
# test_pull_request_creation.py's shared _add_repository fixture uses the
# 6-character "abc123" across a dozen other test files this phase must
# not touch, so a WorkPacket-building test here lengthens its OWN run's
# snapshot sha instead of changing that shared fixture.
_LONG_SHA = "abc123abc123abc123abc123abc123abc123ab"


def _orm_run(db, run) -> ImplementationRun:
    """_accepted_run/start_implementation_run/review_implementation_run
    all return the Pydantic ImplementationRunRead response shape, not the
    ORM row — this fetches the real row (with real relationships, e.g.
    .repository_snapshot) a test needs to inspect directly."""
    return db.get(ImplementationRun, run.id)


def _lengthen_snapshot_sha(db, run) -> None:
    orm_run = _orm_run(db, run)
    orm_run.repository_snapshot.commit_sha = _LONG_SHA
    db.flush()


@pytest.fixture(autouse=True)
def _enable_v2_flag(monkeypatch):
    monkeypatch.setattr(get_settings(), "STORY_GIT_PR_FLOW_V2_ENABLED", True)


def _regenerate_and_accept(db, task, actor):
    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    return review_implementation_run(run.id, ReviewImplementationRunRequest(decision="ACCEPTED", reviewed_by_user_id=actor.id), db)


# --- Feature flag ------------------------------------------------------------------------


def test_v2_endpoint_404s_when_the_flag_is_off(db, project, actor, monkeypatch):
    monkeypatch.setattr(get_settings(), "STORY_GIT_PR_FLOW_V2_ENABLED", False)
    task, run = _accepted_run(db, project, actor)
    _mock_github(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        create_pull_request_v2(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 404


# --- Same gates as the existing create_pull_request -----------------------------------


def test_v2_is_blocked_when_run_is_not_accepted(db, project, actor, monkeypatch):
    task = _chain(db, project, actor)
    _add_repository(db, project)
    run = start_implementation_run(StartImplementationRunRequest(implementation_task_id=task.id, triggered_by_user_id=actor.id), db)
    _mock_github(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        create_pull_request_v2(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    assert exc_info.value.status_code == 409
    assert db.query(PullRequestLink).count() == 0


# --- Happy path: one atomic commit, not one per file ------------------------------------


def test_v2_creates_the_pr_with_one_atomic_commit_not_one_per_file(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    _lengthen_snapshot_sha(db, run)
    calls = _mock_github(monkeypatch)

    result = create_pull_request_v2(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    link = db.query(PullRequestLink).filter(PullRequestLink.implementation_run_id == run.id).first()
    assert link is not None
    assert link.pr_number == 7
    assert link.status == PullRequestStatus.OPEN
    assert result.pull_request is not None

    # Exactly one create_tree + one create_commit + one update_ref — never
    # N of each for N changed files (the bug this phase fixes).
    assert len([c for c in calls if c[0] == "create_tree"]) == 1
    assert len([c for c in calls if c[0] == "create_commit"]) == 1
    assert len([c for c in calls if c[0] == "update_ref"]) == 1
    # And never the old Contents-API, one-commit-per-file calls.
    assert not [c for c in calls if c[0] in ("create_or_update_file", "delete_file")]


def test_v2_pins_the_branch_to_the_snapshots_exact_commit_sha(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    _lengthen_snapshot_sha(db, run)
    calls = _mock_github(monkeypatch)

    create_pull_request_v2(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    branch_calls = [c for c in calls if c[0] == "create_branch_at_sha"]
    assert len(branch_calls) == 1
    assert branch_calls[0][2] == _LONG_SHA


def test_v2_never_writes_to_the_default_branch(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    _lengthen_snapshot_sha(db, run)
    calls = _mock_github(monkeypatch)

    create_pull_request_v2(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    branch_name = [c for c in calls if c[0] == "create_branch_at_sha"][0][1]
    assert branch_name != "main"
    assert branch_name.startswith("story/")


# --- Idempotency — the other real bug this phase fixes ----------------------------------


def test_v2_regenerate_pushes_one_more_atomic_commit_onto_the_same_pr(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    _lengthen_snapshot_sha(db, run)
    calls = _mock_github(monkeypatch)
    first = create_pull_request_v2(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    calls.clear()

    second_run = _regenerate_and_accept(db, task, actor)
    second = create_pull_request_v2(second_run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    # Still exactly one PullRequestLink row — repointed at the new run —
    # never a second row for a second PR.
    assert db.query(PullRequestLink).count() == 1
    link = db.query(PullRequestLink).first()
    assert link.pr_number == first.pull_request.pr_number == second.pull_request.pr_number
    assert link.implementation_run_id == second_run.id

    # No second branch/PR ever created — only one more commit onto the
    # existing branch.
    assert not [c for c in calls if c[0] in ("create_branch_at_sha", "create_pull_request")]
    assert len([c for c in calls if c[0] == "create_commit"]) == 1
    assert ("get_branch_head_sha", link.branch_name) in calls


def test_v2_does_not_reuse_a_merged_prs_branch(db, project, actor, monkeypatch):
    task, run = _accepted_run(db, project, actor)
    _lengthen_snapshot_sha(db, run)
    calls = _mock_github(monkeypatch)
    first = create_pull_request_v2(run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)
    first_link = db.query(PullRequestLink).filter(PullRequestLink.implementation_run_id == run.id).first()
    first_link.status = PullRequestStatus.MERGED
    db.flush()
    calls.clear()

    second_run = _regenerate_and_accept(db, task, actor)
    second = create_pull_request_v2(second_run.id, CreatePullRequestRequest(triggered_by_user_id=actor.id), db)

    assert db.query(PullRequestLink).count() == 2
    assert second.pull_request.pr_number != first.pull_request.pr_number
    assert [c for c in calls if c[0] == "create_branch_at_sha"]


# --- Runtime selection (Phase 02 connection) ---------------------------------------------


def test_select_runtime_for_task_selects_the_legacy_coding_adapter(db, project, actor):
    task, run = _accepted_run(db, project, actor)
    _lengthen_snapshot_sha(db, run)
    orm_run = _orm_run(db, run)
    packet = story_git_pr_flow.build_work_packet_for_run(
        run=orm_run, task=task, story=None, repository=orm_run.repository_snapshot.repository, base_branch="main",
    )

    _registry, decision = story_git_pr_flow.select_runtime_for_task(packet, task)

    assert decision.selected_runtime == "legacy-coding"
    assert decision.rejected == {}


def test_build_work_packet_pins_base_commit_sha_to_the_real_snapshot(db, project, actor):
    task, run = _accepted_run(db, project, actor)
    _lengthen_snapshot_sha(db, run)
    orm_run = _orm_run(db, run)
    repository = orm_run.repository_snapshot.repository

    packet = story_git_pr_flow.build_work_packet_for_run(run=orm_run, task=task, story=None, repository=repository, base_branch="main")

    assert packet.repository.base_commit_sha == _LONG_SHA
    assert packet.repository.owner == "octocat"
    assert packet.task_type.value == "IMPLEMENT_STORY"
    assert packet.packet_id == orm_run.id
