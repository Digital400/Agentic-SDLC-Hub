"""Generates optional runtime-specific instruction files under
`.sdlc/generated/` FROM the canonical `.sdlc/skills/` + `.sdlc/policies/`
catalog — AGENTS.md-compatible instructions, CLAUDE.md, GEMINI.md, and an
OpenCode configuration file.

HARD RULE (this phase's instruction, enforced structurally by this
module's own shape): every vendor file below is rendered from exactly one
shared data structure (`_CatalogSummary`, built once by
`_build_catalog_summary()`) — no vendor renderer below reads
`.sdlc/skills/`/`.sdlc/policies/` directly, and no vendor file's content
is typed out by hand anywhere in this codebase. Regenerating after a
skill/policy change is the only way any of these four files ever changes.

Run as a script to regenerate:

    cd apps/api && .venv/Scripts/python.exe -m app.prompt_compiler.vendor_export
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.prompt_compiler.policy import Policy, load_all_latest_policies
from app.prompt_compiler.skill import SDLC_ROOT, Skill, load_all_latest_skills

DEFAULT_OUTPUT_DIR = SDLC_ROOT / "generated"

_GENERATED_HEADER = (
    "<!-- GENERATED FILE — do not edit by hand. Source of truth: .sdlc/skills/ "
    "and .sdlc/policies/, rendered by apps/api/app/prompt_compiler/vendor_export.py. "
    "Regenerate with: cd apps/api && .venv/Scripts/python.exe -m app.prompt_compiler.vendor_export -->"
)


@dataclass(frozen=True)
class _CatalogSummary:
    skills: tuple[Skill, ...]
    policies: tuple[Policy, ...]
    catalog_hash: str  # stable given identical skill+policy content — see _hash_catalog


def _hash_catalog(skills: list[Skill], policies: list[Policy]) -> str:
    digest = hashlib.sha256()
    for skill in sorted(skills, key=lambda s: s.skill_key):
        digest.update(f"{skill.skill_key}@{skill.version}".encode())
        digest.update(skill.model_dump_json().encode())
    for policy in sorted(policies, key=lambda p: p.policy_key):
        digest.update(f"{policy.policy_key}@{policy.version}".encode())
        digest.update(policy.model_dump_json().encode())
    return digest.hexdigest()


def _build_catalog_summary() -> _CatalogSummary:
    skills = load_all_latest_skills()
    policies = load_all_latest_policies()
    return _CatalogSummary(
        skills=tuple(sorted(skills, key=lambda s: s.skill_key)),
        policies=tuple(sorted(policies, key=lambda p: p.policy_key)),
        catalog_hash=_hash_catalog(skills, policies),
    )


def _render_skill_section(skill: Skill, *, heading_level: str) -> str:
    lines = [
        f"{heading_level} {skill.name} (`{skill.skill_key}`, v{skill.version})",
        "",
        f"**Applies to:** {', '.join(skill.applicable_task_types)}",
        "",
        skill.purpose.strip(),
        "",
        "**Never:**",
    ]
    lines += [f"- {action}" for action in skill.prohibited_actions] or ["- (no explicit prohibitions declared)"]
    return "\n".join(lines)


def _render_policy_section(policy: Policy, *, heading_level: str) -> str:
    lines = [f"{heading_level} {policy.name} (`{policy.policy_key}`, v{policy.version}, {policy.severity})", "", policy.description.strip(), ""]
    lines += [f"- {rule}" for rule in policy.rules]
    return "\n".join(lines)


def _render_markdown(summary: _CatalogSummary, *, title: str, intro: str) -> str:
    parts = [_GENERATED_HEADER, "", f"# {title}", "", intro, "", "## Skills", ""]
    parts += [_render_skill_section(s, heading_level="###") for s in summary.skills]
    parts += ["", "## Policies", ""]
    parts += [_render_policy_section(p, heading_level="###") for p in summary.policies]
    parts += ["", f"_Catalog hash: `{summary.catalog_hash[:16]}`_"]
    return "\n\n".join(p for p in parts if p != "") + "\n"


def render_agents_md(summary: _CatalogSummary) -> str:
    """AGENTS.md-compatible — the emerging cross-vendor convention (a
    plain-Markdown instructions file any AGENTS.md-aware coding tool can
    read), so this file is deliberately vendor-neutral prose, same as
    every app/agent_runtime contract's own hard rule 1."""
    return _render_markdown(
        summary, title="Agent Instructions",
        intro=(
            "This repository's skills and policies, compiled for any AGENTS.md-compatible coding "
            "agent. Each skill below applies to one WorkPacketTaskType (see app/agent_runtime); each "
            "policy applies across one or more of them. This file is generated — see the header above."
        ),
    )


def render_claude_md(summary: _CatalogSummary) -> str:
    return _render_markdown(
        summary, title="CLAUDE.md — Agentic SDLC Hub",
        intro=(
            "Instructions for Claude Code (or any Claude-based runtime) working in this repository. "
            "Generated from the same canonical `.sdlc/skills/`/`.sdlc/policies/` catalog as every other "
            "vendor instruction file here — see AGENTS.md for the vendor-neutral version of this content."
        ),
    )


def render_gemini_md(summary: _CatalogSummary) -> str:
    return _render_markdown(
        summary, title="GEMINI.md — Agentic SDLC Hub",
        intro=(
            "Instructions for Gemini CLI / Antigravity (or any Gemini-based runtime) working in this "
            "repository. Generated from the same canonical catalog as AGENTS.md/CLAUDE.md."
        ),
    )


def render_opencode_config(summary: _CatalogSummary) -> dict:
    """OpenCode's own configuration is JSON, not prose — same catalog,
    rendered as structured `instructions`/`rules` arrays instead of
    Markdown sections."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "generated_by": "apps/api/app/prompt_compiler/vendor_export.py",
        "catalog_hash": summary.catalog_hash,
        "instructions": [
            {
                "skill_key": s.skill_key,
                "name": s.name,
                "version": s.version,
                "applicable_task_types": list(s.applicable_task_types),
                "purpose": s.purpose.strip(),
                "prohibited_actions": list(s.prohibited_actions),
                "allowed_tool_categories": list(s.allowed_tool_categories),
            }
            for s in summary.skills
        ],
        "rules": [
            {"policy_key": p.policy_key, "name": p.name, "version": p.version, "severity": p.severity, "applies_to": list(p.applies_to), "rules": list(p.rules)}
            for p in summary.policies
        ],
    }


def generate_vendor_files(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = _build_catalog_summary()

    files = {
        "AGENTS.md": render_agents_md(summary),
        "CLAUDE.md": render_claude_md(summary),
        "GEMINI.md": render_gemini_md(summary),
        "opencode.json": json.dumps(render_opencode_config(summary), indent=2, sort_keys=True) + "\n",
    }
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalog_hash": summary.catalog_hash,
        "skill_count": len(summary.skills),
        "policy_count": len(summary.policies),
        "files": sorted(files.keys()),
    }
    files["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True) + "\n"

    written: dict[str, Path] = {}
    for filename, content in files.items():
        path = output_dir / filename
        path.write_text(content, encoding="utf-8")
        written[filename] = path
    return written


if __name__ == "__main__":
    for name, path in sorted(generate_vendor_files().items()):
        print(f"wrote {path} ({name})")
