"""Role-based permission foundation for the SDLC workflow's stage actions.

No authentication exists yet (see docs/mvp-plan.md) — there's no session or
token to derive "the current user" from. Every check here instead takes the
explicit actor id already present on the mutating request (created_by_id,
triggered_by_user_id, or a review's own reviewer_id) — the same pattern
this codebase already uses everywhere else attribution is needed. This is
a real authorization layer over that actor, not just an audit label: a
request naming a user without the right role is rejected with 403, not
merely logged. Swap the actor-lookup for a real "current user from session"
later — nothing here assumes the actor came from a request body forever.

Only two rules are explicitly specified end-to-end by the product request
(BA edits requirement/problem; the five approval rules); every other
stage's edit/approve roles below are a reasonable inferred default,
flagged inline — revisit if the product spec ever states them explicitly.
"""

from fastapi import HTTPException, status

from app.models import User, UserRole

# --- Who may create/edit each stage's artifact -------------------------------------
#
# Explicit from the product spec: BA for requirement_intake/problem_discovery.
# Everything else below is an inferred default (not yet specified), chosen
# to mirror who naturally owns that stage's output.
STAGE_EDIT_ROLES: dict[str, set[UserRole]] = {
    "requirement_intake": {UserRole.BA},
    "problem_discovery": {UserRole.BA},
    "solution_discovery": {UserRole.BA, UserRole.ARCHITECT},  # inferred default
    "hld": {UserRole.ARCHITECT},  # inferred default
    "story_crafting": {UserRole.BA, UserRole.PRODUCT_OWNER},  # inferred default
    "lld": {UserRole.ARCHITECT, UserRole.TECH_LEAD},  # inferred default
    "implementation_planning": {UserRole.ARCHITECT, UserRole.TECH_LEAD},  # inferred default — same owners as lld
    "infrastructure_planning": {UserRole.DEVOPS, UserRole.ARCHITECT},  # inferred default — co-owned, like lld
    "implementation": {UserRole.DEVELOPER, UserRole.TECH_LEAD},  # inferred default
    "pr_review": {UserRole.DEVELOPER, UserRole.TECH_LEAD},  # inferred default — same owners as implementation
    "testing": {UserRole.QA},  # inferred default
    "infrastructure": {UserRole.DEVOPS},  # inferred default
    "release": {UserRole.DEVOPS},  # inferred default
    "maintenance": {UserRole.DEVOPS, UserRole.DEVELOPER},  # inferred default
    # --- Scrum story lanes (workflows/scrum-story-lanes-*.json) ---
    "story_lld": {UserRole.ARCHITECT, UserRole.TECH_LEAD},  # inferred default — same owners as lld
    "sprint_planning": {UserRole.PRODUCT_OWNER},  # inferred default
    "release_planning": {UserRole.PRODUCT_OWNER, UserRole.DEVOPS},  # inferred default
}

# --- Who may approve (review-decide) each stage's artifact -------------------------
#
# All five rows are explicit from the product spec.
STAGE_APPROVE_ROLES: dict[str, set[UserRole]] = {
    "requirement_intake": {UserRole.PRODUCT_OWNER},
    "problem_discovery": {UserRole.PRODUCT_OWNER},
    "solution_discovery": {UserRole.PRODUCT_OWNER},
    "story_crafting": {UserRole.PRODUCT_OWNER},
    "hld": {UserRole.ARCHITECT},
    "lld": {UserRole.TECH_LEAD},
    "implementation_planning": {UserRole.TECH_LEAD},
    "infrastructure_planning": {UserRole.DEVOPS},  # rule: DevOps approval is required
    "implementation": {UserRole.TECH_LEAD},
    "pr_review": {UserRole.TECH_LEAD},
    "testing": {UserRole.QA},
    "infrastructure": {UserRole.DEVOPS},
    "release": {UserRole.DEVOPS},
    # --- Scrum story lanes (workflows/scrum-story-lanes-*.json) ---
    "story_lld": {UserRole.TECH_LEAD},
    "release_planning": {UserRole.PRODUCT_OWNER},
}

# Project-level and prompt-level actions aren't tied to a workflow stage,
# so they get a flat role set instead of a per-stage table. Not specified
# by the product request — inferred defaults, see module docstring.
PROJECT_UPDATE_ROLES: set[UserRole] = {UserRole.PRODUCT_OWNER}  # inferred default
PROMPT_UPDATE_ROLES: set[UserRole] = set()  # inferred default: Admin only (via the bypass below)
# A manual override bypasses every graph rule (see
# app/services/graph_engine.py) — Admin only, same inferred-default
# reasoning as prompt updates.
WORKFLOW_NODE_OVERRIDE_ROLES: set[UserRole] = set()


def _require_role(user: User, allowed_roles: set[UserRole], action: str) -> None:
    """ADMIN always passes — see UserRole's docstring. VIEWER never appears
    in any allowed set above, so it's denied everywhere by construction,
    matching "Viewer can only read" without needing a special case here."""
    if user.role == UserRole.ADMIN:
        return
    if user.role not in allowed_roles:
        allowed_label = ", ".join(sorted(r.value for r in allowed_roles)) or "Admin only"
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Role {user.role.value} may not {action} (allowed: {allowed_label}).",
        )


def require_can_edit_stage(user: User, node_key: str) -> None:
    """Create/edit an artifact for this workflow stage — also covers
    starting an agent run against it, since a run just produces a draft, an
    editing-class action."""
    _require_role(user, STAGE_EDIT_ROLES.get(node_key, set()), f"create or edit a '{node_key}' document")


def require_can_approve_stage(user: User, node_key: str) -> None:
    """Approve/request-changes/reject a review for this workflow stage."""
    _require_role(user, STAGE_APPROVE_ROLES.get(node_key, set()), f"decide a '{node_key}' review")


def require_can_update_project(user: User) -> None:
    _require_role(user, PROJECT_UPDATE_ROLES, "update this project")


def require_can_update_prompt(user: User) -> None:
    _require_role(user, PROMPT_UPDATE_ROLES, "update agent prompts")


def require_can_override_node(user: User) -> None:
    _require_role(user, WORKFLOW_NODE_OVERRIDE_ROLES, "manually override a workflow node's status")
