"""Generates `.sdlc/schemas/*.schema.json` FROM the Skill/Policy Pydantic
models — the JSON Schema a skill/policy YAML+frontmatter file's parsed
shape must satisfy. Mirrors app/agent_runtime/schema_export.py's pattern
exactly (same "generated, never hand-duplicated" rule, same
model_json_schema() mechanism).

Run as a script to regenerate after any Skill/Policy field change:

    cd apps/api && .venv/Scripts/python.exe -m app.prompt_compiler.schema_export
"""

import json
from pathlib import Path

from app.prompt_compiler.policy import Policy
from app.prompt_compiler.project import ProjectConfig
from app.prompt_compiler.skill import SDLC_ROOT, Skill

DEFAULT_OUTPUT_DIR = SDLC_ROOT / "schemas"

_REGISTRY = {"skill": Skill, "policy": Policy, "project": ProjectConfig}


def generate_schemas(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name, model in _REGISTRY.items():
        schema = model.model_json_schema()
        path = output_dir / f"{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written[name] = path
    return written


if __name__ == "__main__":
    for name, path in sorted(generate_schemas().items()):
        print(f"wrote {path} ({name})")
