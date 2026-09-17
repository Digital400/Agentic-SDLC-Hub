"""Generates JSON Schema files for every contract in CONTRACT_REGISTRY.

Run as a script to regenerate app/agent_runtime/json_schema/*.schema.json
after any contract change:

    cd apps/api && .venv/Scripts/python.exe -m app.agent_runtime.schema_export

test_agent_runtime_schema_versioning.py::test_generated_schemas_match_committed_files
fails if the committed .schema.json files drift from what the current
models would generate — regenerate and commit the diff when that happens,
the same way a migration accompanies a model change.
"""

import json
from pathlib import Path

from app.agent_runtime.registry import CONTRACT_REGISTRY

DEFAULT_OUTPUT_DIR = Path(__file__).parent / "json_schema"


def generate_json_schemas(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Path]:
    """Writes one `<Name>.schema.json` file per registered contract.
    Returns {name: written_path}. Deterministic output (sorted keys, fixed
    indent) so regeneration produces a clean diff only when a contract
    actually changed."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name, model in CONTRACT_REGISTRY.items():
        schema = model.model_json_schema()
        path = output_dir / f"{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written[name] = path
    return written


if __name__ == "__main__":
    for name, path in sorted(generate_json_schemas().items()):
        print(f"wrote {path} ({name})")
