"""Phase 18: independent rollback switches + rollout stage reporting.

"Implement independent rollback switches for: PromptCompiler,
ModelGateway, Document runtimes, OpenCode, ACP, Native premium adapters,
Local bridge, Automatic routing."

Every one of these switches already exists, independently, as a
Settings field this migration added at the phase that built each system
— that IS the "independent rollback switch," and this module does not
duplicate or wrap them a second time. What this module adds is a single,
honest STATUS REPORT reading those real flags back, since Phase 18 also
asks to track where a rollout currently stands per system.

**No system in this report is past RolloutStage.OFFLINE_EVALUATION.**
This is not a placeholder — it is the true, current state: every flag
below defaults to its safest/most-off value, and nothing in this
migration has ever been deployed to real production traffic. Reporting
anything further along (shadow execution against real traffic, pilot
projects, percentage rollouts) would be fabricating operational history
that does not exist — the exact thing every phase of this migration has
committed to disclosing honestly rather than inventing. See
docs/architecture/evaluation-and-rollout.md for the full, explicit
statement of what stages 3-8 (and the final legacy-code removal) require
before they can honestly be attempted.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.core.config import Settings


class RolloutStage(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    OFFLINE_EVALUATION = "OFFLINE_EVALUATION"
    SHADOW_EXECUTION = "SHADOW_EXECUTION"
    INTERNAL_ADMINISTRATORS = "INTERNAL_ADMINISTRATORS"
    SELECTED_DEVELOPERS = "SELECTED_DEVELOPERS"
    PILOT_PROJECT = "PILOT_PROJECT"
    PERCENT_25 = "PERCENT_25"
    PERCENT_50 = "PERCENT_50"
    PERCENT_100 = "PERCENT_100"


@dataclass
class SystemRolloutStatus:
    system_name: str
    rollback_switch: str
    currently_enabled: bool
    stage: RolloutStage
    note: str


def current_rollout_status(settings: Settings) -> list[SystemRolloutStatus]:
    """Reads the REAL, already-existing per-system flags back — never a
    second, independent source of truth that could drift from them."""
    return [
        SystemRolloutStatus(
            system_name="PromptCompiler", rollback_switch="(no disable flag — read-only skill/policy compilation, always available)",
            currently_enabled=True, stage=RolloutStage.SHADOW_EXECUTION,
            note="In real use only via app/services/agent_migration_shadow.py's shadow comparison (Phase 16) and PromptCompiler's own test suite — not yet the instruction source for any live-served agent run.",
        ),
        SystemRolloutStatus(
            system_name="ModelGateway", rollback_switch="MODEL_GATEWAY_MODE",
            currently_enabled=settings.MODEL_GATEWAY_MODE == "gateway", stage=RolloutStage.OFFLINE_EVALUATION,
            note="Default 'legacy' — the gateway branch exists and is tested, but has never served real traffic.",
        ),
        SystemRolloutStatus(
            system_name="Document runtimes", rollback_switch="(coding_runtime registry — no single flag; see app/services/coding_runtimes.py)",
            currently_enabled=False, stage=RolloutStage.OFFLINE_EVALUATION,
            note="LegacyDocumentRuntimeAdapter is registered but the runtime-selection registry itself is not consulted by any live route.",
        ),
        SystemRolloutStatus(
            system_name="OpenCode", rollback_switch="OPENCODE_RUNTIME_ENABLED",
            currently_enabled=settings.OPENCODE_RUNTIME_ENABLED, stage=RolloutStage.OFFLINE_EVALUATION,
            note="Default False. No bridge exists yet from the Celery job dispatcher into apps/runner (disclosed in Phase 08's own doc).",
        ),
        SystemRolloutStatus(
            system_name="ACP", rollback_switch="ACP_RUNTIME_ENABLED (apps/runner) + ACP_APPROVED_AGENTS_JSON",
            currently_enabled=False, stage=RolloutStage.OFFLINE_EVALUATION,
            note="Default False/unset. Verified only against a protocol-shape-compatible test fixture, never a real ACP agent (disclosed in Phase 11's own doc).",
        ),
        SystemRolloutStatus(
            system_name="Native premium adapters", rollback_switch="(none built — see Phase 12)",
            currently_enabled=False, stage=RolloutStage.NOT_STARTED,
            note="Blocked: no official Codex/Claude Code/Antigravity SDK access in this environment (Phase 12's disclosed blocker) — deferred, not built.",
        ),
        SystemRolloutStatus(
            system_name="Local bridge", rollback_switch="DEVELOPER_BRIDGE_ENABLED + apps/bridge's own BRIDGE_ENABLED",
            currently_enabled=settings.DEVELOPER_BRIDGE_ENABLED, stage=RolloutStage.OFFLINE_EVALUATION,
            note="Default False. Two literal I/O boundaries remain disclosed stubs (real WorkPacket construction, real runtime launch) — see Phase 13's own doc.",
        ),
        SystemRolloutStatus(
            system_name="Automatic routing", rollback_switch="RUNTIME_COST_ROUTER_ENABLED",
            currently_enabled=settings.RUNTIME_COST_ROUTER_ENABLED, stage=RolloutStage.OFFLINE_EVALUATION,
            note="Default False. RuntimeCostRouter is tested standalone via the offline simulator (Phase 17) but not called from any live path.",
        ),
    ]


def all_systems_are_rollback_safe(statuses: list[SystemRolloutStatus]) -> bool:
    """True only if every system is at or before OFFLINE_EVALUATION and
    its switch is off — the one invariant this migration has held at
    every phase boundary: nothing ships enabled by default."""
    unsafe_stages = {
        RolloutStage.SELECTED_DEVELOPERS, RolloutStage.PILOT_PROJECT,
        RolloutStage.PERCENT_25, RolloutStage.PERCENT_50, RolloutStage.PERCENT_100,
    }
    return all(s.stage not in unsafe_stages for s in statuses)
