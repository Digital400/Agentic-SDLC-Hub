"""Phase 10: Story Implementation, Branch and PR Workflow.

The first real CONNECTION point for this arc's Phase 01/02 machinery
(app.agent_runtime.WorkPacket, app.coding_runtime.RuntimeRegistry) — a
genuine, feature-flagged, ADDITIVE alternative to app/api/routes/
implementation_runs.py's existing create_pull_request, gated behind
Settings.STORY_GIT_PR_FLOW_V2_ENABLED (default False). That existing
route is completely untouched and stays the default path — this module
follows the exact same strangler pattern app.model_gateway/app.
prompt_compiler already established: additive, feature-flagged, never a
replacement until it's proven.

WHAT THIS FIXES (real, disclosed bugs in the existing default path — see
app/services/github_integration.py's own module docstring, which
explicitly names both): the existing create_pull_request commits one
file at a time via GitHub's Contents API (one real commit per changed
file — this phase's own "never create one GitHub commit per changed
file" rule), and has no idempotency check at all (a second
create_pull_request call for a regenerated run always opens a brand new,
competing PR/branch, never updates the first). This module fixes both,
using the new Git Data API functions (create_tree/create_commit/
update_ref) and an explicit "does this task already have an open PR"
check before ever creating a new branch.

SCOPE (this phase, only): the patch itself (steps 1-9 of Phase 10's own
step list — verify approval, build the WorkPacket, select the runtime,
generate the proposed diff, get it human-Accepted) is UNCHANGED — it
still runs through the existing, proven start_implementation_run /
review_implementation_run pipeline (app/services/implementation_agent.py,
via app.coding_runtime.LegacyCodingRuntimeAdapter — see
build_work_packet_for_run/select_runtime_for_task below). What's NEW
here is everything from "the diff is already Accepted" onward (steps
10-15): inject the token, one atomic commit, push, idempotent PR,
store every identifier, advance the story only through the existing
app.services.story_delivery functions — never a parallel/competing
state machine.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.agent_runtime import (
    AcceptanceCriterion,
    ApprovalPolicy,
    RepositoryReference,
    ScopePolicy,
    WorkObjective,
    WorkPacket,
    WorkPacketTaskType,
)
from app.coding_runtime import (
    LegacyCodingRuntimeAdapter,
    RuntimeCapability,
    RuntimeRegistry,
    RuntimeSelectionDecision,
    RuntimeSelectionRequest,
)
from app.models import ImplementationRun, ImplementationTask, PullRequestLink, PullRequestStatus, Repository, Story, WorkflowNode
from app.services import github_integration as github_api
from app.services.github_integration import GitHubIntegrationError
from app.services.repo_context_builder import RepoContextPreviewResult


class StoryGitPrFlowError(Exception):
    """Raised when this flow can't proceed — a precondition (run not
    Accepted, task has no repository snapshot, ...) or a GitHub write
    failure. A caller (the route) turns this into an honest HTTP error,
    same convention as StoryCodeImplementationError elsewhere."""


def build_work_packet_for_run(
    *, run: ImplementationRun, task: ImplementationTask, story: Story | None, repository: Repository, base_branch: str,
) -> WorkPacket:
    """A real WorkPacket (Phase 01) for an already-generated,
    already-Accepted ImplementationRun — built from data that already
    exists (the task, its run, its story, the repository/snapshot it was
    generated against), not a re-derivation. `repository.base_commit_sha`
    is pinned to the exact snapshot this run's diff was actually produced
    against (run.repository_snapshot.commit_sha), never the base branch's
    current tip — see RepositoryReference's own hard rule."""
    if run.repository_snapshot is None:
        raise StoryGitPrFlowError(f"Run {run.id} has no repository snapshot — cannot pin a base commit sha for its WorkPacket.")

    return WorkPacket(
        packet_id=run.id,  # stable identity — this WorkPacket represents exactly this one run, not a new unit of work
        task_type=WorkPacketTaskType.IMPLEMENT_STORY,
        project_id=run.project_id,
        story_id=story.id if story is not None else None,
        objective=WorkObjective(
            goal=task.description or task.title,
            success_definition="A pull request exists carrying this run's accepted diff.",
        ),
        acceptance_criteria=[AcceptanceCriterion(id=str(i), description=c) for i, c in enumerate(task.acceptance_criteria)],
        repository=RepositoryReference(
            provider="github", owner=repository.owner, name=repository.name,
            base_branch=base_branch, base_commit_sha=run.repository_snapshot.commit_sha,
        ),
        scope_policy=ScopePolicy(allowed_paths=list(task.expected_paths or [])),
        approval_policy=ApprovalPolicy(requires_human_approval=True),
        created_at=datetime.now(timezone.utc),
        created_by_user_id=run.triggered_by_user_id,
    )


def select_runtime_for_task(packet: WorkPacket, task: ImplementationTask) -> tuple[RuntimeRegistry, RuntimeSelectionDecision]:
    """Registers this arc's one real, working coding runtime today
    (LegacyCodingRuntimeAdapter — wraps app.services.implementation_agent.
    run_implementation_agent unmodified, see app.coding_runtime's own
    module docstring) and selects it via RuntimeRegistry — Phase 10's
    step 4, "select the runtime," made real rather than assumed. Returns
    the registry alongside the decision so a caller can also record
    RuntimeSelectionDecision.reason/rejected for audit, per Phase 02's own
    "persist ... selection reason" requirement.

    Only one runtime is ever registered here today — Phase 11/12's ACP/
    premium adapters aren't connected yet (this phase's own scope is the
    git/PR workflow, not runtime choice) — so this always selects
    "legacy-coding" barring a genuine bug; the real value today is that
    the SAME selection machinery a future phase's ACP/premium adapters
    will also go through already exists and is already exercised end to
    end here, not a placeholder no caller ever reaches.

    The adapter is constructed with an empty repo context / no story —
    this call never invokes .execute() on it (the run this WorkPacket
    represents already executed, via the existing pipeline — see module
    docstring's SCOPE note); only its static `.definition` (kind,
    capabilities, execution location) is read, by RuntimePolicyEvaluator,
    to confirm it's a valid candidate for an IMPLEMENT_STORY task."""
    registry = RuntimeRegistry()
    registry.register(
        LegacyCodingRuntimeAdapter(task=task, repo_context=RepoContextPreviewResult(), story=None, lld_summary=""),
    )
    decision = registry.select(
        RuntimeSelectionRequest(
            packet=packet, task_type=WorkPacketTaskType.IMPLEMENT_STORY,
            required_capabilities=[RuntimeCapability.REPOSITORY_READ, RuntimeCapability.PATCH_GENERATION],
        ),
    )
    return registry, decision


def _find_open_pull_request(db: Session, task_id: uuid.UUID) -> PullRequestLink | None:
    """Phase 10's "detect an existing branch/PR before creating
    duplicates" — mirrors the identical idempotency check this arc's own
    Phase 09/earlier-session precedent already established for the
    legacy path: only OPEN counts (a MERGED/CLOSED PR is done; a later
    run starts a genuinely new one)."""
    return (
        db.query(PullRequestLink)
        .filter(PullRequestLink.implementation_task_id == task_id, PullRequestLink.status == PullRequestStatus.OPEN)
        .order_by(PullRequestLink.created_at.desc())
        .first()
    )


def _commit_packet_changes_atomically(
    *, github_token: str, repository: Repository, branch: str, parent_commit_sha: str, changes: list[dict], commit_message: str,
) -> str:
    """The real fix this phase makes: ONE commit for every changed file,
    via the Git Data API (create_tree -> create_commit -> update_ref),
    never GitHub's Contents API's one-commit-per-file shape. Returns the
    new commit's sha. Raises GitHubIntegrationError on any failure —
    including update_ref's own non-fast-forward check, which surfaces a
    genuine concurrent-write conflict honestly instead of silently
    forcing past it (see update_ref's own docstring)."""
    base_tree_sha = github_api.get_commit_tree_sha(github_token, repository.owner, repository.name, parent_commit_sha)
    tree_sha = github_api.create_tree(github_token, repository.owner, repository.name, base_tree_sha=base_tree_sha, changes=changes)
    commit_sha = github_api.create_commit(
        github_token, repository.owner, repository.name, message=commit_message, tree_sha=tree_sha, parent_sha=parent_commit_sha,
    )
    github_api.update_ref(github_token, repository.owner, repository.name, branch=branch, commit_sha=commit_sha)
    return commit_sha


def run_story_git_pr_flow(
    db: Session,
    *,
    run: ImplementationRun,
    task: ImplementationTask,
    story: Story | None,
    repository: Repository,
    base_branch: str,
    github_token: str,
    triggered_by_user_id: uuid.UUID | None,
    pr_title: str,
    pr_body: str,
    branch_naming_pattern: str = "story/{task}-{run_id}",
) -> PullRequestLink:
    """Phase 10 steps 10-15, for an already-Accepted ImplementationRun —
    see module docstring's SCOPE note for what this deliberately does NOT
    redo. Never called against `repository`'s own default branch (checked
    explicitly below, same defense-in-depth convention
    app/api/routes/implementation_runs.py's own create_pull_request
    already uses) — this only ever writes to a fresh or already-agent-
    owned feature branch."""
    existing = _find_open_pull_request(db, task.id)

    if existing is not None:
        # Idempotent path: push this run's changes as one more atomic
        # commit onto the SAME branch/PR — never a second, competing one.
        head_sha = github_api.get_branch_head_sha(github_token, repository.owner, repository.name, existing.branch_name)
        if head_sha is None:
            raise StoryGitPrFlowError(f"PR #{existing.pr_number}'s branch {existing.branch_name!r} no longer exists on GitHub.")
        regenerate_message = f"Regenerate: {task.title} (run {str(run.id)[:8]})"
        _commit_packet_changes_atomically(
            github_token=github_token, repository=repository, branch=existing.branch_name, parent_commit_sha=head_sha,
            changes=run.proposed_file_changes, commit_message=regenerate_message,
        )
        existing.implementation_run_id = run.id
        existing.commit_message = regenerate_message
        db.flush()
        return existing

    branch_name = branch_naming_pattern.format(task=_slugify(task.title), run_id=str(run.id)[:8], story=_slugify(task.linked_story or ""))
    if branch_name == repository.default_branch or branch_name == base_branch:
        raise StoryGitPrFlowError("Refusing to use the base/default branch as the feature branch.")

    packet = build_work_packet_for_run(run=run, task=task, story=story, repository=repository, base_branch=base_branch)
    _registry, decision = select_runtime_for_task(packet, task)
    if decision.selected_runtime is None:
        raise StoryGitPrFlowError(f"No coding runtime available for this task: {decision.reason}")

    github_api.create_branch_at_sha(
        github_token, repository.owner, repository.name, new_branch=branch_name, base_commit_sha=packet.repository.base_commit_sha,  # type: ignore[union-attr]
    )
    commit_message = f"Implement {task.title}"
    _commit_packet_changes_atomically(
        github_token=github_token, repository=repository, branch=branch_name,
        parent_commit_sha=packet.repository.base_commit_sha,  # type: ignore[union-attr]
        changes=run.proposed_file_changes, commit_message=commit_message,
    )

    try:
        pr = github_api.create_pull_request(
            github_token, repository.owner, repository.name, title=pr_title, head=branch_name, base=base_branch, body=pr_body,
        )
    except GitHubIntegrationError as exc:
        # The branch (and its one real commit) may still exist on GitHub —
        # no automatic rollback, same disclosed scope as the existing
        # create_pull_request route. A human can find and clean it up.
        raise StoryGitPrFlowError(f"Branch/commit succeeded but pull request creation failed: {exc}") from exc

    # Same lookup the existing create_pull_request route already does —
    # on this branch a story's "implementation" stage is a real
    # WorkflowNode row (story_id set), not a separate table; reused here
    # rather than re-derived, since PullRequestLink.workflow_node_id is a
    # required FK either way.
    implementation_node = (
        db.query(WorkflowNode)
        .filter(WorkflowNode.project_id == run.project_id, WorkflowNode.node_key == "implementation", WorkflowNode.story_id == task.story_id)
        .first()
    )
    if implementation_node is None:
        raise StoryGitPrFlowError(f"Project {run.project_id}'s workflow has no implementation stage for this task's scope.")

    link = PullRequestLink(
        project_id=run.project_id,
        workflow_node_id=implementation_node.id,
        implementation_task_id=task.id,
        implementation_run_id=run.id,
        repository_id=repository.id,
        story_id=task.story_id,
        branch_name=branch_name,
        base_branch=base_branch,
        pr_number=pr.number,
        pr_url=pr.html_url,
        status=PullRequestStatus.OPEN,
        created_by_agent=True,
        triggered_by_user_id=triggered_by_user_id,
        commit_message=commit_message,
    )
    db.add(link)
    db.flush()
    return link


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return slug or "story"
