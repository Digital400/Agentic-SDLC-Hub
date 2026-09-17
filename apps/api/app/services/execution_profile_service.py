"""ProjectExecutionProfileService — propose (from detection or from an
approved template), approve, and reject ProjectExecutionProfile versions;
plus the gate a coding runtime must pass before it may start.

VERSIONING (see app/models/project_execution_profile.py's class docstring):
every propose_* call creates a brand-new row with version =
(this project's current max version) + 1 — never edits an existing row.
Approving a version demotes whatever was previously APPROVED+is_active for
this project to SUPERSEDED, in the same transaction, so "at most one
active approved profile per project" holds even under concurrent-looking
sequential calls (this codebase has no cross-request locking — same
caveat every other is_active-style invariant here already carries, e.g.
AgentPrompt's).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.models import (
    Project,
    ProjectExecutionProfile,
    ProjectExecutionProfileSource,
    ProjectExecutionProfileStatus,
    ProjectExecutionProfileType,
    Repository,
    RepositorySnapshot,
    User,
)
from app.services.audit import record_audit_log
from app.services.execution_profile_detection import detect_execution_profile
from app.services.execution_profile_templates import ExecutionProfileTemplateError, get_approved_template


class ExecutionProfileError(Exception):
    """A caller/state error (bad ids, wrong status for the requested
    transition) — not a runtime failure."""


class CodingRuntimeBlockedError(Exception):
    """Raised by `require_active_profile_for_runtime_start` — see that
    function's docstring. Carries a human-readable `reason` a caller can
    surface directly (e.g. as an HTTP 409 detail)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _next_version(db: Session, project_id: uuid.UUID) -> int:
    current_max = (
        db.query(ProjectExecutionProfile.version)
        .filter(ProjectExecutionProfile.project_id == project_id)
        .order_by(ProjectExecutionProfile.version.desc())
        .limit(1)
        .scalar()
    )
    return (current_max or 0) + 1


class ProjectExecutionProfileService:
    def __init__(self, db: Session):
        self.db = db

    # --- EXISTING_REPOSITORY flow: detect and propose --------------------------------

    def propose_from_detection(
        self,
        *,
        project: Project,
        snapshot: RepositorySnapshot,
        triggered_by: User,
        github_token: str | None,
        transport: httpx.BaseTransport | None = None,
    ) -> ProjectExecutionProfile:
        """Runs safe, read-only detection (see
        app/services/execution_profile_detection.py — reads config files,
        never executes anything) against `snapshot`, and persists the
        result as a new DRAFT version, then immediately submits it for
        approval (PENDING_APPROVAL) — "detect and propose" is one action
        per this phase's instructions, not two separate steps a caller
        must remember to chain."""
        repository: Repository = snapshot.repository
        if repository.project_id != project.id:
            raise ExecutionProfileError(f"Repository {repository.id} does not belong to project {project.id}.")

        result = detect_execution_profile(snapshot=snapshot, github_token=github_token, transport=transport)

        profile = ProjectExecutionProfile(
            project_id=project.id,
            version=_next_version(self.db, project.id),
            status=ProjectExecutionProfileStatus.PENDING_APPROVAL,
            is_active=False,
            profile_type=ProjectExecutionProfileType.EXISTING_REPOSITORY,
            source=ProjectExecutionProfileSource.DETECTED,
            repository_id=repository.id,
            repository_snapshot_id=snapshot.id,
            default_branch=result.default_branch,
            working_directories=result.working_directories,
            detected_languages=result.detected_languages,
            detected_frameworks=result.detected_frameworks,
            package_manager=result.package_manager,
            runtime_image=result.runtime_image,
            install_command=result.install_command,
            lint_command=result.lint_command,
            format_check_command=result.format_check_command,
            type_check_command=result.type_check_command,
            unit_test_command=result.unit_test_command,
            integration_test_command=result.integration_test_command,
            build_command=result.build_command,
            approved_security_scan_commands=result.approved_security_scan_commands,
            allowed_command_patterns=result.allowed_command_patterns,
            denied_command_patterns=result.denied_command_patterns,
            allowed_paths=result.allowed_paths,
            denied_paths=result.denied_paths,
            network_policy={"default": "DENY", "allowed_domains": [], "denied_domains": []},
            environment_variable_names=result.environment_variable_names,
            branch_naming_convention="feature/<ticket-id>-<short-description>",
            commit_convention="Conventional Commits (https://www.conventionalcommits.org)",
            required_coding_skills=result.required_coding_skills,
            required_testing_skills=result.required_testing_skills,
            quality_gates=self._default_quality_gates(result.unit_test_command, result.lint_command),
            data_classification=result.data_classification,
            detection_metadata=result.detection_metadata,
            created_by_id=triggered_by.id,
            submitted_at=datetime.now(timezone.utc),
        )
        self.db.add(profile)
        self.db.flush()

        record_audit_log(
            self.db, project_id=project.id, actor_user_id=triggered_by.id, action="project_execution_profile.proposed",
            entity_type="ProjectExecutionProfile", entity_id=profile.id,
            extra_data={
                "profile_type": "EXISTING_REPOSITORY", "source": "DETECTED", "version": profile.version,
                "repository_snapshot_id": str(snapshot.id), "detected_languages": result.detected_languages,
            },
        )
        return profile

    # --- NEW_PROJECT flow: select an approved template and propose -------------------

    def propose_from_template(
        self, *, project: Project, template_key: str, triggered_by: User, default_branch_override: str | None = None,
    ) -> ProjectExecutionProfile:
        """NEW_PROJECT flow — `template_key` must name one of
        app/services/execution_profile_templates.py's
        APPROVED_STACK_TEMPLATES; anything else raises
        ExecutionProfileTemplateError (a 400 at the route layer). Also
        submits immediately (PENDING_APPROVAL), same as
        propose_from_detection — see that method's docstring."""
        template = get_approved_template(template_key)  # raises ExecutionProfileTemplateError on an unknown key

        profile = ProjectExecutionProfile(
            project_id=project.id,
            version=_next_version(self.db, project.id),
            status=ProjectExecutionProfileStatus.PENDING_APPROVAL,
            is_active=False,
            profile_type=ProjectExecutionProfileType.NEW_PROJECT,
            source=ProjectExecutionProfileSource.TEMPLATE,
            template_key=template.template_key,
            default_branch=default_branch_override or template.default_branch,
            working_directories=list(template.working_directories),
            detected_languages=list(template.languages),
            detected_frameworks=list(template.frameworks),
            package_manager=template.package_manager,
            runtime_image=template.runtime_image,
            install_command=template.install_command,
            lint_command=template.lint_command,
            format_check_command=template.format_check_command,
            type_check_command=template.type_check_command,
            unit_test_command=template.unit_test_command,
            integration_test_command=template.integration_test_command,
            build_command=template.build_command,
            approved_security_scan_commands=list(template.approved_security_scan_commands),
            allowed_command_patterns=[c for c in (
                template.install_command, template.lint_command, template.format_check_command,
                template.type_check_command, template.unit_test_command, template.build_command,
            ) if c],
            denied_command_patterns=["rm -rf *", "sudo *", "curl * | sh", "wget * | sh", ": > *", "git push --force *"],
            allowed_paths=list(template.working_directories) or ["."],
            denied_paths=[".env", ".env.*", "**/.git/**", "**/node_modules/**", "**/.venv/**", "**/secrets/**"],
            network_policy={"default": "DENY", "allowed_domains": [], "denied_domains": []},
            environment_variable_names=[],
            branch_naming_convention=template.branch_naming_convention,
            commit_convention=template.commit_convention,
            required_coding_skills=list(template.required_coding_skills),
            required_testing_skills=list(template.required_testing_skills),
            quality_gates=self._default_quality_gates(template.unit_test_command, template.lint_command),
            data_classification=template.data_classification,
            detection_metadata={},
            created_by_id=triggered_by.id,
            submitted_at=datetime.now(timezone.utc),
        )
        self.db.add(profile)
        self.db.flush()

        record_audit_log(
            self.db, project_id=project.id, actor_user_id=triggered_by.id, action="project_execution_profile.proposed",
            entity_type="ProjectExecutionProfile", entity_id=profile.id,
            extra_data={"profile_type": "NEW_PROJECT", "source": "TEMPLATE", "version": profile.version, "template_key": template.template_key},
        )
        return profile

    @staticmethod
    def _default_quality_gates(unit_test_command: str | None, lint_command: str | None) -> list[dict]:
        gates = []
        if unit_test_command:
            gates.append({"name": "unit-tests-pass", "description": "The unit test command must exit successfully.", "severity": "BLOCKING", "command": unit_test_command})
        if lint_command:
            gates.append({"name": "lint-clean", "description": "The lint command must report no errors.", "severity": "ADVISORY", "command": lint_command})
        return gates

    # --- Approval lifecycle (Project Owner) -------------------------------------------

    def approve(self, *, profile: ProjectExecutionProfile, approved_by: User) -> ProjectExecutionProfile:
        """Approves `profile` and demotes whatever was previously the
        active approved profile for its project to SUPERSEDED — see
        module docstring. Requires PENDING_APPROVAL (or DRAFT, for a
        profile a caller submits and approves in one step) — anything
        else (already APPROVED/REJECTED/SUPERSEDED) is a caller error.

        Caller (the route) is responsible for the actual role check —
        see app/services/permissions.py's require_can_approve_execution_profile
        — this method only enforces the STATE transition, matching how
        app/services/graph_engine.py's own mark_* methods stay separate
        from app/services/permissions.py's role checks.
        """
        if profile.status not in (ProjectExecutionProfileStatus.DRAFT, ProjectExecutionProfileStatus.PENDING_APPROVAL):
            raise ExecutionProfileError(f"Profile {profile.id} is {profile.status.value}; only DRAFT or PENDING_APPROVAL profiles can be approved.")

        previously_active = (
            self.db.query(ProjectExecutionProfile)
            .filter(
                ProjectExecutionProfile.project_id == profile.project_id,
                ProjectExecutionProfile.status == ProjectExecutionProfileStatus.APPROVED,
                ProjectExecutionProfile.is_active.is_(True),
            )
            .first()
        )
        if previously_active is not None:
            previously_active.status = ProjectExecutionProfileStatus.SUPERSEDED
            previously_active.is_active = False

        profile.status = ProjectExecutionProfileStatus.APPROVED
        profile.is_active = True
        profile.approved_by_id = approved_by.id
        profile.approved_at = datetime.now(timezone.utc)

        record_audit_log(
            self.db, project_id=profile.project_id, actor_user_id=approved_by.id, action="project_execution_profile.approved",
            entity_type="ProjectExecutionProfile", entity_id=profile.id,
            extra_data={
                "version": profile.version,
                "superseded_version": previously_active.version if previously_active is not None else None,
            },
        )
        return profile

    def reject(self, *, profile: ProjectExecutionProfile, rejected_by: User, reason: str) -> ProjectExecutionProfile:
        if profile.status not in (ProjectExecutionProfileStatus.DRAFT, ProjectExecutionProfileStatus.PENDING_APPROVAL):
            raise ExecutionProfileError(f"Profile {profile.id} is {profile.status.value}; only DRAFT or PENDING_APPROVAL profiles can be rejected.")

        profile.status = ProjectExecutionProfileStatus.REJECTED
        profile.is_active = False
        profile.rejected_by_id = rejected_by.id
        profile.rejected_at = datetime.now(timezone.utc)
        profile.rejection_reason = reason

        record_audit_log(
            self.db, project_id=profile.project_id, actor_user_id=rejected_by.id, action="project_execution_profile.rejected",
            entity_type="ProjectExecutionProfile", entity_id=profile.id,
            extra_data={"version": profile.version, "reason": reason},
        )
        return profile

    # --- Read paths --------------------------------------------------------------------

    def get_active_profile(self, project_id: uuid.UUID) -> ProjectExecutionProfile | None:
        return (
            self.db.query(ProjectExecutionProfile)
            .filter(
                ProjectExecutionProfile.project_id == project_id,
                ProjectExecutionProfile.status == ProjectExecutionProfileStatus.APPROVED,
                ProjectExecutionProfile.is_active.is_(True),
            )
            .first()
        )

    def list_versions(self, project_id: uuid.UUID) -> list[ProjectExecutionProfile]:
        """The full version/audit history for one project, newest first —
        every DRAFT/PENDING_APPROVAL/APPROVED/REJECTED/SUPERSEDED row ever
        created, immutable, exactly like AgentPrompt's own version list."""
        return (
            self.db.query(ProjectExecutionProfile)
            .filter(ProjectExecutionProfile.project_id == project_id)
            .order_by(ProjectExecutionProfile.version.desc())
            .all()
        )


# --- The coding-runtime gate --------------------------------------------------------


def require_active_profile_for_runtime_start(db: Session, project_id: uuid.UUID) -> ProjectExecutionProfile:
    """"A coding runtime cannot start without an approved active profile"
    — this is the one function that rule compiles down to. Raises
    CodingRuntimeBlockedError (never returns None) when no
    ProjectExecutionProfile for this project is both APPROVED and
    is_active — the caller (see
    app/api/routes/implementation_runs.py's start_implementation_run,
    which is this codebase's closest existing thing to "a coding runtime
    start" — see docs/architecture/universal-agent-runtime-baseline.md
    section 7) is expected to surface CodingRuntimeBlockedError.reason as
    a 409, the same status every other graph/gate precondition failure in
    this codebase already uses.
    """
    profile = ProjectExecutionProfileService(db).get_active_profile(project_id)
    if profile is None:
        raise CodingRuntimeBlockedError(
            f"No approved, active ProjectExecutionProfile exists for project {project_id} — "
            "propose one (detect from a repository, or select an approved template) and get "
            "Project Owner approval before starting a coding runtime."
        )
    return profile
