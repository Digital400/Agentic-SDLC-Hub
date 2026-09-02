"""Orchestrates CodeRunnerService's primitives (app/services/code_runner.py)
for one already-ACCEPTED story-scoped ImplementationRun — the Story Code
Implementation Agent's Flow steps 4-9: apply the reviewed patch, run
tests, commit, push, and (only on success) move the story's lane toward
its GitHub PR stage.

Deliberately separate from app/api/routes/implementation_runs.py's
existing create_pull_request: that action commits file-by-file through
GitHub's REST API with no local checkout at all — an already-shipped,
unmodified path this module doesn't touch or replace. This module is
the *local git* alternative CodeRunnerService exists for — cloning a
real workspace, applying the diff, running configured tests, and only
then committing/pushing a real branch.

RULE — "Do not apply changes without user approval": start_code_run
refuses anything but an ACCEPTED ImplementationRun; nothing in this
module can ever be reached with a PENDING_REVIEW or REJECTED one.

RULE — "Do not implement multiple stories in one run": structural, same
as everywhere else in this codebase — ImplementationRun.story_id is a
single nullable FK, so exactly one story is representable here.

RULE — "Do not push to main": checked explicitly before ever creating
the feature branch, same defense-in-depth convention as
implementation_runs.py's own create_pull_request.

RULE — "If tests fail, keep logs and mark CodeRun failed": a failing
configured test command stops the pipeline immediately — no commit, no
push, no lane advance — and CodeRun.logs (append-only throughout) is
never touched by the failure path, only CodeRun.status/error_message.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import (
    CodeRun,
    CodeRunStatus,
    ImplementationRun,
    ImplementationRunReviewStatus,
    Repository,
    Story,
    StoryActivityLog,
    StoryDeliveryNodeStatus,
    User,
)
from app.services.audit import record_audit_log
from app.services.code_runner import CodeRunnerError, CodeRunnerService, generate_branch_name
from app.services.story_delivery import advance_lane


class StoryCodeImplementationError(Exception):
    """Raised when the pipeline can't even start — an unapproved run, a
    non-story-scoped run, or a base-branch collision. A failure *during*
    the pipeline (clone/apply/test/commit/push) is captured as
    CodeRun.status=FAILED instead of raised, so its logs survive and the
    caller can still read back a normal CodeRun row."""


def create_code_run(
    db: Session, *, implementation_run: ImplementationRun, repository: Repository, triggered_by: User,
) -> CodeRun:
    """Rule — "do not apply changes without user approval." Creates the
    CodeRun row (QUEUED) and assigns its real branch name; running the
    actual pipeline is a separate call (run_code_implementation_pipeline)
    so a caller can inspect/audit the row before committing to running it."""
    if implementation_run.review_status != ImplementationRunReviewStatus.ACCEPTED:
        raise StoryCodeImplementationError(
            f"ImplementationRun {implementation_run.id} is {implementation_run.review_status.value}, not ACCEPTED — cannot apply."
        )
    if implementation_run.story_id is None:
        raise StoryCodeImplementationError("CodeRunnerService is only wired up for story-scoped implementation runs.")

    code_run = CodeRun(
        project_id=implementation_run.project_id, story_id=implementation_run.story_id, lane_id=implementation_run.lane_id,
        repository_id=repository.id, triggered_by_user_id=triggered_by.id,
        branch_name="", status=CodeRunStatus.QUEUED, logs=[],
    )
    db.add(code_run)
    db.flush()
    code_run.branch_name = generate_branch_name(implementation_run.implementation_task.title, code_run.id)
    db.flush()
    return code_run


def run_code_implementation_pipeline(
    db: Session, *, code_run: CodeRun, implementation_run: ImplementationRun, repository: Repository,
    story: Story, base_branch: str, test_commands: list[str],
) -> CodeRun:
    """Flow steps 5-9. Never raises for an in-pipeline failure — every
    outcome (including FAILED) is reflected on `code_run` and returned
    normally, so a caller never has to catch an exception to know a
    test/clone/push failed; StoryCodeImplementationError is reserved for
    the "shouldn't even be able to try" cases in create_code_run above."""
    service = CodeRunnerService(db)
    try:
        # RULE — never push to main. Checked before anything else touches
        # the workspace, not just before the final push.
        if code_run.branch_name in (base_branch, repository.default_branch):
            raise CodeRunnerError("Refusing to use the base/default branch as the feature branch.")

        workspace = service.create_workspace(code_run)
        service.clone_repository(code_run, workspace, repository, base_branch)
        service.create_branch(code_run, workspace, code_run.branch_name)
        service.apply_changes(code_run, workspace, implementation_run.proposed_file_changes)

        if test_commands:
            results = service.run_tests(code_run, workspace, test_commands)
            if any(not r.succeeded for r in results):
                # RULE — tests failed: keep logs, mark FAILED, stop here.
                # No commit, no push, no lane advance.
                service.set_status(code_run, CodeRunStatus.FAILED, error_message="One or more configured test commands failed.")
                db.flush()
                return code_run

        commit_message_lines = (implementation_run.pr_description or "").strip().splitlines()
        commit_message = commit_message_lines[0] if commit_message_lines else f"Implement {story.title}"
        service.create_commit(code_run, workspace, commit_message)
        service.push_branch(code_run, workspace, repository, code_run.branch_name)

        _advance_lane_to_pr_stage(db, code_run=code_run, story=story, triggered_by_user_id=code_run.triggered_by_user_id)
        db.flush()
    except CodeRunnerError as exc:
        service.set_status(code_run, CodeRunStatus.FAILED, error_message=str(exc))
        db.flush()
    return code_run


def _advance_lane_to_pr_stage(db: Session, *, code_run: CodeRun, story: Story, triggered_by_user_id: uuid.UUID | None) -> None:
    """Flow step 9 — "Move lane to GitHub PR stage." Completes this
    story's IMPLEMENTATION node now that real, tested, committed, pushed
    code exists — advance_lane unlocks the lane's own next stage
    (TEST_SCENARIOS, per the existing sequence — see
    app/services/story_delivery.py), the story's actual path toward its
    later PULL_REQUEST stage. A no-op if the node is already COMPLETED
    (e.g. this pipeline is re-run after an earlier success)."""
    lane = story.delivery_lane
    if lane is None:
        return
    implementation_node = next((n for n in lane.nodes if n.node_key == "IMPLEMENTATION"), None)
    if implementation_node is None or implementation_node.status == StoryDeliveryNodeStatus.COMPLETED:
        return

    implementation_node.status = StoryDeliveryNodeStatus.COMPLETED
    implementation_node.completed_at = datetime.now(timezone.utc)
    db.add(
        StoryActivityLog(
            story_id=story.id, lane_id=lane.id, node_id=implementation_node.id, action="story_lane_node.status_changed",
            actor_user_id=triggered_by_user_id, details={"node_key": "IMPLEMENTATION", "to": "COMPLETED", "code_run_id": str(code_run.id)},
        )
    )
    advance_lane(db, lane=lane, completed_node=implementation_node)
    record_audit_log(
        db, project_id=code_run.project_id, actor_user_id=triggered_by_user_id, action="code_run.pushed",
        entity_type="CodeRun", entity_id=code_run.id,
        extra_data={"story_id": str(story.id), "branch_name": code_run.branch_name},
    )
