"""Tests for the HLD stage's workflow template config.

Regression test for a real production bug: Solution Discovery's
agent_context_summary (the default P2 context every downstream stage
sees) was truncated before reaching its `## Recommendation` section, so a
re-run of the HLD agent genuinely couldn't tell which option was chosen
and asked for clarification instead of drafting — even though the full
Solution Discovery document clearly states a recommendation. Escalating
`solution_options_doc` to full content for the `hld` stage (matching how
`lld` already escalates `hld_document`) fixes this at the source.
"""

from app.services.workflow_templates import load_workflow_template


def test_hld_node_sees_solution_discoverys_full_content_not_just_a_summary():
    template = load_workflow_template("sdlc-workflow.json")
    hld = next(n for n in template["nodes"] if n["id"] == "hld")

    assert hld["requiredInputs"] == ["solution_options_doc"]
    assert "solution_options_doc" in hld.get("fullContentArtifactTypes", [])
