"""Safe repository detection for the EXISTING_REPOSITORY
ProjectExecutionProfile flow — see
app/services/execution_profile_service.py's propose_from_detection.

HARD RULE (the whole reason this module is its own file, reviewable in
isolation from the rest of the profile flow): detection may only ever
READ configuration files already committed to the repository — via
app/services/github_integration.py's read-only `read_file`, the exact same
call app/services/repo_context_builder.py already uses for its own
preview — and must NEVER execute, shell out to, or otherwise invoke any
command it discovers or proposes. There is no subprocess/os.system/exec
call anywhere in this module — verified by
tests/test_execution_profile_detection.py's
test_module_never_shells_out, which greps this file's own source for
exactly that.

Every proposed command below is a STATIC STRING keyed off which
config/lockfiles were found to exist and, for a bounded few high-value
files, their parsed (never executed) content — e.g. `package.json`'s own
`scripts` object. A file's presence or declared scripts are trusted only
as *signals*; nothing here ever runs `npm run <script>` to see what it
does.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.models import RepositoryFileEntryType, RepositorySnapshot
from app.models.enums import DataClassification
from app.services import github_integration as github_api
from app.services.github_integration import GitHubIntegrationError, MAX_PREVIEWABLE_FILE_SIZE_BYTES

logger = logging.getLogger(__name__)

# Config/lockfiles this module will read the CONTENT of (bounded list —
# reading is itself bounded to files this specific, never "every file
# under some threshold"). Every other signal below is presence-only (a
# path existing in the snapshot's RepositoryFileIndex), which needs no
# read at all.
_CONTENT_READ_CANDIDATES = ("package.json", "pyproject.toml", "dockerfile", ".env.example", "env.example")

# Directories commonly used to separate a monorepo's own sub-projects —
# checked for one of the content-read candidates or a well-known lockfile
# living directly under them, to propose `working_directories`.
_COMMON_SUBPROJECT_DIRS = ("apps/api", "apps/web", "backend", "frontend", "server", "client", "api", "web")

_LANGUAGE_BY_MARKER = {
    "package.json": "TypeScript/JavaScript",
    "requirements.txt": "Python",
    "pyproject.toml": "Python",
    "pipfile": "Python",
    "go.mod": "Go",
    "cargo.toml": "Rust",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "gemfile": "Ruby",
}

_PACKAGE_MANAGER_BY_LOCKFILE = {
    "yarn.lock": "yarn",
    "package-lock.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "poetry.lock": "poetry",
    "pipfile.lock": "pipenv",
    "requirements.txt": "pip",
    "go.sum": "go modules",
    "cargo.lock": "cargo",
}

_SECURITY_SCAN_BY_LANGUAGE = {
    "Python": ["pip-audit"],
    "TypeScript/JavaScript": ["npm audit"],
    "Go": ["govulncheck ./..."],
    "Rust": ["cargo audit"],
    "Java": ["mvn dependency-check:check"],
    "Ruby": ["bundle audit"],
}

_SKILLS_BY_LANGUAGE = {
    "Python": (["Python"], ["pytest"]),
    "TypeScript/JavaScript": (["TypeScript", "JavaScript"], ["Jest"]),
    "Go": (["Go"], ["go test"]),
    "Rust": (["Rust"], ["cargo test"]),
    "Java": (["Java"], ["JUnit"]),
    "Ruby": (["Ruby"], ["RSpec"]),
}

# A baseline, safe-by-default deny list — proposed, never silently
# applied: this is one field on a DRAFT profile a Project Owner must still
# approve (see execution_profile_service.py). Kept short and generic
# rather than exhaustive; a real deny list is a security review's job, not
# a heuristic detector's.
_DEFAULT_DENIED_COMMAND_PATTERNS = ["rm -rf *", "sudo *", "curl * | sh", "wget * | sh", ": > *", "git push --force *"]

_ENV_EXAMPLE_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


@dataclass
class DetectionResult:
    default_branch: str | None = None
    working_directories: list[str] = field(default_factory=list)
    detected_languages: list[str] = field(default_factory=list)
    detected_frameworks: list[str] = field(default_factory=list)
    package_manager: str | None = None
    runtime_image: str | None = None
    install_command: str | None = None
    lint_command: str | None = None
    format_check_command: str | None = None
    type_check_command: str | None = None
    unit_test_command: str | None = None
    integration_test_command: str | None = None
    build_command: str | None = None
    approved_security_scan_commands: list[str] = field(default_factory=list)
    allowed_command_patterns: list[str] = field(default_factory=list)
    denied_command_patterns: list[str] = field(default_factory=lambda: list(_DEFAULT_DENIED_COMMAND_PATTERNS))
    allowed_paths: list[str] = field(default_factory=list)
    denied_paths: list[str] = field(default_factory=lambda: [".env", ".env.*", "**/.git/**", "**/node_modules/**", "**/.venv/**", "**/secrets/**"])
    environment_variable_names: list[str] = field(default_factory=list)
    required_coding_skills: list[str] = field(default_factory=list)
    required_testing_skills: list[str] = field(default_factory=list)
    data_classification: DataClassification = DataClassification.INTERNAL
    # Every signal detection actually used to reach the proposal above —
    # for audit/debugging ("why was this proposed"), see
    # ProjectExecutionProfile.detection_metadata's column docstring. Never
    # itself interpreted by the approval gate.
    detection_metadata: dict = field(default_factory=dict)


def _find_path(paths_lower: dict[str, str], *candidates: str) -> str | None:
    """Returns the real (original-case) path for the first candidate
    (lowercase) found anywhere in the snapshot, or None."""
    for candidate in candidates:
        for lower_path, real_path in paths_lower.items():
            if lower_path == candidate or lower_path.endswith("/" + candidate):
                return real_path
    return None


def _read_text(*, token: str | None, owner: str, name: str, path: str, ref: str, size: int | None, transport: httpx.BaseTransport | None) -> str | None:
    """A single bounded, read-only fetch — see module docstring's HARD
    RULE. Never raises; a read failure is just a missing signal, same
    resilience contract every other best-effort context fetch in this
    codebase already follows (e.g. repo_context_builder.py's own file
    reads)."""
    if not token or (size or 0) > MAX_PREVIEWABLE_FILE_SIZE_BYTES:
        return None
    try:
        result = github_api.read_file(token, owner, name, path, ref, transport=transport)
    except GitHubIntegrationError as exc:
        logger.warning("Execution profile detection: could not read '%s' (%s) — continuing without it.", path, exc)
        return None
    return result.content


def _parse_package_json(content: str) -> tuple[list[str], dict[str, str]]:
    """Returns (frameworks, scripts) — never executes a script, only reads
    the `scripts` object's own command strings as a signal for which
    lifecycle steps this project already declares for itself."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [], {}
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    frameworks = []
    for name, label in (("next", "Next.js"), ("react", "React"), ("express", "Express"), ("vue", "Vue"), ("@angular/core", "Angular"), ("nestjs", "NestJS")):
        if any(name in dep for dep in deps):
            frameworks.append(label)
    scripts = {k: v for k, v in (data.get("scripts") or {}).items() if isinstance(v, str)}
    return frameworks, scripts


def _npm_run(package_manager: str | None, script: str) -> str:
    runner = {"yarn": "yarn", "pnpm": "pnpm"}.get(package_manager or "", "npm run")
    return f"{runner} {script}" if runner != "npm run" else f"npm run {script}"


def _parse_pyproject(content: str) -> tuple[list[str], str | None]:
    """Cheap, dependency-free signal extraction — no TOML parser dependency
    exists anywhere else in this codebase (checked), so this looks for a
    handful of well-known section/tool names as plain substrings rather
    than adding one just for this. Returns (frameworks, package_manager)."""
    frameworks = []
    if "fastapi" in content.lower():
        frameworks.append("FastAPI")
    if "django" in content.lower():
        frameworks.append("Django")
    if "flask" in content.lower():
        frameworks.append("Flask")
    package_manager = "poetry" if "[tool.poetry]" in content else None
    return frameworks, package_manager


def _parse_dockerfile_image(content: str) -> str | None:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("FROM "):
            # First FROM only — a multi-stage build's later stages are a
            # detail this heuristic doesn't need to resolve; the first
            # base image is the most informative single signal.
            return stripped[5:].strip().split(" ")[0]
    return None


def _env_var_names_from_example(content: str) -> list[str]:
    """Extracts NAMES ONLY from a `.env.example`/`env.example` file — the
    right-hand side (any example/placeholder value) is deliberately never
    read into the result, even though `.env.example` files conventionally
    hold non-secret placeholder values; see module + schema HARD RULE."""
    names: list[str] = []
    for line in content.splitlines():
        match = _ENV_EXAMPLE_LINE_RE.match(line)
        if match:
            names.append(match.group(1))
    return names


def detect_execution_profile(
    *,
    snapshot: RepositorySnapshot,
    github_token: str | None,
    transport: httpx.BaseTransport | None = None,
) -> DetectionResult:
    """The one entry point — reads what it needs from `snapshot`'s already-
    indexed file tree (app/models/repository.py's RepositoryFileIndex) plus
    a bounded set of read-only content fetches, and returns a fully
    populated, PROPOSED (never auto-applied) DetectionResult.
    `github_token` may be None (e.g. a revoked/undecryptable connection) —
    detection degrades to presence-only signals rather than failing the
    whole proposal, same resilience contract as every other best-effort
    GitHub read in this codebase.
    """
    repository = snapshot.repository
    file_rows = [f for f in snapshot.files if f.entry_type == RepositoryFileEntryType.FILE]
    paths_lower = {row.path.lower(): row.path for row in file_rows}
    size_by_path = {row.path: row.size for row in file_rows}

    signals: dict[str, object] = {"snapshot_ref": snapshot.ref, "commit_sha": snapshot.commit_sha, "files_scanned": len(file_rows)}

    # --- Working directories: root, or well-known monorepo sub-dirs. -------------------
    working_directories: list[str] = []
    for sub_dir in _COMMON_SUBPROJECT_DIRS:
        if any(lower.startswith(sub_dir + "/") for lower in paths_lower):
            working_directories.append(sub_dir)
    if not working_directories:
        working_directories = ["."]
    signals["working_directories_matched"] = list(working_directories)

    # --- Languages: presence of any known marker file, anywhere in the tree. -----------
    detected_languages = sorted({lang for marker, lang in _LANGUAGE_BY_MARKER.items() if any(marker in lower for lower in paths_lower)})
    signals["language_markers_found"] = [m for m in _LANGUAGE_BY_MARKER if any(m in lower for lower in paths_lower)]

    # --- Package manager: most specific lockfile wins (checked in the fixed order below). ---
    package_manager = None
    for lockfile, manager in _PACKAGE_MANAGER_BY_LOCKFILE.items():
        if any(lockfile == lower or lower.endswith("/" + lockfile) for lower in paths_lower):
            package_manager = manager
            break
    signals["package_manager_lockfile_found"] = package_manager is not None

    detected_frameworks: list[str] = []
    lint_command = format_check_command = type_check_command = unit_test_command = build_command = None
    install_command = None

    # --- package.json: scripts are a signal, never executed. --------------------------
    package_json_path = _find_path(paths_lower, "package.json")
    if package_json_path:
        content = _read_text(
            token=github_token, owner=repository.owner, name=repository.name, path=package_json_path,
            ref=snapshot.ref, size=size_by_path.get(package_json_path), transport=transport,
        )
        if content:
            frameworks, scripts = _parse_package_json(content)
            detected_frameworks += frameworks
            signals["package_json_scripts_found"] = sorted(scripts.keys())
            install_command = install_command or {"yarn": "yarn install --frozen-lockfile", "pnpm": "pnpm install --frozen-lockfile"}.get(package_manager or "", "npm ci")
            if "lint" in scripts:
                lint_command = _npm_run(package_manager, "lint")
            if "format" in scripts or "format:check" in scripts:
                format_check_command = _npm_run(package_manager, "format:check" if "format:check" in scripts else "format")
            if "typecheck" in scripts or "type-check" in scripts:
                type_check_command = _npm_run(package_manager, "typecheck" if "typecheck" in scripts else "type-check")
            if "test" in scripts:
                unit_test_command = _npm_run(package_manager, "test")
            if "build" in scripts:
                build_command = _npm_run(package_manager, "build")

    # --- pyproject.toml: cheap substring signals, never executed. ---------------------
    pyproject_path = _find_path(paths_lower, "pyproject.toml")
    if pyproject_path:
        content = _read_text(
            token=github_token, owner=repository.owner, name=repository.name, path=pyproject_path,
            ref=snapshot.ref, size=size_by_path.get(pyproject_path), transport=transport,
        )
        if content:
            frameworks, pm = _parse_pyproject(content)
            detected_frameworks += frameworks
            package_manager = package_manager or pm
            signals["pyproject_toml_read"] = True

    if "Python" in detected_languages and unit_test_command is None:
        install_command = install_command or ("poetry install" if package_manager == "poetry" else "pip install -r requirements.txt")
        unit_test_command = "pytest -q"
        integration_test_command = "pytest -q -m integration"
        if any("ruff" in lower for lower in paths_lower) or any(p.endswith(".ruff.toml") for p in paths_lower):
            lint_command = lint_command or "ruff check ."
            format_check_command = format_check_command or "ruff format --check ."
    else:
        integration_test_command = None

    # --- Dockerfile: base image only, never built or run. ------------------------------
    runtime_image = None
    dockerfile_path = _find_path(paths_lower, "dockerfile")
    if dockerfile_path:
        content = _read_text(
            token=github_token, owner=repository.owner, name=repository.name, path=dockerfile_path,
            ref=snapshot.ref, size=size_by_path.get(dockerfile_path), transport=transport,
        )
        if content:
            runtime_image = _parse_dockerfile_image(content)
            signals["dockerfile_from_image"] = runtime_image

    # --- .env.example: NAMES ONLY, values never read into the result. ------------------
    environment_variable_names: list[str] = []
    env_example_path = _find_path(paths_lower, ".env.example", "env.example")
    if env_example_path:
        content = _read_text(
            token=github_token, owner=repository.owner, name=repository.name, path=env_example_path,
            ref=snapshot.ref, size=size_by_path.get(env_example_path), transport=transport,
        )
        if content:
            environment_variable_names = _env_var_names_from_example(content)
            signals["env_example_names_found"] = len(environment_variable_names)

    # --- Security scans + skills: derived from detected languages, never from repo content. ---
    approved_security_scan_commands: list[str] = []
    required_coding_skills: list[str] = []
    required_testing_skills: list[str] = []
    for language in detected_languages:
        approved_security_scan_commands += _SECURITY_SCAN_BY_LANGUAGE.get(language, [])
        coding, testing = _SKILLS_BY_LANGUAGE.get(language, ([], []))
        required_coding_skills += coding
        required_testing_skills += testing
    required_coding_skills += detected_frameworks

    allowed_command_patterns = [c for c in (install_command, lint_command, format_check_command, type_check_command, unit_test_command, build_command) if c]

    return DetectionResult(
        default_branch=repository.default_branch,
        working_directories=working_directories,
        detected_languages=detected_languages,
        detected_frameworks=sorted(set(detected_frameworks)),
        package_manager=package_manager,
        runtime_image=runtime_image,
        install_command=install_command,
        lint_command=lint_command,
        format_check_command=format_check_command,
        type_check_command=type_check_command,
        unit_test_command=unit_test_command,
        integration_test_command=integration_test_command,
        build_command=build_command,
        approved_security_scan_commands=sorted(set(approved_security_scan_commands)),
        allowed_command_patterns=allowed_command_patterns,
        environment_variable_names=environment_variable_names,
        required_coding_skills=sorted(set(required_coding_skills)),
        required_testing_skills=sorted(set(required_testing_skills)),
        detection_metadata=signals,
    )
