"""Snapshot/drift tests for the two generated-file pipelines in
app/prompt_compiler/: schema_export.py (.sdlc/schemas/*.schema.json) and
vendor_export.py (.sdlc/generated/*) — both must be regenerated and
committed whenever a skill/policy changes, same discipline
app/agent_runtime/schema_export.py already established in Phase 01.
"""

import json

from app.prompt_compiler.schema_export import DEFAULT_OUTPUT_DIR as SCHEMAS_DIR
from app.prompt_compiler.schema_export import generate_schemas
from app.prompt_compiler.vendor_export import DEFAULT_OUTPUT_DIR as GENERATED_DIR
from app.prompt_compiler.vendor_export import _build_catalog_summary, generate_vendor_files


# --- Schema generation ---------------------------------------------------------------------


def test_committed_schema_files_exist():
    for name in ("skill", "policy", "project"):
        assert (SCHEMAS_DIR / f"{name}.schema.json").exists(), f"missing .sdlc/schemas/{name}.schema.json"


def test_committed_schema_files_match_current_models(tmp_path):
    regenerated = generate_schemas(output_dir=tmp_path)
    for name, path in regenerated.items():
        committed_path = SCHEMAS_DIR / f"{name}.schema.json"
        assert committed_path.read_text(encoding="utf-8") == path.read_text(encoding="utf-8"), (
            f"{name}.schema.json is stale — run `cd apps/api && .venv/Scripts/python.exe -m "
            "app.prompt_compiler.schema_export` and commit the diff."
        )


def test_skill_schema_is_valid_json_schema_shape():
    schema = json.loads((SCHEMAS_DIR / "skill.schema.json").read_text(encoding="utf-8"))
    assert schema["title"] == "Skill"
    assert "procedure" in schema["properties"]
    assert "prohibited_actions" in schema["properties"]


# --- Vendor file generation ------------------------------------------------------------------


def test_all_four_vendor_files_plus_manifest_are_committed():
    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "opencode.json", "manifest.json"):
        assert (GENERATED_DIR / name).exists(), f"missing .sdlc/generated/{name}"


def test_vendor_files_are_generated_from_one_shared_catalog_not_duplicated_by_hand():
    """The HARD RULE this phase's instructions require: regenerating into
    a temp dir must reproduce (for the three content-bearing files, not
    the timestamped manifest) exactly what's committed — proof no vendor
    file was hand-edited out of sync with the canonical .sdlc/ catalog."""
    regenerated = generate_vendor_files(output_dir=GENERATED_DIR.parent / "_test_regenerated")
    try:
        for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "opencode.json"):
            committed = (GENERATED_DIR / name).read_text(encoding="utf-8")
            fresh = regenerated[name].read_text(encoding="utf-8")
            assert committed == fresh, f"{name} is stale — regenerate with app.prompt_compiler.vendor_export and commit the diff."
    finally:
        import shutil

        shutil.rmtree(GENERATED_DIR.parent / "_test_regenerated", ignore_errors=True)


def test_every_vendor_file_contains_every_skill_by_name():
    summary = _build_catalog_summary()
    agents_md = (GENERATED_DIR / "AGENTS.md").read_text(encoding="utf-8")
    for skill in summary.skills:
        assert skill.name in agents_md, f"AGENTS.md is missing skill '{skill.name}' — regenerate."


def test_opencode_json_is_valid_and_lists_every_skill_and_policy():
    data = json.loads((GENERATED_DIR / "opencode.json").read_text(encoding="utf-8"))
    assert len(data["instructions"]) == 13
    assert len(data["rules"]) == 3
    assert data["$schema"] == "https://opencode.ai/config.json"


def test_catalog_hash_is_identical_across_all_generated_files():
    """AGENTS.md/CLAUDE.md/GEMINI.md/opencode.json/manifest.json must all
    report the same catalog_hash when generated together — this IS the
    "generated from canonical sources, never manually duplicated" rule
    made checkable: divergent hashes would mean the files were produced
    from different catalog states (e.g. one hand-edited afterward)."""
    manifest = json.loads((GENERATED_DIR / "manifest.json").read_text(encoding="utf-8"))
    opencode = json.loads((GENERATED_DIR / "opencode.json").read_text(encoding="utf-8"))
    assert manifest["catalog_hash"] == opencode["catalog_hash"]

    for md_file in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        content = (GENERATED_DIR / md_file).read_text(encoding="utf-8")
        assert manifest["catalog_hash"][:16] in content, f"{md_file} doesn't carry the matching catalog hash footer."


def test_catalog_hash_is_stable_given_identical_skill_and_policy_content():
    s1 = _build_catalog_summary()
    s2 = _build_catalog_summary()
    assert s1.catalog_hash == s2.catalog_hash


def test_generated_files_carry_a_do_not_edit_header():
    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        content = (GENERATED_DIR / name).read_text(encoding="utf-8")
        assert "GENERATED FILE" in content
        assert "do not edit by hand" in content.lower()
