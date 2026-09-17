"""Structural enforcement of this phase's "Business agents must not call
provider SDKs directly" requirement — greps each business-agent module's
own source (not its transitive imports) for a provider SDK import.

Every provider SDK call in this codebase must live behind a
ModelGateway adapter (app/model_gateway/*.py) or, on the preserved legacy
path, inside app/services/ai_generation.py itself — never inside a
business agent module.
"""

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"  # tests/../app == apps/api/app

BUSINESS_AGENT_MODULES = [
    "services/implementation_agent.py",
    "services/testing_agent.py",
    "services/pr_review_agent.py",
    "services/validator_agent.py",
    "services/artifact_summary.py",
]

# The modules PERMITTED to import a provider SDK directly — the gateway
# boundary itself, plus ai_generation.py on the preserved legacy path.
ALLOWED_SDK_IMPORTERS = {
    "services/ai_generation.py",
    "model_gateway/legacy_gateway.py",
    "model_gateway/ollama_gateway.py",
    "model_gateway/openrouter_gateway.py",
    "model_gateway/litellm_gateway.py",
}

_PROVIDER_SDK_MODULE_NAMES = {"anthropic", "ollama", "google.genai", "genai"}


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_business_agent_imports_a_provider_sdk_directly():
    for relative in BUSINESS_AGENT_MODULES:
        path = APP_DIR / relative
        assert path.is_file(), f"expected business agent module at {path}"
        imported = _imported_module_names(path)
        sdk_imports = imported & _PROVIDER_SDK_MODULE_NAMES
        assert not sdk_imports, f"{relative} imports provider SDK module(s) directly: {sdk_imports} — must go through app.services.ai_generation or app.model_gateway instead"


def test_only_the_gateway_boundary_modules_import_a_provider_sdk():
    """The inverse check — walks every .py file under app/ and asserts
    provider SDK imports are confined to the allow-list above. Catches a
    FUTURE business agent (or any other module) that starts importing a
    provider SDK directly, not just the five named today."""
    violations: dict[str, set[str]] = {}
    for path in APP_DIR.rglob("*.py"):
        relative = path.relative_to(APP_DIR).as_posix()
        if relative in ALLOWED_SDK_IMPORTERS:
            continue
        if "__pycache__" in path.parts:
            continue
        imported = _imported_module_names(path)
        sdk_imports = imported & _PROVIDER_SDK_MODULE_NAMES
        if sdk_imports:
            violations[relative] = sdk_imports

    assert not violations, f"Module(s) outside the allowed gateway boundary import a provider SDK directly: {violations}"


def test_business_agents_only_use_ai_generation_for_llm_calls():
    """Positive confirmation, not just an absence check — every business
    agent module actually imports from app.services.ai_generation (its
    real, existing call path — Phase 00 baseline section 1)."""
    for relative in BUSINESS_AGENT_MODULES:
        imported = _imported_module_names(APP_DIR / relative)
        assert "app.services.ai_generation" in imported, f"{relative} should call LLMs via app.services.ai_generation"
