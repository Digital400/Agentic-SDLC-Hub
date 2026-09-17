"""Skill — one versioned, canonical source-of-truth document for how to
perform one WorkPacketTaskType. See app/prompt_compiler/__init__.py's
module docstring for how this fits into PromptCompiler.

FILE FORMAT (`.sdlc/skills/<skill-key>/v<N>.md`): a YAML frontmatter block
(machine-critical, structured fields) followed by Markdown `##` sections
(prose-shaped content) — the same two-layer shape
packages/prompts/agents/*.md already uses informally (a table of metadata
+ `##` sections), made machine-parseable here because PromptCompiler
actually loads these at compile time, unlike packages/prompts (see that
package's own README: "No agent engine calls this package yet").

VERSIONING: one directory per skill_key, one file per version
(`v1.md`, `v2.md`, ...) — never edited in place once referenced by a
compiled RuntimeInstructionPackage (see WorkPacket in a golden-eval
fixture, or a compiler cache hash, that named an exact version). Adding
`v2.md` beside `v1.md` is how a skill changes; `v1.md` stays exactly what
it always was.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.markdown_sections import find_section

REPO_ROOT = Path(__file__).resolve().parents[4]
SDLC_ROOT = REPO_ROOT / ".sdlc"
SKILLS_DIR = SDLC_ROOT / "skills"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_VERSION_FILENAME_RE = re.compile(r"^v(\d+)\.md$")


class SkillFileError(Exception):
    """A skill file is missing, malformed, or fails its own schema."""


class SkillExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: str = Field(..., min_length=1)
    input: str = Field(..., min_length=1)
    output: str = Field(..., min_length=1)


class Skill(BaseModel):
    """The fully parsed, validated form of one `.sdlc/skills/<key>/vN.md`
    file — every field this phase's instructions require, no more:
    name, version, purpose, applicable task types, required inputs,
    procedure, validation checklist, output contract, prohibited actions,
    allowed tool categories, short examples."""

    model_config = ConfigDict(extra="forbid")

    skill_key: str = Field(..., min_length=1, description="Directory name under .sdlc/skills/ — stable identity across versions.")
    name: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1, description="e.g. '1.0.0' — matched against the filename (vN.md) at load time; see load_skill's consistency check.")
    purpose: str = Field(..., min_length=1)
    applicable_task_types: list[str] = Field(..., min_length=1, description="One or more app.agent_runtime.WorkPacketTaskType values this skill applies to.")
    required_inputs: list[str] = Field(default_factory=list)
    allowed_tool_categories: list[str] = Field(default_factory=list, description="Coarse categories, e.g. 'file_read' — intersected against RuntimeCapabilityManifest.supported_tool_categories at compile time (see compiler.py), never trusted alone.")
    prohibited_actions: list[str] = Field(default_factory=list)

    procedure: list[str] = Field(..., min_length=1, description="Ordered steps — parsed from the '## Procedure' body section, one entry per numbered/bulleted line.")
    validation_checklist: list[str] = Field(..., min_length=1, description="Parsed from '## Validation Checklist'.")
    output_contract: str = Field(..., min_length=1, description="Parsed from '## Output Contract' — kept as prose (a format description), not further structured, since output shapes vary per skill (Markdown headings for a drafting skill, a JSON key list for a structured skill).")
    examples: list[SkillExample] = Field(default_factory=list)

    source_path: str = Field(..., description="Relative path this skill was loaded from, for audit/debugging — not part of the file's own frontmatter.")

    @field_validator("applicable_task_types")
    @classmethod
    def _known_task_types(cls, v: list[str]) -> list[str]:
        from app.agent_runtime import WorkPacketTaskType

        known = {t.value for t in WorkPacketTaskType}
        unknown = [t for t in v if t not in known]
        if unknown:
            raise ValueError(f"applicable_task_types contains unknown WorkPacketTaskType value(s): {unknown}")
        return v


def _parse_list_lines(text: str) -> list[str]:
    """Turns a Markdown bullet/numbered list body into a plain list of
    strings — strips the leading '-'/'1.'/'[ ]' marker, keeps everything
    else verbatim. A blank body yields an empty list, never a crash."""
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^-\s*\[[ xX]\]\s*", "", stripped)  # "- [ ] " / "- [x] " checklist marker
        stripped = re.sub(r"^(-|\*|\d+\.)\s+", "", stripped)  # "- " / "* " / "1. "
        items.append(stripped)
    return items


def _parse_examples(text: str) -> list[SkillExample]:
    """Parses one or more '### Example: <scenario>' subsections, each with
    a '**Input:**' and '**Output:**' line/block."""
    examples: list[SkillExample] = []
    blocks = re.split(r"^###\s+Example:\s*(.*)$", text, flags=re.MULTILINE)
    # re.split with a capturing group interleaves [prefix, scenario1, body1, scenario2, body2, ...]
    for i in range(1, len(blocks), 2):
        scenario = blocks[i].strip()
        body = blocks[i + 1] if i + 1 < len(blocks) else ""
        input_match = re.search(r"\*\*Input:\*\*\s*(.*?)(?=\*\*Output:\*\*|\Z)", body, re.DOTALL)
        output_match = re.search(r"\*\*Output:\*\*\s*(.*)", body, re.DOTALL)
        if scenario and input_match and output_match:
            examples.append(
                SkillExample(scenario=scenario, input=input_match.group(1).strip(), output=output_match.group(1).strip())
            )
    return examples


def parse_skill_content(content: str, *, source_path: str) -> Skill:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise SkillFileError(f"{source_path}: missing YAML frontmatter (expected a leading '---' block).")
    frontmatter_text, body = match.groups()
    try:
        frontmatter: dict[str, Any] = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        raise SkillFileError(f"{source_path}: invalid YAML frontmatter — {exc}") from exc

    procedure_section = find_section(body, "Procedure")
    checklist_section = find_section(body, "Validation Checklist")
    output_contract_section = find_section(body, "Output Contract")
    examples_section = find_section(body, "Examples")

    if procedure_section is None:
        raise SkillFileError(f"{source_path}: missing required '## Procedure' section.")
    if checklist_section is None:
        raise SkillFileError(f"{source_path}: missing required '## Validation Checklist' section.")
    if output_contract_section is None:
        raise SkillFileError(f"{source_path}: missing required '## Output Contract' section.")

    try:
        return Skill(
            **frontmatter,
            procedure=_parse_list_lines(procedure_section["content"]),
            validation_checklist=_parse_list_lines(checklist_section["content"]),
            output_contract=output_contract_section["content"],
            examples=_parse_examples(examples_section["content"]) if examples_section else [],
            source_path=source_path,
        )
    except TypeError as exc:
        raise SkillFileError(f"{source_path}: frontmatter is missing a required field — {exc}") from exc


def load_skill_file(path: Path) -> Skill:
    if not path.is_file():
        raise SkillFileError(f"Skill file not found: {path}")
    content = path.read_text(encoding="utf-8")
    try:
        relative = path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        # Outside the repo entirely (e.g. a test fixture in a tmp dir) —
        # source_path is purely informational, so fall back to the
        # absolute path rather than failing to load a real, valid file.
        relative = str(path)
    skill = parse_skill_content(content, source_path=relative)

    filename_match = _VERSION_FILENAME_RE.match(path.name)
    if filename_match:
        expected_major = filename_match.group(1)
        actual_major = skill.version.split(".")[0]
        if actual_major != expected_major:
            raise SkillFileError(
                f"{relative}: file is named v{expected_major}.md but frontmatter version is '{skill.version}' "
                f"(major version must match the filename)."
            )
    expected_key = path.parent.name
    if skill.skill_key != expected_key:
        raise SkillFileError(f"{relative}: frontmatter skill_key '{skill.skill_key}' does not match its directory name '{expected_key}'.")

    return skill


@dataclass(frozen=True)
class SkillVersionRef:
    skill_key: str
    version: str  # e.g. "1.0.0" — matches a Skill.version, not necessarily the filename's major-only number


def _available_versions(skill_key: str) -> dict[str, Path]:
    """version string -> file path, for every vN.md under this skill's
    directory (loaded just far enough to read `version` from frontmatter)."""
    skill_dir = SKILLS_DIR / skill_key
    if not skill_dir.is_dir():
        raise SkillFileError(f"No such skill: '{skill_key}' (expected a directory at {skill_dir}).")
    versions: dict[str, Path] = {}
    for path in sorted(skill_dir.glob("v*.md")):
        skill = load_skill_file(path)
        versions[skill.version] = path
    if not versions:
        raise SkillFileError(f"Skill '{skill_key}' has no version files (expected .sdlc/skills/{skill_key}/v1.md at minimum).")
    return versions


def load_skill(skill_key: str, version: str | None = None) -> Skill:
    """Loads one skill at a specific version, or its highest available
    version when `version` is omitted. Raises SkillFileError for an
    unknown skill_key or an unknown version — PromptCompiler is expected
    to treat both as a hard compile-time failure (never silently fall
    back to a different version than the one a WorkPacket pinned)."""
    versions = _available_versions(skill_key)
    if version is None:
        version = max(versions, key=lambda v: tuple(int(p) for p in v.split(".")))
    if version not in versions:
        raise SkillFileError(f"Skill '{skill_key}' has no version '{version}' — available: {sorted(versions)}.")
    return load_skill_file(versions[version])


def load_skill_for_task_type(task_type: str, version: str | None = None) -> Skill:
    """Resolves task_type -> skill_key by scanning every skill directory's
    latest version for one whose applicable_task_types includes
    `task_type` — see list_all_skill_keys for the full catalog. Raises
    SkillFileError if none, or more than one, skill claims this task type
    (an ambiguous catalog is a compile-time error, not a silent pick)."""
    matches = [key for key in list_all_skill_keys() if task_type in load_skill(key).applicable_task_types]
    if not matches:
        raise SkillFileError(f"No skill in the catalog declares applicable_task_types including '{task_type}'.")
    if len(matches) > 1:
        raise SkillFileError(f"More than one skill claims task type '{task_type}': {matches} — the catalog must be unambiguous.")
    return load_skill(matches[0], version=version)


def list_all_skill_keys() -> list[str]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(p.name for p in SKILLS_DIR.iterdir() if p.is_dir())


def load_all_latest_skills() -> list[Skill]:
    return [load_skill(key) for key in list_all_skill_keys()]
