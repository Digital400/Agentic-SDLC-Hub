"""Prompt linting — static checks PromptCompiler runs over its own
compiled output before returning it. See app/prompt_compiler/compiler.py.

Two severities, same distinction this codebase already uses elsewhere
(app/agent_runtime/enums.py's CheckSeverity, app/services/validator_agent.py's
critical_issues vs suggestions): BLOCKING findings raise
PromptLintError — PromptCompiler.compile() never returns a package that
failed a BLOCKING check. ADVISORY findings are attached to the result for
a caller to see, never block compilation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.enums import DataClassification, NetworkPolicyDefault
from app.prompt_compiler.policy import Policy
from app.prompt_compiler.skill import Skill

# Known real-world secret-token SHAPES (not a generic word list — see
# app/agent_runtime's own "secret-shaped field NAME" check, which this
# mirrors but applied to rendered prompt TEXT instead of a contract's
# field names). Catches a credential that slipped into rendered content,
# not merely a field named suspiciously.
_SECRET_SHAPE_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),  # GitHub PAT
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # Anthropic/OpenAI-style API key
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack token
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT shape
]


@dataclass
class LintFinding:
    severity: str  # "BLOCKING" | "ADVISORY"
    code: str
    message: str


class PromptLintError(Exception):
    """Raised by PromptCompiler.compile() when at least one BLOCKING
    LintFinding was produced — carries the full finding list so a caller
    can see every issue, not just the first one."""

    def __init__(self, findings: list[LintFinding]):
        blocking = [f for f in findings if f.severity == "BLOCKING"]
        super().__init__("; ".join(f"[{f.code}] {f.message}" for f in blocking))
        self.findings = findings


def _find_secret_shapes(text: str) -> list[str]:
    hits = []
    for pattern in _SECRET_SHAPE_PATTERNS:
        hits += pattern.findall(text)
    return hits


def lint_compiled_text(
    *,
    skill: Skill,
    policies: list[Policy],
    rendered_text: str,
    data_classification: DataClassification,
    network_default: NetworkPolicyDefault,
    supported_tool_categories: list[str],
) -> list[LintFinding]:
    findings: list[LintFinding] = []

    secret_hits = _find_secret_shapes(rendered_text)
    if secret_hits:
        findings.append(LintFinding("BLOCKING", "secret-shaped-content", f"Rendered prompt text contains {len(secret_hits)} secret-shaped token(s) — a compiled prompt must never carry a real credential."))

    if not skill.procedure:
        findings.append(LintFinding("BLOCKING", "empty-procedure", f"Skill '{skill.skill_key}' has no procedure steps."))
    if not skill.validation_checklist:
        findings.append(LintFinding("BLOCKING", "empty-checklist", f"Skill '{skill.skill_key}' has no validation checklist."))
    if not skill.output_contract.strip():
        findings.append(LintFinding("BLOCKING", "empty-output-contract", f"Skill '{skill.skill_key}' has no output contract."))

    if skill.allowed_tool_categories and not (set(skill.allowed_tool_categories) & set(supported_tool_categories)):
        findings.append(
            LintFinding(
                "ADVISORY", "no-tool-category-overlap",
                f"Skill '{skill.skill_key}' declares allowed_tool_categories {skill.allowed_tool_categories}, "
                f"none of which the runtime's capability manifest supports ({supported_tool_categories}) — this "
                "skill may be unable to act on anything.",
            )
        )

    if data_classification in (DataClassification.CONFIDENTIAL, DataClassification.RESTRICTED) and network_default == NetworkPolicyDefault.ALLOW:
        findings.append(
            LintFinding(
                "BLOCKING", "open-network-on-sensitive-project",
                f"Project data_classification is {data_classification.value} but network_policy.default is ALLOW — "
                "a sensitive project must default-deny outbound network access.",
            )
        )

    applied_policy_keys = {p.policy_key for p in policies}
    if "security-baseline" not in applied_policy_keys:
        findings.append(LintFinding("ADVISORY", "missing-security-baseline-policy", "The 'security-baseline' policy was not among the policies applied to this compilation."))

    return findings


def raise_if_blocking(findings: list[LintFinding]) -> None:
    if any(f.severity == "BLOCKING" for f in findings):
        raise PromptLintError(findings)
