"""Policy — one versioned company/project rule set applied across every
(or a filtered subset of) skills. See app/prompt_compiler/__init__.py.

FILE FORMAT (`.sdlc/policies/<policy-key>/v<N>.yaml`): plain YAML, no
Markdown body — a policy is a flat rule list, not prose-shaped the way a
skill's Procedure/Examples are, so it doesn't need the frontmatter+body
split app/prompt_compiler/skill.py uses.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.prompt_compiler.skill import REPO_ROOT, SDLC_ROOT

POLICIES_DIR = SDLC_ROOT / "policies"

_VALID_SEVERITIES = {"BLOCKING", "ADVISORY"}


class PolicyFileError(Exception):
    pass


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_key: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    # ["ALL"] applies to every task type; otherwise a list of
    # WorkPacketTaskType values — same "closed vocabulary, validated at
    # load time" rule as Skill.applicable_task_types.
    applies_to: list[str] = Field(..., min_length=1)
    severity: str = Field("BLOCKING", description="BLOCKING or ADVISORY — see enums.CheckSeverity's docstring in app/agent_runtime/enums.py for the same distinction applied elsewhere in this codebase.")
    rules: list[str] = Field(..., min_length=1)

    source_path: str = ""


def _validate_policy_dict(data: dict, *, source_path: str) -> None:
    if data.get("severity", "BLOCKING") not in _VALID_SEVERITIES:
        raise PolicyFileError(f"{source_path}: severity must be one of {sorted(_VALID_SEVERITIES)}, got '{data.get('severity')}'.")
    applies_to = data.get("applies_to") or []
    if applies_to != ["ALL"]:
        from app.agent_runtime import WorkPacketTaskType

        known = {t.value for t in WorkPacketTaskType}
        unknown = [t for t in applies_to if t not in known]
        if unknown:
            raise PolicyFileError(f"{source_path}: applies_to contains unknown task type(s): {unknown}")


def load_policy_file(path: Path) -> Policy:
    if not path.is_file():
        raise PolicyFileError(f"Policy file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PolicyFileError(f"{path}: invalid YAML — {exc}") from exc

    try:
        relative = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        relative = str(path)  # see skill.py's load_skill_file for why this falls back rather than raising
    _validate_policy_dict(data, source_path=relative)

    expected_key = path.parent.name
    if data.get("policy_key") != expected_key:
        raise PolicyFileError(f"{relative}: policy_key '{data.get('policy_key')}' does not match its directory name '{expected_key}'.")

    try:
        return Policy(**data, source_path=relative)
    except TypeError as exc:
        raise PolicyFileError(f"{relative}: missing a required field — {exc}") from exc


def list_all_policy_keys() -> list[str]:
    if not POLICIES_DIR.is_dir():
        return []
    return sorted(p.name for p in POLICIES_DIR.iterdir() if p.is_dir())


def load_policy(policy_key: str, version: str | None = None) -> Policy:
    policy_dir = POLICIES_DIR / policy_key
    if not policy_dir.is_dir():
        raise PolicyFileError(f"No such policy: '{policy_key}' (expected a directory at {policy_dir}).")
    versions: dict[str, Path] = {}
    for path in sorted(policy_dir.glob("v*.yaml")):
        policy = load_policy_file(path)
        versions[policy.version] = path
    if not versions:
        raise PolicyFileError(f"Policy '{policy_key}' has no version files.")
    if version is None:
        version = max(versions, key=lambda v: tuple(int(p) for p in v.split(".")))
    if version not in versions:
        raise PolicyFileError(f"Policy '{policy_key}' has no version '{version}' — available: {sorted(versions)}.")
    return load_policy_file(versions[version])


def load_all_latest_policies() -> list[Policy]:
    return [load_policy(key) for key in list_all_policy_keys()]


def load_applicable_policies(task_type: str) -> list[Policy]:
    """Every latest-version policy whose applies_to is ['ALL'] or
    explicitly names `task_type` — the exact set PromptCompiler folds
    into a compiled prompt's P1 (policy) tier."""
    return [p for p in load_all_latest_policies() if p.applies_to == ["ALL"] or task_type in p.applies_to]
