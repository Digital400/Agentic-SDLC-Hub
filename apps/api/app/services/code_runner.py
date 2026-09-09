"""CodeRunnerService — foundation for an isolated, on-disk workspace that
materializes one story's agent-generated changes into a real git branch
(see app/models/code_run.py). Distinct from
app/services/github_integration.py (a read/write client against GitHub's
REST API, no local checkout at all) and from
app/api/routes/implementation_runs.py's create_pull_request (which
commits file-by-file through that same REST API): this service instead
clones a real repository onto local disk, so a story's changes can be
applied, tested, and committed as an actual git history before anything
is pushed.

FOUNDATION SCOPE (this step): workspace creation, a real clone when
GitHub credentials are configured (a disclosed placeholder otherwise),
branch name generation, a single command-execution abstraction every
git/test invocation goes through, structured log capture, and status
tracking through CodeRunStatus. apply_changes/run_tests/create_commit/
push_branch/prepare_pr_creation are also implemented here (they're
built on the exact same primitives), but none of this is wired into an
API route or the story delivery lane yet — that orchestration is a
later step.

SECURITY (all four rules apply everywhere in this module):
  1. Never run untrusted shell commands from user input directly — every
     process this service starts goes through _run_command, which calls
     subprocess.run with an argv list and shell=False; a caller can never
     make this module interpret a raw string as a shell command.
  2. Use allowlisted commands — _run_git refuses any git subcommand not
     in _ALLOWED_GIT_SUBCOMMANDS; run_tests refuses any test command
     whose first token isn't in settings.CODE_RUNNER_ALLOWED_TEST_EXECUTABLES.
     Both raise CodeRunnerError instead of silently skipping or running
     anyway.
  3. Store logs safely — every CodeRun.logs entry is a plain, structured
     dict (timestamp/level/message), never raw untyped shell output
     dumped wholesale without going through the same redaction path as
     everything else in this module.
  4. Do not expose tokens — every call that embeds a decrypted token in
     a URL (clone/push) passes that token as `secret` to _run_command,
     which scrubs every occurrence of it from the log entry before it's
     ever written to CodeRun.logs. The token itself is never returned,
     never stored on the CodeRun row, and never logged in any form.
"""

from __future__ import annotations

import logging
import re
import shlex
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import SecretDecryptionError, decrypt_secret
from app.models import CodeRun, CodeRunStatus, Repository, Story

logger = logging.getLogger(__name__)


class CodeRunnerError(Exception):
    """Raised when a CodeRunnerService step can't proceed — a refused
    (non-allowlisted) command, a failed/timed-out process, or a missing
    precondition. Never raised for "no GitHub credential configured";
    that's the disclosed placeholder path, not a failure."""


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def generate_branch_name(story_title: str, run_id: uuid.UUID) -> str:
    """Deterministic, collision-resistant branch name — same slugify
    convention as app/api/routes/implementation_runs.py's own
    `_slugify`/PR-branch naming, prefixed distinctly ("codegen/") so a
    CodeRunnerService branch is never confused with that route's own
    "agent/"-prefixed branches at a glance."""
    slug = _SLUG_RE.sub("-", story_title.lower()).strip("-")[:40].strip("-") or "story"
    return f"codegen/{slug}-{str(run_id)[:8]}"


# SECURITY (rule 2) — the complete set of git subcommands this service
# will ever execute. Anything else is refused outright.
_ALLOWED_GIT_SUBCOMMANDS = {
    "clone", "checkout", "branch", "add", "commit", "push", "status", "rev-parse", "diff", "log", "init", "remote", "config",
}

# A generated repository is commonly a monorepo — repo_bootstrap.py's own
# scaffolding puts a Next.js project under frontend/ and a FastAPI one
# under backend/, each with their own package.json/requirements.txt, and
# the workspace root has neither. Rather than hardcode those two names,
# each test executable is mapped to the marker file(s) that identify
# *its* project root, and _resolve_test_command_cwd below looks for one.
_TEST_COMMAND_PROJECT_MARKERS: dict[str, tuple[str, ...]] = {
    "npm": ("package.json",),
    "yarn": ("package.json",),
    "pnpm": ("package.json",),
    "pytest": ("pyproject.toml", "requirements.txt", "setup.cfg", "pytest.ini"),
    "go": ("go.mod",),
    "mvn": ("pom.xml",),
    "gradle": ("build.gradle", "build.gradle.kts"),
}


def _resolve_test_command_cwd(workspace: Path, executable: str) -> Path:
    """Runs `executable` from the workspace root when that already has
    (or the executable has no known marker), otherwise from the single
    immediate subdirectory that has it — e.g. "npm" resolves to
    workspace/frontend when frontend/package.json exists but there's no
    package.json at the workspace root. Two or more matching
    subdirectories is ambiguous, so this never guesses between them and
    falls back to the workspace root (the original, disclosed
    behavior) — a real "command failed" surfaces there instead of a
    silent wrong pick."""
    markers = _TEST_COMMAND_PROJECT_MARKERS.get(executable, ())
    if not markers or any((workspace / marker).exists() for marker in markers):
        return workspace
    candidates = sorted(
        child for child in workspace.iterdir()
        if child.is_dir() and not child.name.startswith(".") and any((child / marker).exists() for marker in markers)
    )
    return candidates[0] if len(candidates) == 1 else workspace


@dataclass
class CommandResult:
    command_display: str  # already redacted — safe to log/render as-is
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


def _redact(text: str, secret: str | None) -> str:
    if not secret:
        return text
    return text.replace(secret, "***")


class CodeRunnerService:
    """Stateless except for the DB session it's given — every method
    takes the `CodeRun` row and (where relevant) the workspace `Path` it
    should act on explicitly, rather than holding either as instance
    state, so a caller can freely interleave steps across multiple runs."""

    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    # --- Log capture + status tracking --------------------------------------

    def _log(self, run: CodeRun, level: str, message: str, *, secret: str | None = None) -> None:
        """Rule 3/4 — every log entry is a plain, structured dict; `secret`
        (a decrypted token, if one was in scope for this step) is scrubbed
        from `message` before it's ever appended."""
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": level, "message": _redact(message, secret)}
        run.logs = [*run.logs, entry]
        self.db.flush()

    def set_status(self, run: CodeRun, status: CodeRunStatus, *, error_message: str | None = None) -> None:
        run.status = status
        if run.started_at is None and status != CodeRunStatus.QUEUED:
            run.started_at = datetime.now(timezone.utc)
        if status in (CodeRunStatus.FAILED, CodeRunStatus.PUSHED):
            run.completed_at = datetime.now(timezone.utc)
        if status == CodeRunStatus.FAILED:
            run.error_message = error_message
        self._log(run, "ERROR" if status == CodeRunStatus.FAILED else "INFO", f"Status -> {status.value}" + (f": {error_message}" if error_message else ""))

    # --- 1. Isolated workspace -----------------------------------------------

    def create_workspace(self, run: CodeRun) -> Path:
        """One directory per run, under settings.CODE_RUNNER_WORKSPACE_ROOT
        — never shared between runs, never reused, so one story's changes
        can never leak into another's workspace."""
        workspace = self.settings.CODE_RUNNER_WORKSPACE_ROOT / str(run.id)
        workspace.mkdir(parents=True, exist_ok=False)
        run.workspace_path = str(workspace)
        self._log(run, "INFO", f"Created isolated workspace at {workspace}")
        self.db.flush()
        return workspace

    # --- Command execution abstraction (rules 1, 2) --------------------------

    def _run_command(
        self, run: CodeRun, cwd: Path, command: list[str], *, secret: str | None = None, timeout: int = 900,
    ) -> CommandResult:
        """The one place any process is ever started. `command` must
        already be a real argv list — this never parses or evaluates a
        string as shell syntax (shell=False, always).

        Windows note: CreateProcess (what subprocess uses under
        shell=False) does not search PATHEXT the way cmd.exe does, so a
        bare "npm"/"yarn"/etc. — actually an "npm.cmd" batch file on
        Windows — fails to start with WinError 2 even though it's on
        PATH and running the identical command in a shell works fine.
        shutil.which() does that PATHEXT-aware resolution itself, so we
        resolve just the executable (argv[0]) through it before handing
        the argv list to subprocess.run — still shell=False, still a
        plain argv list, no shell string ever gets interpreted. Falls
        back to the literal name if it can't be resolved, so the
        original (clearer) error still surfaces on a genuinely missing
        executable."""
        display = _redact(" ".join(command), secret)
        resolved = [shutil.which(command[0]) or command[0], *command[1:]]
        start = datetime.now(timezone.utc)
        try:
            proc = subprocess.run(resolved, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, shell=False)
        except subprocess.TimeoutExpired as exc:
            self._log(run, "ERROR", f"Command timed out after {timeout}s: {display}", secret=secret)
            raise CodeRunnerError(f"Command timed out: {display}") from exc
        except OSError as exc:
            self._log(run, "ERROR", f"Command failed to start: {display} ({exc})", secret=secret)
            raise CodeRunnerError(f"Command failed to start: {display}") from exc

        duration = (datetime.now(timezone.utc) - start).total_seconds()
        result = CommandResult(
            command_display=display, exit_code=proc.returncode,
            stdout=_redact(proc.stdout, secret), stderr=_redact(proc.stderr, secret), duration_seconds=duration,
        )
        output = "\n".join(p for p in (result.stdout.strip(), result.stderr.strip()) if p)
        self._log(run, "INFO" if result.succeeded else "ERROR", f"$ {display} (exit {result.exit_code}, {duration:.1f}s)" + (f"\n{output}" if output else ""))
        return result

    def _run_git(self, run: CodeRun, cwd: Path, args: list[str], *, secret: str | None = None, timeout: int = 900) -> CommandResult:
        if not args or args[0] not in _ALLOWED_GIT_SUBCOMMANDS:
            got = args[0] if args else "(empty)"
            raise CodeRunnerError(f"Refusing to run non-allowlisted git subcommand: {got!r}")
        return self._run_command(run, cwd, ["git", *args], secret=secret, timeout=timeout)

    def _repository_token(self, repository: Repository) -> str | None:
        """Best-effort — never raises. A repository with no connection, or
        one whose stored token can no longer be decrypted, degrades to the
        placeholder path (rule 4 stays satisfied either way: no token, no
        exposure risk)."""
        if repository.connection is None:
            return None
        try:
            return decrypt_secret(repository.connection.access_token_encrypted)
        except SecretDecryptionError:
            return None

    # --- 2/3. Clone + checkout base branch -----------------------------------

    def clone_repository(self, run: CodeRun, workspace: Path, repository: Repository, base_branch: str) -> bool:
        """Returns True if a real clone happened, False if the placeholder
        path was used (no usable GitHub credential configured) — never
        raises for the placeholder case itself."""
        self.set_status(run, CodeRunStatus.CLONING)
        token = self._repository_token(repository)
        if token is None:
            self._log(run, "INFO", "No decryptable GitHub credential for this repository — clone skipped (placeholder).")
            (workspace / ".codegen-placeholder").write_text(
                "No real clone was performed for this run — configure and connect a GitHub repository "
                "to enable real cloning (see app/services/github_integration.py).\n"
            )
            return False

        clone_url = f"https://x-access-token:{token}@github.com/{repository.owner}/{repository.name}.git"
        self._run_git(
            run, workspace.parent, ["clone", "--branch", base_branch, "--single-branch", clone_url, str(workspace)], secret=token,
        )
        self._log(run, "INFO", f"Cloned {repository.owner}/{repository.name}@{base_branch} into workspace.")
        return True

    # --- 4. Create story branch ----------------------------------------------

    def create_branch(self, run: CodeRun, workspace: Path, branch_name: str) -> None:
        self._run_git(run, workspace, ["checkout", "-b", branch_name])
        run.branch_name = branch_name
        self.set_status(run, CodeRunStatus.BRANCH_CREATED)

    # --- 5. Apply agent-generated changes -------------------------------------

    def apply_changes(self, run: CodeRun, workspace: Path, changes: list[dict]) -> None:
        """`changes` — the same shape ImplementationRun.proposed_file_changes
        already uses: [{"path", "change_type", "after_content"}, ...].
        Plain filesystem writes, not a shell command — nothing here goes
        through _run_command, so there's no command-injection surface at
        all for this step."""
        self.set_status(run, CodeRunStatus.APPLYING_CHANGES)
        for change in changes:
            rel_path = change["path"]
            # Defense in depth — a change must stay inside the workspace;
            # this never trusts a caller-supplied path to already be safe.
            target = (workspace / rel_path).resolve()
            if workspace.resolve() not in target.parents and target != workspace.resolve():
                raise CodeRunnerError(f"Refusing to write outside the workspace: {rel_path!r}")

            if change.get("change_type") == "delete":
                if target.exists():
                    target.unlink()
                    self._log(run, "INFO", f"Deleted {rel_path}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.get("after_content") or "")
            self._log(run, "INFO", f"Wrote {rel_path}")

    # --- 6. Run configured test commands + 7. capture logs --------------------

    def run_tests(self, run: CodeRun, workspace: Path, test_commands: list[str]) -> list[CommandResult]:
        """Rule 2 — each entry in `test_commands` (project/repository
        configuration, never raw end-user input routed straight through)
        is split into a real argv list and checked against
        settings.CODE_RUNNER_ALLOWED_TEST_EXECUTABLES before it's ever
        executed; anything else is refused."""
        self.set_status(run, CodeRunStatus.TESTING)
        results = []
        for raw_command in test_commands:
            argv = shlex.split(raw_command)
            if not argv or argv[0] not in self.settings.CODE_RUNNER_ALLOWED_TEST_EXECUTABLES:
                raise CodeRunnerError(f"Refusing to run non-allowlisted test command: {raw_command!r}")
            cwd = _resolve_test_command_cwd(workspace, argv[0])
            if cwd != workspace:
                self._log(run, "INFO", f"Running {argv[0]!r} from {cwd.relative_to(workspace)}/ (found its project marker there, not at the workspace root).")
            results.append(self._run_command(run, cwd, argv))
        return results

    # --- 8. Create commit -------------------------------------------------------

    def create_commit(self, run: CodeRun, workspace: Path, message: str) -> None:
        self._run_git(run, workspace, ["add", "-A"])
        self._run_git(run, workspace, ["commit", "-m", message])
        self.set_status(run, CodeRunStatus.COMMITTED)

    # --- 9. Push branch -----------------------------------------------------------

    def push_branch(self, run: CodeRun, workspace: Path, repository: Repository, branch_name: str) -> bool:
        """Returns True if a real push happened, False for the placeholder
        path (no usable GitHub credential)."""
        token = self._repository_token(repository)
        if token is None:
            self._log(run, "INFO", "No decryptable GitHub credential for this repository — push skipped (placeholder).")
            return False
        push_url = f"https://x-access-token:{token}@github.com/{repository.owner}/{repository.name}.git"
        self._run_git(run, workspace, ["push", push_url, branch_name], secret=token)
        self.set_status(run, CodeRunStatus.PUSHED)
        return True

    # --- 10. Prepare PR creation ----------------------------------------------

    def prepare_pr_creation(self, run: CodeRun, story: Story, *, base_branch: str) -> dict:
        """Assembles exactly the fields a PR would need — never calls
        GitHub itself (see module docstring: that's
        app/api/routes/implementation_runs.py's create_pull_request,
        an already-existing, separate action)."""
        return {
            "title": f"[{story.title}] Implementation",
            "head": run.branch_name,
            "base": base_branch,
            "body": (
                f"Automated implementation for story: {story.title}\n\n"
                f"Generated by CodeRunnerService run `{run.id}`.\n"
            ),
        }
