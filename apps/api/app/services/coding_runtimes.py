"""The registry of coding runtimes this platform knows about — Phase 08's
"Register OpenCode as an available but feature-flagged runtime."

This module does NOT execute anything — apps/runner (Phase 08's new
TypeScript service, containing OpenCodeRuntimeAdapter) is a standalone
process this Python codebase does not import or invoke directly. What
belongs here is purely the DECLARATION every other phase in this arc
already established a precedent for (app/agent_runtime/capability.py's
RuntimeCapabilityManifest is the exact vendor-neutral shape a runtime
describes itself with) plus this platform's own opinion on whether that
runtime is currently enabled — mirrors apps/runner/src/registry.ts's own
buildRuntimeRegistry on the TypeScript side, kept as two independent,
symmetric declarations rather than one shared module, since the two
processes never import each other's code.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.agent_runtime.capability import RuntimeCapabilityManifest
from app.core.config import get_settings


class RegisteredCodingRuntime(BaseModel):
    enabled: bool
    capability: RuntimeCapabilityManifest


def build_coding_runtime_registry() -> dict[str, RegisteredCodingRuntime]:
    settings = get_settings()
    return {
        "opencode": RegisteredCodingRuntime(
            # Registered unconditionally so an operator/UI can always see
            # "opencode" is a known runtime and inspect why it's disabled
            # (Settings.OPENCODE_RUNTIME_ENABLED, distinct from
            # EXTERNAL_CODING_RUNTIMES_ENABLED — see that setting's own
            # docstring in app/core/config.py) rather than it being invisible
            # until enabled.
            enabled=settings.OPENCODE_RUNTIME_ENABLED,
            capability=RuntimeCapabilityManifest(
                runtime_name="opencode",
                max_context_tokens=200_000,
                max_output_tokens=8_192,
                supported_tool_categories=["file_read", "file_write", "shell_command"],
                supports_structured_output=False,
                supports_streaming=True,
                response_formats=["opencode_session_transcript"],
            ),
        ),
    }
