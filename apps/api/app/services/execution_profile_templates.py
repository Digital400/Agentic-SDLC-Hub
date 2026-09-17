"""The approved company stack/template catalog for the NEW_PROJECT
ProjectExecutionProfile flow — see
app/services/execution_profile_service.py's propose_from_template.

Static, in-process data (mirrors app/services/workflow_templates.py's role
for workflow graphs, but small enough — a handful of known-good stacks, not
a large per-project graph — that a Python constant is clearer than a JSON
file to load/cache/validate). "Approved" here means exactly this: the only
templates a NEW_PROJECT profile may ever be generated from are the ones
listed in APPROVED_STACK_TEMPLATES below — see propose_from_template's
`ExecutionProfileTemplateError` for what happens with any other key.

Every command here is a STATIC, REVIEWED string — never executed by this
module or by execution_profile_service.py (same hard rule as
execution_profile_detection.py's detection path: this codebase proposes
commands as data, it never runs them).
"""

from dataclasses import dataclass, field

from app.models.enums import DataClassification


@dataclass(frozen=True)
class ExecutionProfileTemplate:
    template_key: str
    name: str
    description: str
    languages: list[str]
    frameworks: list[str]
    package_manager: str | None
    runtime_image: str | None
    default_branch: str = "main"
    working_directories: list[str] = field(default_factory=list)
    install_command: str | None = None
    lint_command: str | None = None
    format_check_command: str | None = None
    type_check_command: str | None = None
    unit_test_command: str | None = None
    integration_test_command: str | None = None
    build_command: str | None = None
    approved_security_scan_commands: list[str] = field(default_factory=list)
    branch_naming_convention: str = "feature/<ticket-id>-<short-description>"
    commit_convention: str = "Conventional Commits (https://www.conventionalcommits.org)"
    required_coding_skills: list[str] = field(default_factory=list)
    required_testing_skills: list[str] = field(default_factory=list)
    data_classification: DataClassification = DataClassification.INTERNAL


# Deliberately small and reviewed — adding a template here is the only way
# a NEW_PROJECT profile can ever be TEMPLATE-sourced (see
# ProjectExecutionProfileSource), so this list IS the company's approved
# stack catalog, not a suggestion.
APPROVED_STACK_TEMPLATES: dict[str, ExecutionProfileTemplate] = {
    "python-fastapi-postgres": ExecutionProfileTemplate(
        template_key="python-fastapi-postgres",
        name="Python / FastAPI / PostgreSQL",
        description="A FastAPI backend with SQLAlchemy + Alembic migrations against PostgreSQL — this codebase's own backend stack (apps/api).",
        languages=["Python"],
        frameworks=["FastAPI", "SQLAlchemy", "Alembic", "Pydantic"],
        package_manager="pip",
        runtime_image="python:3.13-slim",
        working_directories=["apps/api"],
        install_command="pip install -r requirements.txt",
        lint_command="ruff check .",
        format_check_command="ruff format --check .",
        type_check_command="mypy app",
        unit_test_command="pytest -q",
        integration_test_command="pytest -q -m integration",
        build_command="N/A — interpreted; no build step.",
        approved_security_scan_commands=["pip-audit", "bandit -r app"],
        required_coding_skills=["Python", "FastAPI", "SQLAlchemy", "REST API design"],
        required_testing_skills=["pytest", "API testing"],
    ),
    "node-nextjs-typescript": ExecutionProfileTemplate(
        template_key="node-nextjs-typescript",
        name="Node.js / Next.js / TypeScript",
        description="A Next.js/TypeScript frontend — this codebase's own frontend stack (apps/web).",
        languages=["TypeScript", "JavaScript"],
        frameworks=["Next.js", "React"],
        package_manager="yarn",
        runtime_image="node:20-slim",
        working_directories=["apps/web"],
        install_command="yarn install --frozen-lockfile",
        lint_command="yarn lint",
        format_check_command="yarn prettier --check .",
        type_check_command="yarn tsc --noEmit",
        unit_test_command="yarn test",
        integration_test_command="yarn test:e2e",
        build_command="yarn build",
        approved_security_scan_commands=["yarn audit"],
        required_coding_skills=["TypeScript", "React", "Next.js"],
        required_testing_skills=["Jest", "component testing"],
    ),
    "python-django-postgres": ExecutionProfileTemplate(
        template_key="python-django-postgres",
        name="Python / Django / PostgreSQL",
        description="A Django monolith against PostgreSQL, for a project that wants a batteries-included backend framework instead of FastAPI's own thinner stack.",
        languages=["Python"],
        frameworks=["Django", "Django REST Framework"],
        package_manager="pip",
        runtime_image="python:3.13-slim",
        working_directories=["."],
        install_command="pip install -r requirements.txt",
        lint_command="ruff check .",
        format_check_command="ruff format --check .",
        type_check_command="mypy .",
        unit_test_command="python manage.py test",
        integration_test_command="python manage.py test --tag=integration",
        build_command="python manage.py collectstatic --noinput",
        approved_security_scan_commands=["pip-audit", "bandit -r ."],
        required_coding_skills=["Python", "Django", "REST API design"],
        required_testing_skills=["Django TestCase", "API testing"],
    ),
}


class ExecutionProfileTemplateError(Exception):
    """Raised when a caller names a template_key not in
    APPROVED_STACK_TEMPLATES — this IS the enforcement of "approved
    company stack/template only," not merely documentation of it."""


def get_approved_template(template_key: str) -> ExecutionProfileTemplate:
    template = APPROVED_STACK_TEMPLATES.get(template_key)
    if template is None:
        approved = ", ".join(sorted(APPROVED_STACK_TEMPLATES))
        raise ExecutionProfileTemplateError(f"'{template_key}' is not an approved company stack/template. Approved: {approved}.")
    return template


def list_approved_templates() -> list[ExecutionProfileTemplate]:
    return [APPROVED_STACK_TEMPLATES[key] for key in sorted(APPROVED_STACK_TEMPLATES)]
