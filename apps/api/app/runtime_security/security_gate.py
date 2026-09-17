"""SecurityGateService — "Keep all external coding runtimes disabled
until this security gate passes," made concrete and enforced, not just
documented.

An "external coding runtime" here means: anything that executes an
AgentJob (Phase 06) OUTSIDE this process — i.e.
Settings.AGENT_JOB_DISPATCHER_MODE == "celery" (see
app/services/agent_jobs/celery_dispatcher.py). InlineJobDispatcher (the
preserved synchronous default) is never gated by this — it runs in the
same process, under the same request's own authorization context, so
there is no separate "runtime" boundary for this gate to protect.

See app/services/agent_jobs/__init__.py's select_dispatcher — the "celery"
branch calls require_security_gate_passed before ever constructing
CeleryJobDispatcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import Settings


class SecurityGateNotPassedError(Exception):
    def __init__(self, reasons: list[str]):
        super().__init__("External coding runtimes remain disabled — security gate failed: " + "; ".join(reasons))
        self.reasons = reasons


@dataclass(frozen=True)
class SecurityGateStatus:
    passed: bool
    reasons: list[str] = field(default_factory=list)


class SecurityGateService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def check(self) -> SecurityGateStatus:
        reasons: list[str] = []

        if not self.settings.EXTERNAL_CODING_RUNTIMES_ENABLED:
            reasons.append("EXTERNAL_CODING_RUNTIMES_ENABLED is False.")

        has_generic_oidc = bool(self.settings.OIDC_ISSUER and self.settings.OIDC_AUDIENCE)
        has_entra = bool(self.settings.ENTRA_TENANT_ID and self.settings.ENTRA_CLIENT_ID)
        if not (has_generic_oidc or has_entra):
            reasons.append("No OIDC or Entra ID identity provider is configured (server-side identity derivation is required before an external runtime may act).")

        if not self.settings.CELERY_BROKER_URL:
            reasons.append("CELERY_BROKER_URL is not set.")

        credential_broker_ready = bool(
            self.settings.GITHUB_APP_ID and self.settings.GITHUB_APP_PRIVATE_KEY and self.settings.GITHUB_APP_INSTALLATION_ID
        )
        if not credential_broker_ready:
            reasons.append(
                "No GitHub App is configured for RuntimeCredentialBroker — an external runtime would fall back to a "
                "long-lived PAT, which this gate does not consider safe for company-managed automated execution."
            )

        return SecurityGateStatus(passed=not reasons, reasons=reasons)


def require_security_gate_passed(settings: Settings) -> None:
    status = SecurityGateService(settings).check()
    if not status.passed:
        raise SecurityGateNotPassedError(status.reasons)
