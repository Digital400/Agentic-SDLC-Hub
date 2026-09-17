"""Loader for `.sdlc/project.yaml` — the compiler-wide defaults every
PromptCompiler.compile() call can fall back to (default policy set,
default token ceilings) when a caller doesn't pin its own. See
app/prompt_compiler/__init__.py.
"""

from __future__ import annotations

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.prompt_compiler.skill import SDLC_ROOT

PROJECT_YAML_PATH = SDLC_ROOT / "project.yaml"


class ProjectYamlError(Exception):
    pass


class CompilerDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_max_context_tokens: int = Field(..., ge=1)
    default_max_output_tokens: int = Field(..., ge=1)


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    name: str
    description: str
    skills_dir: str
    policies_dir: str
    schemas_dir: str
    generated_dir: str
    default_policies: list[str] = Field(default_factory=list)
    compiler: CompilerDefaults


def load_project_config() -> ProjectConfig:
    if not PROJECT_YAML_PATH.is_file():
        raise ProjectYamlError(f".sdlc/project.yaml not found at {PROJECT_YAML_PATH}")
    try:
        data = yaml.safe_load(PROJECT_YAML_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ProjectYamlError(f".sdlc/project.yaml: invalid YAML — {exc}") from exc
    try:
        return ProjectConfig(**data)
    except TypeError as exc:
        raise ProjectYamlError(f".sdlc/project.yaml: missing a required field — {exc}") from exc
