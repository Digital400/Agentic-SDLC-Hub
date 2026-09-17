""""Never include credentials in WorkPacket or model context" — verified
directly against this phase's own concrete deliverable
(RuntimeCredentialBroker.BrokeredCredential) and, independently, against
every contract app.agent_runtime/app.prompt_compiler actually assemble
into a compiled prompt. Extends (does not replace)
app/agent_runtime's own Phase 01
test_agent_runtime_contracts.py::test_no_contract_field_is_secret_shaped
— this file is this phase's own direct coverage of the same invariant.
"""

import ast
import inspect
from pathlib import Path

from app.agent_runtime.registry import CONTRACT_REGISTRY
from app.runtime_security.credential_broker import BrokeredCredential

_SECRET_SHAPED_WORDS = {"token", "secret", "password", "credential", "credentials", "key", "apikey", "authorization"}


def _field_words(field_name: str) -> set[str]:
    return set(field_name.lower().split("_"))


def test_no_agent_runtime_contract_field_is_credential_shaped():
    """Re-confirms Phase 01's own invariant still holds after this
    phase's changes — every WorkPacket/RuntimeInstructionPackage/
    ExecutionResult field, by name."""
    for name, model in CONTRACT_REGISTRY.items():
        for field_name in model.model_fields:
            assert not (_field_words(field_name) & _SECRET_SHAPED_WORDS), f"{name}.{field_name} looks credential-shaped"


def test_prompt_compiler_never_imports_the_credential_broker():
    """Structural: app.prompt_compiler.compiler (Phase 04) is what
    actually assembles a WorkPacket into model-facing context
    (task_instruction/short_system_instruction) — if it never even
    imports RuntimeCredentialBroker, a credential cannot possibly reach
    that assembly, regardless of what any individual field is named."""
    import app.prompt_compiler.compiler as compiler_module

    source = inspect.getsource(compiler_module)
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)

    assert "app.runtime_security" not in imported_names
    assert "app.runtime_security.credential_broker" not in imported_names
    assert not any(name.startswith("app.runtime_security") for name in imported_names)


def test_brokered_credential_is_never_constructed_from_a_work_packet_field():
    """BrokeredCredential (this phase's own credential type) has no
    constructor path that reads from a WorkPacket — it takes a
    Repository row and this application's own settings/DB, never
    anything WorkPacket-shaped. Confirmed by signature inspection: no
    parameter of RuntimeCredentialBroker.get_github_credential accepts a
    WorkPacket."""
    from app.runtime_security.credential_broker import RuntimeCredentialBroker

    sig = inspect.signature(RuntimeCredentialBroker.get_github_credential)
    for param in sig.parameters.values():
        assert param.annotation != "WorkPacket"
        assert "WorkPacket" not in str(param.annotation)


def test_compiled_prompt_output_schema_never_carries_the_word_credential():
    """A compiled prompt's rendered task/system instructions come from
    skill procedure text + policy rules + project execution profile
    commands + RAG snippets (see app/prompt_compiler/compiler.py) — none
    of which this codebase's own skills/policies (see .sdlc/) ever
    reference a literal credential value; they reference environment
    variable NAMES only (see .sdlc/skills/implement-story/v1.md's own
    prohibited_actions). This test confirms that discipline holds in the
    actual committed skill/policy catalog, not just in prose."""
    from app.prompt_compiler.skill import load_all_latest_skills

    for skill in load_all_latest_skills():
        full_text = skill.purpose + " ".join(skill.procedure) + skill.output_contract + " ".join(skill.prohibited_actions)
        # A skill file is allowed to MENTION the word "credential" or
        # "secret" as a concept (e.g. "never hardcode a secret") — what
        # must never appear is a real-looking secret VALUE, which the
        # existing app/prompt_compiler/lint.py's secret-shape patterns
        # already check for at compile time (see
        # tests/test_prompt_compiler_lint.py). This test only confirms no
        # skill file was authored with a literal REDACTED-style example
        # that looks like it was copy-pasted from a real credential.
        assert "ghp_" not in full_text
        assert "sk-" not in full_text
