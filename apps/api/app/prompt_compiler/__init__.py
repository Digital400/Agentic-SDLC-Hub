"""PromptCompiler — Phase 04's canonical skill/policy compiler.

Reads the versioned catalog under `.sdlc/` (skills/, policies/) and
combines it with a WorkPacket, a RuntimeCapabilityManifest, an approved
ProjectExecutionProfile, and selected RAG references into one
RuntimeInstructionPackage — see compiler.py's module docstring for the
full input/output contract and its "additive, nothing calls this yet"
status relative to the existing AgentPrompt/DB-driven system.

    .sdlc/
      project.yaml       — compiler-wide defaults (see project.py)
      policies/<key>/vN.yaml
      skills/<key>/vN.md
      schemas/            — JSON Schemas generated FROM the Skill/Policy
                             Pydantic models below (see schema_export.py)
      generated/           — vendor-specific instruction files generated
                             FROM the skill/policy catalog (see
                             vendor_export.py) — never hand-duplicated.

Package layout:
    skill.py         — Skill model + `.sdlc/skills/` loader
    policy.py          — Policy model + `.sdlc/policies/` loader
    project.py           — project.yaml loader
    compiler.py            — PromptCompiler itself
    lint.py                  — prompt linting (BLOCKING vs ADVISORY findings)
    schema_export.py           — writes .sdlc/schemas/*.json
    vendor_export.py             — writes .sdlc/generated/*
"""

from app.prompt_compiler.compiler import CompiledPrompt, PromptCompiler, PromptCompilerError, dedupe_instructions
from app.prompt_compiler.lint import LintFinding, PromptLintError
from app.prompt_compiler.policy import Policy, PolicyFileError, load_all_latest_policies, load_applicable_policies, load_policy
from app.prompt_compiler.project import ProjectConfig, ProjectYamlError, load_project_config
from app.prompt_compiler.skill import Skill, SkillExample, SkillFileError, list_all_skill_keys, load_all_latest_skills, load_skill, load_skill_for_task_type

__all__ = [
    "CompiledPrompt",
    "PromptCompiler",
    "PromptCompilerError",
    "dedupe_instructions",
    "LintFinding",
    "PromptLintError",
    "Policy",
    "PolicyFileError",
    "load_all_latest_policies",
    "load_applicable_policies",
    "load_policy",
    "ProjectConfig",
    "ProjectYamlError",
    "load_project_config",
    "Skill",
    "SkillExample",
    "SkillFileError",
    "list_all_skill_keys",
    "load_all_latest_skills",
    "load_skill",
    "load_skill_for_task_type",
]
