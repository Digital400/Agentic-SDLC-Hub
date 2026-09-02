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

GITHUB PR CREATION (create_pull_request_from_code_run) — "After
CodeRunner pushes branch, create GitHub PR":
RULE — "PR creation requires pushed branch": refused unless
CodeRun.status == PUSHED.
RULE — "PR body must include Jira key": always rendered, even when the
story has none yet (an explicit "not synced" line, never silently
omitted).
DISCLOSED SCOPE — the story's own PULL_REQUEST lane node only completes
here if it's already reachable (not LOCKED). Under the default lane
sequence (see app/services/story_delivery.py) PULL_REQUEST sits after
TEST_SCENARIOS, so a PR created immediately after a push — before
Test Scenarios has been approved — still gets created for real (GitHub
doesn't wait on this app's own lane state), but the lane node itself
only advances once it's actually unlockable in sequence; nothing here
forces nodes out of order.
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
    PullRequestLink,
    PullRequestStatus,
    Repository,
    Story,
    StoryActivityLog,
    StoryArtifact,
    StoryDeliveryNodeStatus,
    User,
)
from app.services.audit import record_audit_log
from app.services.code_runner import CodeRunnerError, CodeRunnerService, generate_branch_name
from app.services.github_integration import GitHubIntegrationError
from app.services.story_delivery import advance_lane
from app.services.story_implementation_plan_agent import STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE

_JIRA_LINE_LABEL = "Jira"


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
        repository_id=repository.id, implementation_run_id=implementation_run.id, triggered_by_user_id=triggered_by.id,
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


def _build_pr_title(story: Story) -> str:
    """Requirement 2 — "Generate PR title from story/Jira key.\""""
    if story.jira_issue_key:
        return f"[{story.jira_issue_key}] {story.title}"
    return story.title


def _build_pr_body(*, story: Story, implementation_run: ImplementationRun, implementation_plan_content: str, code_run: CodeRun) -> str:
    """Requirement 3 — story summary, implementation plan, changed
    files, test results, risks. RULE — the Jira key line is always
    present, even when there isn't one yet, never silently omitted."""
    changed_files = (
        "\n".join(f"- `{c.get('path', '?')}` ({c.get('change_type', 'modify')})" for c in implementation_run.proposed_file_changes)
        or "(no files listed)"
    )
    risks = "\n".join(f"- {r}" for r in implementation_run.risks) or "None recorded."
    # HONESTY: this app has no real test-execution sandbox (see
    # app/services/testing_agent.py's own disclosure) — run_code_implementation_pipeline
    # only ever reaches PUSHED after every configured test command
    # exited 0 (or none were configured), so that fact is reported
    # plainly rather than fabricating a pass/fail breakdown this app
    # never actually measured in structured form.
    test_results = (
        f"This CodeRun (`{code_run.id}`) reached PUSHED status, meaning every configured test command exited "
        "successfully (or none were configured). See the CodeRun's own logs for full test output."
    )
    jira_line = f"**{_JIRA_LINE_LABEL}:** {story.jira_issue_key}" if story.jira_issue_key else f"**{_JIRA_LINE_LABEL}:** Not yet synced to Jira."

    return (
        f"## Story Summary\n{story.user_story or story.title}\n\n"
        f"## Implementation Plan\n{implementation_plan_content or '(not available)'}\n\n"
        f"## Changed Files\n{changed_files}\n\n"
        f"## Test Results\n{test_results}\n\n"
        f"## Risks / Blockers\n{risks}\n\n"
        f"{jira_line}\n\n"
        f"---\n_Opened from CodeRun `{code_run.id}` — branch `{code_run.branch_name}`._"
    )


def create_pull_request_from_code_run(
    db: Session, *, code_run: CodeRun, implementation_run: ImplementationRun, story: Story, repository: Repository,
    base_branch: str, github_token: str, triggered_by: User,
) -> PullRequestLink:
    """Requirement 1 — create the PR; requirements 2/3 — its title/body;
    requirement 4 — store number/URL/branches/status, linked to project/
    story/lane/Jira key/implementation run/code run; requirement 5 —
    advance the lane where it's actually reachable (see module docstring).

    RULE — "PR creation requires pushed branch."
    """
    if code_run.status != CodeRunStatus.PUSHED:
        raise StoryCodeImplementationError(f"CodeRun {code_run.id} is {code_run.status.value}, not PUSHED — cannot create a pull request yet.")
    existing = db.query(PullRequestLink).filter(PullRequestLink.code_run_id == code_run.id).first()
    if existing is not None:
        raise StoryCodeImplementationError(f"CodeRun {code_run.id} already has a pull request: {existing.pr_url}")

    lane = story.delivery_lane
    plan_artifact = (
        db.query(StoryArtifact)
        .filter(StoryArtifact.story_id == story.id, StoryArtifact.artifact_type == STORY_IMPLEMENTATION_PLAN_ARTIFACT_TYPE)
        .order_by(StoryArtifact.version_number.desc())
        .first()
    )
    plan_content = plan_artifact.content_markdown if plan_artifact is not None else ""

    title = _build_pr_title(story)
    body = _build_pr_body(story=story, implementation_run=implementation_run, implementation_plan_content=plan_content, code_run=code_run)

    from app.services import github_integration as github_api

    try:
        pr = github_api.create_pull_request(
            github_token, repository.owner, repository.name, title=title, head=code_run.branch_name, base=base_branch, body=body,
        )
    except GitHubIntegrationError as exc:
        raise StoryCodeImplementationError(f"GitHub pull request creation failed: {exc}") from exc

    link = PullRequestLink(
        project_id=code_run.project_id,
        implementation_task_id=implementation_run.implementation_task_id,
        implementation_run_id=implementation_run.id,
        repository_id=repository.id,
        story_id=story.id,
        lane_id=lane.id if lane is not None else None,
        code_run_id=code_run.id,
        jira_issue_key=story.jira_issue_key,
        branch_name=code_run.branch_name,
        base_branch=base_branch,
        pr_number=pr.number,
        pr_url=pr.html_url,
        status=PullRequestStatus.OPEN,
        created_by_agent=True,
        triggered_by_user_id=triggered_by.id,
        commit_message=title,
    )
    db.add(link)
    db.flush()

    if lane is not None:
        _complete_pull_request_node_if_reachable(db, lane=lane, story=story, link=link, triggered_by_user_id=triggered_by.id)

    # SECURITY: never the token — only non-secret PR metadata.
    record_audit_log(
        db, project_id=code_run.project_id, actor_user_id=triggered_by.id, action="pull_request.created_from_code_run",
        entity_type="PullRequestLink", entity_id=link.id,
        extra_data={
            "story_id": str(story.id), "code_run_id": str(code_run.id), "jira_issue_key": story.jira_issue_key,
            "pr_number": pr.number, "pr_url": pr.html_url, "branch_name": code_run.branch_name, "base_branch": base_branch,
        },
    )
    return link


def _complete_pull_request_node_if_reachable(
    db: Session, *, lane, story: Story, link: PullRequestLink, triggered_by_user_id: uuid.UUID | None,
) -> None:
    """Requirement 5 — "Update story lane node to PR_REVIEW_READY." See
    the module docstring's disclosed scope: only advances the
    PULL_REQUEST node when it isn't LOCKED — never forces the lane's own
    sequence out of order."""
    pull_request_node = next((n for n in lane.nodes if n.node_key == "PULL_REQUEST"), None)
    if pull_request_node is None or pull_request_node.status in (StoryDeliveryNodeStatus.LOCKED, StoryDeliveryNodeStatus.COMPLETED):
        return

    pull_request_node.status = StoryDeliveryNodeStatus.COMPLETED
    pull_request_node.completed_at = datetime.now(timezone.utc)
    db.add(
        StoryActivityLog(
            story_id=story.id, lane_id=lane.id, node_id=pull_request_node.id, action="story_lane_node.status_changed",
            actor_user_id=triggered_by_user_id,
            details={"node_key": "PULL_REQUEST", "to": "COMPLETED", "pr_url": link.pr_url},
        )
    )
    advance_lane(db, lane=lane, completed_node=pull_request_node)
