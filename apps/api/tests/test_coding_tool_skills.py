"""Use-your-own-coding-tool skills: pack generation, install-by-PR, and sync
back (app/services/coding_tool_skills.py, stage_document_sync.py,
app/api/routes/coding_tools.py)."""

import json
import shutil
import subprocess
import uuid

import pytest
from fastapi import HTTPException

import app.api.routes.coding_tools as routes
from app.api.routes.coding_tools import install_skill_pack, preview_skill_pack, sync_stage
from app.models import (
    Artifact,
    ArtifactStatus,
    Integration,
    IntegrationConnection,
    IntegrationProvider,
    IntegrationStatus,
    Repository,
    User,
    UserRole,
    WorkflowStatus,
)
from app.schemas.coding_tools import InstallSkillsRequest, SyncStageRequest
from app.services import github_integration as github_api
from app.services.coding_tool_skills import TOOLS, build_skill_pack, parse_front_matter
from app.services.github_integration import GitHubFileContent, GitHubIntegrationError
from app.services.stage_document_sync import StageSyncError, sync_stage_document
from tests.conftest import make_approved_artifact, make_node

pytestmark = pytest.mark.usefixtures("db")


def _repo(db, project, default_branch="main"):
    integration = Integration(integration_name="GitHub", provider=IntegrationProvider.GITHUB, status=IntegrationStatus.CONNECTED)
    db.add(integration)
    db.flush()
    connection = IntegrationConnection(
        integration=integration, access_token_encrypted="x", token_last_four="7890", github_username="octocat", status=IntegrationStatus.CONNECTED
    )
    db.add(connection)
    db.flush()
    repo = Repository(project=project, connection=connection, owner="octocat", name="app", default_branch=default_branch, is_primary=True)
    db.add(repo)
    db.flush()
    return repo


def _dev(db) -> User:
    user = User(email=f"{uuid.uuid4()}@example.com", full_name="Dev", role=UserRole.DEVELOPER)
    db.add(user)
    db.flush()
    return user


def _intake_node(db, project, status=WorkflowStatus.READY):
    return make_node(db, project, node_key="requirement_intake", order_index=0, status=status, output_artifact_type="intake_summary")


# --- Skill packs --------------------------------------------------------------------------


def test_every_tool_gets_its_native_files(db, project):
    expected = {
        "claude_code": {".claude/commands/requirement-intake.md", ".claude/agents/sdlc-reviewer-requirement-intake.md", ".claude/settings.json"},
        "codex": {".sdlc/codex/requirement-intake.md", "AGENTS.md"},
        "opencode": {".opencode/command/requirement-intake.md", ".opencode/agent/sdlc-reviewer-requirement-intake.md", "AGENTS.md"},
        "cursor": {".cursor/commands/requirement-intake.md", ".cursor/rules/sdlc-stage-documents.mdc"},
    }
    assert set(expected) == set(TOOLS)
    for tool, paths in expected.items():
        pack = build_skill_pack(db, project, tool=tool, stage="requirement_intake")
        got = {f.path for f in pack.files}
        assert paths <= got, tool
        # shared files every tool gets
        assert {"docs/sdlc/context.md", "docs/sdlc/README.md", ".sdlc/hooks/validate-doc.mjs"} <= got
        assert pack.usage and all(f.content.strip() for f in pack.files)


def test_claude_code_uses_its_advanced_features(db, project):
    pack = build_skill_pack(db, project, tool="claude_code", stage="requirement_intake")
    files = {f.path: f for f in pack.files}
    command = files[".claude/commands/requirement-intake.md"].content
    assert "$ARGUMENTS" in command and "argument-hint:" in command and "allowed-tools:" in command
    assert all(tag in command for tag in ("<role>", "<input>", "<procedure>", "<quality_checklist>"))  # XML-tagged prompt
    assert "sdlc-reviewer" in command  # subagent
    settings = json.loads(files[".claude/settings.json"].content)
    hook = settings["hooks"]["PostToolUse"][0]
    assert "Write" in hook["matcher"] and "validate-doc.mjs" in hook["hooks"][0]["command"]  # hook loop
    assert files[".claude/settings.json"].managed is False  # never overwrite an existing settings file
    reviewer = files[".claude/agents/sdlc-reviewer-requirement-intake.md"].content
    assert "tools: Read, Grep, Glob" in reviewer and "Write" not in reviewer.split("---")[1]  # read-only


def test_other_tools_use_their_own_conventions(db, project):
    oc = {f.path: f.content for f in build_skill_pack(db, project, tool="opencode", stage="requirement_intake").files}
    assert "@docs/sdlc/context.md" in oc[".opencode/command/requirement-intake.md"]
    assert "mode: subagent" in oc[".opencode/agent/sdlc-reviewer-requirement-intake.md"]
    cx = build_skill_pack(db, project, tool="codex", stage="requirement_intake")
    assert any("~/.codex/prompts" in u for u in cx.usage)
    cu = {f.path: f.content for f in build_skill_pack(db, project, tool="cursor", stage="requirement_intake").files}
    assert "globs: docs/sdlc/**" in cu[".cursor/rules/sdlc-stage-documents.mdc"]


def test_pack_carries_project_specific_content(db, project):
    project.name = "Loyalty Portal"
    db.flush()
    pack = build_skill_pack(db, project, tool="claude_code", stage="requirement_intake")
    files = {f.path: f.content for f in pack.files}
    assert "Loyalty Portal" in files[".claude/commands/requirement-intake.md"]
    assert str(project.id) in files[".claude/commands/requirement-intake.md"]  # in the document template front matter
    assert "Loyalty Portal" in files["docs/sdlc/context.md"]
    assert "States who the stakeholder is" in files[".claude/commands/requirement-intake.md"]  # checklist


def test_preview_rejects_unknown_tool_and_stage(db, project):
    with pytest.raises(HTTPException) as e1:
        preview_skill_pack(project.id, tool="vim", stage="requirement_intake", db=db)
    with pytest.raises(HTTPException) as e2:
        preview_skill_pack(project.id, tool="codex", stage="release", db=db)
    assert e1.value.status_code == 400 and e2.value.status_code == 400


def test_front_matter_parsing():
    meta, body = parse_front_matter("---\nsdlc_stage: requirement_intake\nproject_id: abc\n---\n# Title\n\nText")
    assert meta == {"sdlc_stage": "requirement_intake", "project_id": "abc"} and body.startswith("# Title")
    assert parse_front_matter("no front matter") == ({}, "no front matter")


# --- Install by pull request ----------------------------------------------------------------


def _patch_github(monkeypatch, *, existing=None, create_branch_error=None, branches=("main",), existing_readme=None):
    existing = existing or {}
    calls = {"branch": [], "files": [], "pr": [], "content": {}}
    monkeypatch.setattr(github_api, "list_branches", lambda token, o, r, **kw: list(branches))
    monkeypatch.setattr(routes, "decrypt_repository_token", lambda repo: "tok")
    monkeypatch.setattr(github_api, "get_file_sha", lambda token, o, r, path, ref, **kw: existing.get(path))
    monkeypatch.setattr(
        github_api, "read_file",
        lambda token, o, r, path, ref, **kw: GitHubFileContent(path=path, sha="readsha", size=len(existing_readme or ""), content=existing_readme, truncated=False, is_binary=False),
    )

    def create_branch(token, o, r, *, new_branch, base_ref, **kw):
        calls["branch"].append((new_branch, base_ref))
        if create_branch_error:
            raise create_branch_error

    monkeypatch.setattr(github_api, "create_branch", create_branch)

    def create_or_update_file(token, o, r, path, *, content, message, branch, sha=None, **kw):
        calls["files"].append((path, branch, sha))
        calls["content"][path] = content
        return "c"

    monkeypatch.setattr(github_api, "create_or_update_file", create_or_update_file)

    class PR:
        html_url = "https://github.com/octocat/app/pull/7"

    monkeypatch.setattr(github_api, "create_pull_request", lambda *a, **kw: calls["pr"].append((kw["head"], kw["base"])) or PR())
    return calls


def test_install_commits_to_a_new_branch_and_opens_a_pr(db, project, monkeypatch):
    _repo(db, project)
    calls = _patch_github(monkeypatch)
    result = install_skill_pack(project.id, InstallSkillsRequest(tool="claude_code", triggered_by_user_id=_dev(db).id), db)

    branch, base = calls["branch"][0]
    assert base == "main" and branch != "main" and branch.startswith("sdlc/claude-code-requirement-intake-")
    assert all(b == branch for _, b, _ in calls["files"])  # never the default branch
    assert ".claude/commands/requirement-intake.md" in result.committed and result.pull_request_url.endswith("/pull/7")
    assert calls["pr"] == [(branch, "main")]


def test_install_never_overwrites_files_teams_edit_but_refreshes_managed_ones(db, project, monkeypatch):
    _repo(db, project)
    calls = _patch_github(monkeypatch, existing={".claude/settings.json": "sha1", "docs/sdlc/context.md": "sha2"})
    result = install_skill_pack(project.id, InstallSkillsRequest(tool="claude_code", triggered_by_user_id=_dev(db).id), db)

    assert [s.path for s in result.skipped] == [".claude/settings.json"]
    assert ".claude/settings.json" not in result.committed
    updated = {p: sha for p, _, sha in calls["files"]}
    assert updated["docs/sdlc/context.md"] == "sha2"  # managed file updated in place, with its existing sha


def test_install_creates_a_fresh_readme_with_the_shared_header_when_none_exists(db, project, monkeypatch):
    _repo(db, project)
    calls = _patch_github(monkeypatch, existing_readme=None)
    install_skill_pack(project.id, InstallSkillsRequest(tool="claude_code", triggered_by_user_id=_dev(db).id), db)

    readme = calls["content"]["docs/sdlc/README.md"]
    assert "# Working on SDLC stages with your own coding tool" in readme
    assert "## Requirement Intake — Claude Code" in readme
    assert "<!-- sdlc-hub:coding-tool-section:claude_code:requirement_intake start -->" in readme


def test_install_of_a_second_stage_preserves_the_first_stage_section_and_reviewer(db, project, monkeypatch):
    # This is the real regression a user hit: installing Existing System Context Scan's
    # skills after Feature Intake's must not delete Feature Intake's instructions or
    # silently repoint its reviewer subagent.
    _repo(db, project)
    existing_readme = (
        "# Working on SDLC stages with your own coding tool\n\n"
        "## Shared files\n- shared stuff\n\n"
        "<!-- sdlc-hub:coding-tool-section:claude_code:feature_intake start -->\n"
        "## Feature Intake — Claude Code\n\nDo the feature intake thing.\n"
        "<!-- sdlc-hub:coding-tool-section:claude_code:feature_intake end -->\n"
    )
    calls = _patch_github(
        monkeypatch,
        existing={"docs/sdlc/README.md": "readmesha", ".claude/agents/sdlc-reviewer-feature-intake.md": "reviewersha"},
        existing_readme=existing_readme,
    )
    result = install_skill_pack(
        project.id, InstallSkillsRequest(tool="claude_code", stage="existing_system_context_scan", triggered_by_user_id=_dev(db).id), db
    )

    # The old stage's reviewer file is untouched — a different filename, never even looked at.
    assert not any(p == ".claude/agents/sdlc-reviewer-feature-intake.md" for p, _, _ in calls["files"])
    assert ".claude/agents/sdlc-reviewer-existing-system-context-scan.md" in result.committed

    readme = calls["content"]["docs/sdlc/README.md"]
    assert "## Feature Intake — Claude Code" in readme and "Do the feature intake thing." in readme
    assert "## Existing System Context Scan — Claude Code" in readme
    assert readme.index("Feature Intake") < readme.index("Existing System Context Scan")


def test_install_of_the_same_stage_again_replaces_only_its_own_section(db, project, monkeypatch):
    _repo(db, project)
    existing_readme = (
        "# Working on SDLC stages with your own coding tool\n\n"
        "<!-- sdlc-hub:coding-tool-section:claude_code:requirement_intake start -->\n"
        "## Requirement Intake — Claude Code\n\nSTALE INSTRUCTIONS.\n"
        "<!-- sdlc-hub:coding-tool-section:claude_code:requirement_intake end -->\n"
        "\n<!-- sdlc-hub:coding-tool-section:codex:feature_intake start -->\n"
        "## Feature Intake — Codex\n\nOther tool, other stage, keep me.\n"
        "<!-- sdlc-hub:coding-tool-section:codex:feature_intake end -->\n"
    )
    calls = _patch_github(monkeypatch, existing={"docs/sdlc/README.md": "readmesha"}, existing_readme=existing_readme)
    install_skill_pack(project.id, InstallSkillsRequest(tool="claude_code", triggered_by_user_id=_dev(db).id), db)

    readme = calls["content"]["docs/sdlc/README.md"]
    assert "STALE INSTRUCTIONS." not in readme
    assert "## Requirement Intake — Claude Code" in readme
    assert "## Feature Intake — Codex" in readme and "Other tool, other stage, keep me." in readme


def test_install_with_nothing_to_add_creates_no_branch(db, project, monkeypatch):
    _repo(db, project)
    # Only the shared, team-edited files remain, and they already exist.
    only_unmanaged = build_skill_pack(db, project, tool="codex", stage="requirement_intake")
    monkeypatch.setattr(routes, "build_skill_pack", lambda *a, **k: type(only_unmanaged)(
        only_unmanaged.tool, only_unmanaged.tool_label, only_unmanaged.stage, [f for f in only_unmanaged.files if not f.managed],
        only_unmanaged.usage, only_unmanaged.notes))
    calls = _patch_github(monkeypatch, existing={"AGENTS.md": "s"})
    result = install_skill_pack(project.id, InstallSkillsRequest(tool="codex", triggered_by_user_id=_dev(db).id), db)
    assert result.branch_name is None and calls["branch"] == [] and calls["pr"] == []


def test_reinstalling_with_nothing_actually_changed_opens_no_pr(db, project, monkeypatch):
    """Regression — a real bug: re-running "Add via pull request" when
    every managed file's content was already identical to what's on the
    base branch still opened a new, empty-diff pull request every time."""
    _repo(db, project)
    pack = build_skill_pack(db, project, tool="claude_code", stage="requirement_intake")
    existing_by_path = {f.path: f.content for f in pack.files}
    # The README section, as it would actually look once already merged in.
    from app.services.coding_tool_skills import merge_readme, readme_section_key

    readme_path = next(p for p in existing_by_path if p.endswith("README.md"))
    merged_readme = merge_readme(None, existing_by_path[readme_path], readme_section_key("claude_code", "requirement_intake"))
    existing_by_path[readme_path] = merged_readme

    calls = _patch_github(monkeypatch, existing={p: "sha" for p in existing_by_path})
    monkeypatch.setattr(
        github_api, "read_file",
        lambda token, o, r, path, ref, **kw: GitHubFileContent(
            path=path, sha="sha", size=len(existing_by_path[path]), content=existing_by_path[path], truncated=False, is_binary=False
        ),
    )

    result = install_skill_pack(project.id, InstallSkillsRequest(tool="claude_code", triggered_by_user_id=_dev(db).id), db)

    assert result.branch_name is None and result.pull_request_url is None and result.committed == []
    assert calls["branch"] == [] and calls["pr"] == []
    assert "up to date" in result.message
    assert any(s.reason == "Already up to date." for s in result.skipped)


def test_install_into_an_empty_repository_makes_the_first_commit_without_a_pr(db, project, monkeypatch):
    _repo(db, project)
    calls = _patch_github(monkeypatch, branches=())
    result = install_skill_pack(project.id, InstallSkillsRequest(tool="claude_code", triggered_by_user_id=_dev(db).id), db)

    assert calls["branch"] == [] and calls["pr"] == []  # nothing to branch from or open a PR against
    assert result.pull_request_url is None and result.branch_name == "main"
    assert ".claude/commands/requirement-intake.md" in result.committed and ".claude/settings.json" in result.committed
    assert all(branch == "main" and sha is None for _, branch, sha in calls["files"])
    assert "was empty" in result.message


def test_install_names_the_existing_branches_when_the_default_branch_is_wrong(db, project, monkeypatch):
    _repo(db, project, default_branch="main")
    _patch_github(monkeypatch, branches=("master", "dev"))
    with pytest.raises(HTTPException) as exc:
        install_skill_pack(project.id, InstallSkillsRequest(tool="codex", triggered_by_user_id=_dev(db).id), db)
    assert exc.value.status_code == 409 and "'main' does not exist" in exc.value.detail and "master" in exc.value.detail


def test_install_requires_a_connected_repository_and_a_permitted_role(db, project, actor, monkeypatch):
    with pytest.raises(HTTPException) as no_repo:
        install_skill_pack(project.id, InstallSkillsRequest(tool="codex", triggered_by_user_id=actor.id), db)
    assert no_repo.value.status_code == 409

    _repo(db, project)
    viewer = User(email=f"{uuid.uuid4()}@example.com", full_name="V", role=UserRole.VIEWER)
    db.add(viewer)
    db.flush()
    with pytest.raises(HTTPException) as denied:
        install_skill_pack(project.id, InstallSkillsRequest(tool="codex", triggered_by_user_id=viewer.id), db)
    assert denied.value.status_code == 403


# --- Sync back ------------------------------------------------------------------------------

GOOD_DOC = """---
sdlc_stage: requirement_intake
project_id: {pid}
project: Test Project
status: draft
generated_by: claude-code
---
# Requirement Intake Summary

## Stakeholder & Request
Kanishka Isuru, the business owner, asks for a simple but practical to-do application for day to day work and personal task tracking.

## Constraints
- Budget: keep the cost very low; prefer free and open source tools.
- Timeline: a simple working version in one to two weeks.

## Success Metric
Users can create, update, delete and complete tasks easily, and see today's tasks and overdue tasks clearly on desktop and mobile.
"""


def _patch_read(monkeypatch, content, sha="filesha1"):
    monkeypatch.setattr(routes, "decrypt_repository_token", lambda repo: "tok")
    seen = {}

    def read_file(token, o, r, path, ref, **kw):
        seen["path"], seen["ref"] = path, ref
        if content is None:
            raise GitHubIntegrationError("Not Found", status_code=404)
        return GitHubFileContent(path=path, sha=sha, size=len(content), content=content, truncated=False, is_binary=False)

    monkeypatch.setattr(github_api, "read_file", read_file)
    return seen


def test_sync_creates_a_draft_version_from_the_repo_file(db, project, actor, monkeypatch):
    node = _intake_node(db, project)
    _repo(db, project)
    seen = _patch_read(monkeypatch, GOOD_DOC.format(pid=project.id))

    result = sync_stage(project.id, SyncStageRequest(triggered_by_user_id=actor.id, ref="feature/intake"), db)

    assert seen == {"path": "docs/sdlc/requirement-intake.md", "ref": "feature/intake"}
    assert result.created_artifact and result.version_number == 1 and result.artifact_status == "DRAFT"
    assert result.workflow_node_status == "READY" and result.generated_by == "claude-code"
    artifact = db.get(Artifact, result.artifact_id)
    text = artifact.current_version.content_markdown
    assert text.startswith("# Requirement Intake Summary") and "sdlc_stage" not in text  # front matter stripped
    assert "claude-code" in artifact.current_version.change_summary and "octocat/app@feature/intake" in artifact.current_version.change_summary
    assert node.status == WorkflowStatus.READY


def test_second_sync_adds_a_new_version_defaulting_to_the_default_branch(db, project, actor, monkeypatch):
    _intake_node(db, project)
    _repo(db, project)
    seen = _patch_read(monkeypatch, GOOD_DOC.format(pid=project.id))
    sync_stage(project.id, SyncStageRequest(triggered_by_user_id=actor.id), db)
    second = sync_stage(project.id, SyncStageRequest(triggered_by_user_id=actor.id), db)
    assert seen["ref"] == "main" and second.version_number == 2 and not second.created_artifact


@pytest.mark.parametrize(
    "mutate,status_code,fragment",
    [
        (lambda d, pid: d.replace("sdlc_stage: requirement_intake", "sdlc_stage: hld"), 409, "not 'requirement_intake'"),
        (lambda d, pid: d.replace(str(pid), str(uuid.uuid4())), 409, "different project"),
        (lambda d, pid: d.replace("## Constraints", "## Limits"), 422, 'missing section "## Constraints"'),
        (lambda d, pid: d.replace("Users can create", "TODO Users can create"), 422, "placeholder"),
        (lambda d, pid: "---\nsdlc_stage: requirement_intake\n---\n# Clarification Needed\n\n- What budget?", 422, "clarification"),
    ],
)
def test_sync_rejects_bad_documents(db, project, actor, monkeypatch, mutate, status_code, fragment):
    _intake_node(db, project)
    _repo(db, project)
    _patch_read(monkeypatch, mutate(GOOD_DOC.format(pid=project.id), project.id))
    with pytest.raises(HTTPException) as exc:
        sync_stage(project.id, SyncStageRequest(triggered_by_user_id=actor.id), db)
    assert exc.value.status_code == status_code and fragment in exc.value.detail


def test_sync_never_overwrites_reviewed_work(db, project, actor, monkeypatch):
    _intake_node(db, project)
    _repo(db, project)
    _patch_read(monkeypatch, GOOD_DOC.format(pid=project.id))
    first = sync_stage(project.id, SyncStageRequest(triggered_by_user_id=actor.id), db)
    artifact = db.get(Artifact, first.artifact_id)
    artifact.status = ArtifactStatus.READY_FOR_REVIEW
    db.flush()
    with pytest.raises(HTTPException) as exc:
        sync_stage(project.id, SyncStageRequest(triggered_by_user_id=actor.id), db)
    assert exc.value.status_code == 409 and "never overwrites reviewed work" in exc.value.detail


def test_sync_missing_file_and_locked_stage_are_clear_errors(db, project, actor, monkeypatch):
    _intake_node(db, project)
    _repo(db, project)
    _patch_read(monkeypatch, None)
    with pytest.raises(HTTPException) as missing:
        sync_stage(project.id, SyncStageRequest(triggered_by_user_id=actor.id, ref="dev"), db)
    assert missing.value.status_code == 404 and "'dev'" in missing.value.detail


def test_sync_into_a_locked_stage_is_refused(db, project, actor, monkeypatch):
    _intake_node(db, project, status=WorkflowStatus.LOCKED)
    _repo(db, project)
    _patch_read(monkeypatch, GOOD_DOC.format(pid=project.id))
    with pytest.raises(HTTPException) as exc:
        sync_stage(project.id, SyncStageRequest(triggered_by_user_id=actor.id), db)
    assert exc.value.status_code == 409 and "locked" in exc.value.detail


# --- The generated validator script really works (needs Node) -------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_generated_validator_script(db, project, tmp_path):
    pack = build_skill_pack(db, project, tool="claude_code", stage="requirement_intake")
    script = next(f for f in pack.files if f.path == ".sdlc/hooks/validate-doc.mjs")
    (tmp_path / ".sdlc" / "hooks").mkdir(parents=True)
    (tmp_path / ".sdlc" / "hooks" / "validate-doc.mjs").write_text(script.content, encoding="utf-8")
    (tmp_path / "docs" / "sdlc").mkdir(parents=True)
    doc = tmp_path / "docs" / "sdlc" / "requirement-intake.md"

    def run(*args, stdin=None):
        return subprocess.run(["node", ".sdlc/hooks/validate-doc.mjs", *args], cwd=tmp_path, input=stdin, capture_output=True, text=True, timeout=30)

    doc.write_text(GOOD_DOC.format(pid=project.id), encoding="utf-8")
    assert run("docs/sdlc/requirement-intake.md").returncode == 0

    doc.write_text(GOOD_DOC.format(pid=project.id).replace("## Constraints", "## Limits").replace("Users can", "TODO Users can"), encoding="utf-8")
    cli = run("docs/sdlc/requirement-intake.md")
    assert cli.returncode == 1 and 'Missing section "## Constraints"' in cli.stderr and "Placeholder" in cli.stderr

    # As a Claude Code hook: JSON on stdin, exit code 2 so the assistant sees stderr.
    hook = run(stdin=json.dumps({"tool_input": {"file_path": str(doc)}}))
    assert hook.returncode == 2 and "Constraints" in hook.stderr

    # Files that are not stage documents are ignored by the hook.
    other = tmp_path / "src.md"
    other.write_text("hello", encoding="utf-8")
    assert run(stdin=json.dumps({"tool_input": {"file_path": str(other)}})).returncode == 0
    assert run(stdin=json.dumps({"tool_input": {"file_path": str(tmp_path / "docs" / "sdlc" / "context.md")}})).returncode == 0


# --- feature_intake (existing-project work) — one freeform field --------------------------

FEATURE_DOC = """---
sdlc_stage: feature_intake
project_id: {pid}
project: Drisk Application
status: draft
generated_by: claude-code
---
# Feature Intake Summary

## Summary
Add CSV export to the reports page so finance can download monthly totals without asking engineering.

## Current Behavior
Reports are only viewable on-screen; there is no way to export the data.

## Expected Behavior
A "Export CSV" button on the reports page downloads the currently filtered report as a CSV file.

## Scope
Existing module: apps/web/reports. Affected users: the finance team and any admin viewing reports.

## References
GitHub repository: octocat/app. Jira project key: DRISK. Confluence link: None given. Attachments: None given.
"""


def _feature_node(db, project, status=WorkflowStatus.READY):
    return make_node(db, project, node_key="feature_intake", order_index=0, status=status, output_artifact_type="existing_feature_intake")


def test_feature_intake_uses_a_single_freeform_field(db, project):
    pack = build_skill_pack(db, project, tool="claude_code", stage="feature_intake")
    command = next(f for f in pack.files if f.path == ".claude/commands/feature-intake.md").content
    # One consolidated field, not the old thirteen separate ones.
    assert "$ARGUMENTS" in command
    assert "feature_title" not in command and "business_reason" not in command
    assert "## Summary" in command and "## References" in command


def test_feature_intake_sync_round_trips(db, project, actor, monkeypatch):
    node = _feature_node(db, project)
    _repo(db, project)
    monkeypatch.setattr(routes, "decrypt_repository_token", lambda repo: "tok")
    monkeypatch.setattr(
        github_api, "read_file",
        lambda token, o, r, path, ref, **kw: GitHubFileContent(
            path=path, sha="sha1", size=1, content=FEATURE_DOC.format(pid=project.id), truncated=False, is_binary=False
        ),
    )
    result = sync_stage(project.id, SyncStageRequest(stage="feature_intake", triggered_by_user_id=actor.id), db)
    assert result.created_artifact and result.artifact_status == "DRAFT" and result.path == "docs/sdlc/feature-intake.md"
    artifact = db.get(Artifact, result.artifact_id)
    assert artifact.current_version.content_markdown.startswith("# Feature Intake Summary")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_feature_intake_validator_script_checks_its_own_headings(db, project, tmp_path):
    pack = build_skill_pack(db, project, tool="claude_code", stage="feature_intake")
    script = next(f for f in pack.files if f.path == ".sdlc/hooks/validate-doc.mjs")
    (tmp_path / ".sdlc" / "hooks").mkdir(parents=True)
    (tmp_path / ".sdlc" / "hooks" / "validate-doc.mjs").write_text(script.content, encoding="utf-8")
    (tmp_path / "docs" / "sdlc").mkdir(parents=True)
    doc = tmp_path / "docs" / "sdlc" / "feature-intake.md"

    def run(*args):
        return subprocess.run(["node", ".sdlc/hooks/validate-doc.mjs", *args], cwd=tmp_path, capture_output=True, text=True, timeout=30)

    doc.write_text(FEATURE_DOC.format(pid=project.id), encoding="utf-8")
    assert run("docs/sdlc/feature-intake.md").returncode == 0

    doc.write_text(FEATURE_DOC.format(pid=project.id).replace("## Scope", "## Extent"), encoding="utf-8")
    result = run("docs/sdlc/feature-intake.md")
    assert result.returncode == 1 and 'Missing section "## Scope"' in result.stderr


# --- Derived stages (Existing System Context Scan, Impact Analysis, Feature Solution --------
# --- Discovery, HLD Delta, Story Crafting) --- no freeform input, built from upstream docs -----


def test_derived_stage_snapshots_approved_upstream_and_flags_missing_ones(db, project, actor):
    intake = make_node(db, project, node_key="feature_intake", order_index=0, status=WorkflowStatus.APPROVED, output_artifact_type="existing_feature_intake")
    make_node(
        db, project, node_key="existing_system_context_scan", order_index=1, status=WorkflowStatus.READY,
        output_artifact_type="existing_system_context", required_inputs=["existing_feature_intake"],
    )
    make_approved_artifact(db, project, intake, actor, content="## Summary\nAdd CSV export.\n")

    # existing_system_context_scan needs only feature_intake's output -> fully available.
    pack = build_skill_pack(db, project, tool="claude_code", stage="existing_system_context_scan")
    inputs_files = {f.path: f for f in pack.files if f.path.startswith("docs/sdlc/inputs/")}
    assert set(inputs_files) == {"docs/sdlc/inputs/existing_feature_intake.md"}
    assert "Add CSV export." in inputs_files["docs/sdlc/inputs/existing_feature_intake.md"].content
    assert inputs_files["docs/sdlc/inputs/existing_feature_intake.md"].managed is True

    # impact_analysis also needs existing_system_context, which hasn't been approved yet.
    make_node(
        db, project, node_key="impact_analysis", order_index=2, status=WorkflowStatus.LOCKED, output_artifact_type="impact_analysis",
        required_inputs=["existing_feature_intake", "existing_system_context"],
    )
    pack2 = build_skill_pack(db, project, tool="claude_code", stage="impact_analysis")
    inputs2 = {f.path: f for f in pack2.files if f.path.startswith("docs/sdlc/inputs/")}
    assert set(inputs2) == {"docs/sdlc/inputs/existing_feature_intake.md", "docs/sdlc/inputs/existing_system_context.md"}
    assert "not yet available" in inputs2["docs/sdlc/inputs/existing_system_context.md"].content


def test_derived_stage_command_takes_no_required_argument_and_lists_its_inputs(db, project, actor):
    intake = make_node(db, project, node_key="feature_intake", order_index=0, status=WorkflowStatus.APPROVED, output_artifact_type="existing_feature_intake")
    make_node(
        db, project, node_key="existing_system_context_scan", order_index=1, status=WorkflowStatus.READY,
        output_artifact_type="existing_system_context", required_inputs=["existing_feature_intake"],
    )
    make_approved_artifact(db, project, intake, actor)

    pack = build_skill_pack(db, project, tool="claude_code", stage="existing_system_context_scan")
    command = next(f for f in pack.files if f.path == ".claude/commands/existing-system-context-scan.md").content
    assert "argument-hint: [no argument needed]" in command
    assert "docs/sdlc/inputs/existing_feature_intake.md" in command
    assert "No freeform input is required" in command
    assert "Get the input:" not in command


def test_feature_solution_discovery_uses_the_renamed_display_title(db, project):
    pack = build_skill_pack(db, project, tool="cursor", stage="mini_solution_discovery")
    command = next(f for f in pack.files if f.path == ".cursor/commands/feature-solution-discovery.md").content
    assert "Feature Solution Discovery" in command and "Mini Solution Discovery" not in command


def test_story_crafting_command_uses_story_backlog_template_regardless_of_which_template_produced_it(db, project, actor):
    # Default (new-project) template's story_crafting depends on solution_discovery + hld --
    # build_skill_pack must classify those as upstream artifacts generically, not by a
    # hardcoded per-stage list, since the same node_key is shared by both templates.
    sol = make_node(db, project, node_key="solution_discovery", order_index=0, status=WorkflowStatus.APPROVED, output_artifact_type="solution_options_doc")
    hld = make_node(db, project, node_key="hld", order_index=1, status=WorkflowStatus.APPROVED, output_artifact_type="hld_document")
    make_node(
        db, project, node_key="story_crafting", order_index=2, status=WorkflowStatus.READY, output_artifact_type="story_backlog",
        required_inputs=["solution_options_doc", "hld_document"],
    )
    make_approved_artifact(db, project, sol, actor, content="Chosen solution: X.")
    make_approved_artifact(db, project, hld, actor, content="Architecture: Y.")

    pack = build_skill_pack(db, project, tool="claude_code", stage="story_crafting")
    inputs_files = {f.path for f in pack.files if f.path.startswith("docs/sdlc/inputs/")}
    assert inputs_files == {"docs/sdlc/inputs/solution_options_doc.md", "docs/sdlc/inputs/hld_document.md"}
    command = next(f for f in pack.files if f.path == ".claude/commands/story-crafting.md").content
    assert "## Story:" in command and "Repeat this" in command


VALID_STORY_BACKLOG = (
    "---\n"
    "sdlc_stage: story_crafting\n"
    "project_id: {pid}\n"
    "status: draft\n"
    "generated_by: claude-code\n"
    "---\n"
    "## Story: Export report as CSV\n\n"
    "**Epic:** Reporting\n"
    "**Feature:** CSV Export\n"
    "**Mode:** VERTICAL\n"
    "**User Story:** As a finance user, I want to export a report as CSV, so that I can analyze it offline.\n"
    "**Business Value:** Reduces manual data requests to engineering.\n"
    "**Acceptance Criteria:**\n"
    "- [ ] Export button downloads a CSV of the current filtered view\n"
    "**Suggested Owner Role:** DEVELOPER\n"
    "**Technical Areas Involved:** Frontend, Backend\n"
    "**Dependencies:** None.\n"
    "**Priority:** High\n"
    "**Story Points Estimate:** 5\n"
    "**Jira Issue Type:** Story\n"
    "**Estimated PR Review Time:** 5 / 15 / 30\n"
    "**Suggested Subtasks:**\n"
    "- [ ] Add export endpoint\n"
    "**Release Readiness Criteria:**\n"
    "- [ ] Verified on staging\n"
    "**Definition of Done:**\n"
    "- [ ] Tests pass\n"
)


def test_story_crafting_sync_uses_the_real_story_parser(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, status=WorkflowStatus.READY, output_artifact_type="story_backlog")

    result = sync_stage_document(
        db=db, project=project, node=node, user=actor, markdown=VALID_STORY_BACKLOG.format(pid=project.id),
        source_label="octocat/app@main", source_path="docs/sdlc/story-crafting.md",
    )
    assert result.created_artifact
    assert "Export report as CSV" in result.artifact.current_version.content_markdown


def test_story_crafting_sync_rejects_a_story_missing_required_fields(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, status=WorkflowStatus.READY, output_artifact_type="story_backlog")
    broken = VALID_STORY_BACKLOG.format(pid=project.id).replace("**Mode:** VERTICAL\n", "")

    with pytest.raises(StageSyncError) as exc:
        sync_stage_document(
            db=db, project=project, node=node, user=actor, markdown=broken,
            source_label="octocat/app@main", source_path="docs/sdlc/story-crafting.md",
        )
    assert "missing Mode" in str(exc.value)


def test_story_crafting_sync_rejects_a_story_whose_review_time_exceeds_the_cap(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, status=WorkflowStatus.READY, output_artifact_type="story_backlog")
    too_big = VALID_STORY_BACKLOG.format(pid=project.id).replace("**Estimated PR Review Time:** 5 / 15 / 30\n", "**Estimated PR Review Time:** 10 / 40 / 90\n")

    with pytest.raises(StageSyncError) as exc:
        sync_stage_document(
            db=db, project=project, node=node, user=actor, markdown=too_big,
            source_label="octocat/app@main", source_path="docs/sdlc/story-crafting.md",
        )
    assert "worst case is 90 minutes" in str(exc.value) and "split this story" in str(exc.value)


def test_story_crafting_sync_rejects_a_story_missing_the_review_time_field(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, status=WorkflowStatus.READY, output_artifact_type="story_backlog")
    missing = VALID_STORY_BACKLOG.format(pid=project.id).replace("**Estimated PR Review Time:** 5 / 15 / 30\n", "")

    with pytest.raises(StageSyncError) as exc:
        sync_stage_document(
            db=db, project=project, node=node, user=actor, markdown=missing,
            source_label="octocat/app@main", source_path="docs/sdlc/story-crafting.md",
        )
    assert "missing Estimated PR Review Time" in str(exc.value)


def test_story_crafting_sync_accepts_a_review_time_within_budget(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, status=WorkflowStatus.READY, output_artifact_type="story_backlog")
    result = sync_stage_document(
        db=db, project=project, node=node, user=actor, markdown=VALID_STORY_BACKLOG.format(pid=project.id),
        source_label="octocat/app@main", source_path="docs/sdlc/story-crafting.md",
    )
    assert result.created_artifact


def test_story_crafting_sync_rejects_a_document_with_no_story_blocks(db, project, actor):
    node = make_node(db, project, node_key="story_crafting", order_index=0, status=WorkflowStatus.READY, output_artifact_type="story_backlog")
    with pytest.raises(StageSyncError) as exc:
        sync_stage_document(
            db=db, project=project, node=node, user=actor,
            markdown="---\nsdlc_stage: story_crafting\nproject_id: " + str(project.id) + "\n---\nJust prose, no stories.\n",
            source_label="octocat/app@main", source_path="docs/sdlc/story-crafting.md",
        )
    assert "no \"## Story" in str(exc.value)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_generated_validator_checks_a_story_backlog_document(db, project, tmp_path):
    pack = build_skill_pack(db, project, tool="claude_code", stage="story_crafting")
    script = next(f for f in pack.files if f.path == ".sdlc/hooks/validate-doc.mjs")
    (tmp_path / ".sdlc" / "hooks").mkdir(parents=True)
    (tmp_path / ".sdlc" / "hooks" / "validate-doc.mjs").write_text(script.content, encoding="utf-8")
    (tmp_path / "docs" / "sdlc").mkdir(parents=True)
    doc = tmp_path / "docs" / "sdlc" / "story-crafting.md"

    def run():
        return subprocess.run(["node", ".sdlc/hooks/validate-doc.mjs", "docs/sdlc/story-crafting.md"], cwd=tmp_path, capture_output=True, text=True, timeout=30)

    doc.write_text(VALID_STORY_BACKLOG.format(pid=project.id), encoding="utf-8")
    assert run().returncode == 0

    doc.write_text(VALID_STORY_BACKLOG.format(pid=project.id).replace("**Priority:** High\n", ""), encoding="utf-8")
    result = run()
    assert result.returncode == 1 and 'missing or empty "Priority"' in result.stderr
