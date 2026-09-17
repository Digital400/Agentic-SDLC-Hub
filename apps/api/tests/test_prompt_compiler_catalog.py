"""Tests for the .sdlc/ skill + policy catalog loaders — see
app/prompt_compiler/skill.py and app/prompt_compiler/policy.py.
"""

from pathlib import Path

import pytest

from app.agent_runtime import WorkPacketTaskType
from app.prompt_compiler.policy import PolicyFileError, list_all_policy_keys, load_all_latest_policies, load_applicable_policies, load_policy
from app.prompt_compiler.project import load_project_config
from app.prompt_compiler.skill import (
    SKILLS_DIR,
    Skill,
    SkillFileError,
    list_all_skill_keys,
    load_all_latest_skills,
    load_skill,
    load_skill_for_task_type,
    parse_skill_content,
)

ALL_TASK_TYPES = {t.value for t in WorkPacketTaskType}


# --- Skill catalog completeness ---------------------------------------------------------


def test_every_work_packet_task_type_has_exactly_one_skill():
    """Phase 04 requires a versioned skill for all 13 task types — this
    asserts the catalog is both COMPLETE (every task type covered) and
    UNAMBIGUOUS (no task type claimed twice)."""
    skills = load_all_latest_skills()
    covered: dict[str, list[str]] = {}
    for skill in skills:
        for task_type in skill.applicable_task_types:
            covered.setdefault(task_type, []).append(skill.skill_key)

    assert set(covered.keys()) == ALL_TASK_TYPES, f"missing task types: {ALL_TASK_TYPES - set(covered.keys())}"
    ambiguous = {t: keys for t, keys in covered.items() if len(keys) > 1}
    assert not ambiguous, f"task types claimed by more than one skill: {ambiguous}"


def test_thirteen_skills_exist():
    assert len(list_all_skill_keys()) == 13


@pytest.mark.parametrize("task_type", sorted(ALL_TASK_TYPES))
def test_load_skill_for_every_task_type(task_type):
    skill = load_skill_for_task_type(task_type)
    assert isinstance(skill, Skill)
    assert task_type in skill.applicable_task_types


@pytest.mark.parametrize("skill_key", list_all_skill_keys())
def test_every_skill_has_every_required_field_populated(skill_key):
    skill = load_skill(skill_key)
    assert skill.name
    assert skill.version
    assert skill.purpose
    assert skill.applicable_task_types
    assert skill.procedure
    assert skill.validation_checklist
    assert skill.output_contract
    assert skill.allowed_tool_categories or skill.allowed_tool_categories == []  # present as a field either way
    assert skill.prohibited_actions, f"{skill_key} declares no prohibited actions"
    assert skill.examples, f"{skill_key} has no examples"


def test_load_skill_for_task_type_raises_for_unknown_task_type():
    with pytest.raises(SkillFileError):
        load_skill_for_task_type("NOT_A_REAL_TASK_TYPE")


def test_load_skill_raises_for_unknown_skill_key():
    with pytest.raises(SkillFileError):
        load_skill("not-a-real-skill")


def test_load_skill_raises_for_unknown_version():
    with pytest.raises(SkillFileError):
        load_skill("hld", version="99.0.0")


def test_load_skill_defaults_to_highest_version():
    skill = load_skill("hld")
    assert skill.version == "1.0.0"


# --- Parser robustness -------------------------------------------------------------------


def test_parse_skill_content_rejects_missing_frontmatter():
    with pytest.raises(SkillFileError):
        parse_skill_content("# Just a heading, no frontmatter\n", source_path="fake.md")


def test_parse_skill_content_rejects_missing_procedure_section():
    content = (
        "---\nskill_key: x\nname: X\nversion: \"1.0.0\"\npurpose: p\n"
        "applicable_task_types: [HLD]\n---\n\n## Validation Checklist\n- a\n\n## Output Contract\nfoo\n"
    )
    with pytest.raises(SkillFileError, match="Procedure"):
        parse_skill_content(content, source_path="fake.md")


def test_parse_skill_content_parses_examples_correctly():
    content = (
        "---\nskill_key: x\nname: X\nversion: \"1.0.0\"\npurpose: p\n"
        "applicable_task_types: [HLD]\n---\n\n"
        "## Procedure\n1. Step one.\n\n## Validation Checklist\n- Check one.\n\n## Output Contract\nSome format.\n\n"
        "## Examples\n### Example: A scenario\n**Input:** the input text\n\n**Output:** the output text\n"
    )
    skill = parse_skill_content(content, source_path="fake.md")
    assert skill.procedure == ["Step one."]
    assert skill.validation_checklist == ["Check one."]
    assert len(skill.examples) == 1
    assert skill.examples[0].scenario == "A scenario"
    assert skill.examples[0].input == "the input text"
    assert skill.examples[0].output == "the output text"


def test_skill_key_must_match_directory_name(tmp_path, monkeypatch):
    bad_dir = tmp_path / "skills" / "actual-dir-name"
    bad_dir.mkdir(parents=True)
    (bad_dir / "v1.md").write_text(
        "---\nskill_key: wrong-key\nname: X\nversion: \"1.0.0\"\npurpose: p\n"
        "applicable_task_types: [HLD]\n---\n\n## Procedure\n1. a\n\n## Validation Checklist\n- a\n\n## Output Contract\nfoo\n",
        encoding="utf-8",
    )
    import app.prompt_compiler.skill as skill_module

    monkeypatch.setattr(skill_module, "SKILLS_DIR", tmp_path / "skills")
    with pytest.raises(SkillFileError, match="does not match"):
        skill_module.load_skill("actual-dir-name")


def test_unknown_task_type_in_frontmatter_is_rejected():
    content = (
        "---\nskill_key: x\nname: X\nversion: \"1.0.0\"\npurpose: p\n"
        "applicable_task_types: [NOT_REAL]\n---\n\n## Procedure\n1. a\n\n## Validation Checklist\n- a\n\n## Output Contract\nfoo\n"
    )
    with pytest.raises(Exception):  # pydantic ValidationError
        parse_skill_content(content, source_path="fake.md")


# --- Policy catalog ------------------------------------------------------------------------


def test_three_policies_exist():
    assert len(list_all_policy_keys()) == 3


def test_load_all_latest_policies_returns_valid_policies():
    policies = load_all_latest_policies()
    assert {p.policy_key for p in policies} == {"security-baseline", "coding-standards", "data-handling"}


def test_load_applicable_policies_includes_all_and_specific():
    applicable = load_applicable_policies("IMPLEMENT_STORY")
    keys = {p.policy_key for p in applicable}
    assert keys == {"security-baseline", "data-handling", "coding-standards"}


def test_load_applicable_policies_excludes_non_matching_specific_policy():
    applicable = load_applicable_policies("HLD")
    keys = {p.policy_key for p in applicable}
    assert "coding-standards" not in keys  # only applies to IMPLEMENT_STORY/PR_REVIEW/INFRASTRUCTURE_CHANGE
    assert "security-baseline" in keys  # ALL


def test_load_policy_raises_for_unknown_key():
    with pytest.raises(PolicyFileError):
        load_policy("not-a-real-policy")


# --- project.yaml --------------------------------------------------------------------------


def test_project_yaml_loads_and_declares_default_policies():
    config = load_project_config()
    assert config.schema_version == "1.0.0"
    assert "security-baseline" in config.default_policies
    assert config.compiler.default_max_context_tokens > 0


# --- Directory structure ------------------------------------------------------------------


def test_sdlc_directory_structure_exists():
    from app.prompt_compiler.skill import SDLC_ROOT

    assert (SDLC_ROOT / "project.yaml").is_file()
    assert (SDLC_ROOT / "policies").is_dir()
    assert (SDLC_ROOT / "skills").is_dir()
    assert (SDLC_ROOT / "schemas").is_dir()
    assert (SDLC_ROOT / "generated").is_dir()
