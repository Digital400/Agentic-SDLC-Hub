"""Tests for app/prompt_compiler/lint.py."""

import pytest

from app.models.enums import DataClassification, NetworkPolicyDefault
from app.prompt_compiler.lint import lint_compiled_text, raise_if_blocking, PromptLintError
from app.prompt_compiler.skill import load_skill
from app.prompt_compiler.policy import load_policy


def _base_kwargs(**overrides):
    defaults = dict(
        skill=load_skill("hld"),
        policies=[load_policy("security-baseline")],
        rendered_text="Some ordinary, safe prompt text with no secrets.",
        data_classification=DataClassification.INTERNAL,
        network_default=NetworkPolicyDefault.DENY,
        supported_tool_categories=["file_read"],
    )
    defaults.update(overrides)
    return defaults


def test_clean_text_produces_no_blocking_findings():
    findings = lint_compiled_text(**_base_kwargs())
    assert not any(f.severity == "BLOCKING" for f in findings)


def test_github_token_shape_is_blocking():
    text = "Use this credential: ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    findings = lint_compiled_text(**_base_kwargs(rendered_text=text))
    assert any(f.code == "secret-shaped-content" and f.severity == "BLOCKING" for f in findings)


def test_anthropic_style_key_shape_is_blocking():
    text = "sk-abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"
    findings = lint_compiled_text(**_base_kwargs(rendered_text=text))
    assert any(f.code == "secret-shaped-content" for f in findings)


def test_aws_key_shape_is_blocking():
    text = "AKIAABCDEFGHIJKLMNOP"
    findings = lint_compiled_text(**_base_kwargs(rendered_text=text))
    assert any(f.code == "secret-shaped-content" for f in findings)


def test_jwt_shape_is_blocking():
    text = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    findings = lint_compiled_text(**_base_kwargs(rendered_text=text))
    assert any(f.code == "secret-shaped-content" for f in findings)


def test_open_network_on_restricted_project_is_blocking():
    findings = lint_compiled_text(**_base_kwargs(data_classification=DataClassification.RESTRICTED, network_default=NetworkPolicyDefault.ALLOW))
    assert any(f.code == "open-network-on-sensitive-project" and f.severity == "BLOCKING" for f in findings)


def test_deny_network_on_restricted_project_is_fine():
    findings = lint_compiled_text(**_base_kwargs(data_classification=DataClassification.RESTRICTED, network_default=NetworkPolicyDefault.DENY))
    assert not any(f.code == "open-network-on-sensitive-project" for f in findings)


def test_allow_network_on_public_project_is_fine():
    findings = lint_compiled_text(**_base_kwargs(data_classification=DataClassification.PUBLIC, network_default=NetworkPolicyDefault.ALLOW))
    assert not any(f.code == "open-network-on-sensitive-project" for f in findings)


def test_missing_security_baseline_policy_is_advisory_not_blocking():
    findings = lint_compiled_text(**_base_kwargs(policies=[load_policy("coding-standards")]))
    matching = [f for f in findings if f.code == "missing-security-baseline-policy"]
    assert len(matching) == 1
    assert matching[0].severity == "ADVISORY"


def test_no_tool_category_overlap_is_advisory():
    skill = load_skill("implement-story")  # declares file_read/file_write/shell_command
    findings = lint_compiled_text(**_base_kwargs(skill=skill, supported_tool_categories=["network_request"]))
    matching = [f for f in findings if f.code == "no-tool-category-overlap"]
    assert len(matching) == 1
    assert matching[0].severity == "ADVISORY"


def test_raise_if_blocking_raises_only_when_a_blocking_finding_exists():
    from app.prompt_compiler.lint import LintFinding

    raise_if_blocking([LintFinding("ADVISORY", "x", "y")])  # no raise

    with pytest.raises(PromptLintError) as exc_info:
        raise_if_blocking([LintFinding("BLOCKING", "x", "y"), LintFinding("ADVISORY", "a", "b")])
    assert len(exc_info.value.findings) == 2
