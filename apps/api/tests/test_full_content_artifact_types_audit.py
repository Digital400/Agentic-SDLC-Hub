"""Regression test for a recurring real bug: a stage whose required input
is a long, structured upstream document (not just a short summary) needs
that input's FULL content, not the default P2 agent_context_summary —
build_prioritized_context only escalates to full content for artifact
types listed in the node's own fullContentArtifactTypes (see
app/services/ai_generation.py). Three separate real occurrences of this
were found and fixed by hand in a single session (Solution Discovery's
recommendation getting truncated out of HLD's summary, then the same
class of bug in Story Crafting reading a summarized HLD) before this test
was added to cover every stage that consumes one of these long,
structured documents, not just the ones already hit in practice.
"""

from app.services.workflow_templates import load_workflow_template

# Every artifact type here is a long, structured, multi-section document
# where the single most important fact (a recommendation, an acceptance
# criterion, a specific task) can legitimately land anywhere in the
# document, including near the end — exactly the shape that a P2 summary
# has already been observed to truncate before reaching. A short, mostly
# single-fact document (e.g. intake_summary) is not included here.
_LONG_STRUCTURED_ARTIFACT_TYPES = {
    "problem_statement", "solution_options_doc", "hld_document", "lld_document", "story_backlog",
    "implementation_plan", "story_lld_document",
}


def _violations_for(template_file: str) -> list[str]:
    template = load_workflow_template(template_file)
    violations = []
    for node in template["nodes"]:
        full_content = set(node.get("fullContentArtifactTypes", []))
        for required in node["requiredInputs"]:
            if required in _LONG_STRUCTURED_ARTIFACT_TYPES and required not in full_content:
                violations.append(f"{template_file}:{node['id']} requires '{required}' but doesn't list it in fullContentArtifactTypes")
    return violations


def test_every_stage_escalates_its_long_structured_required_inputs_to_full_content():
    assert _violations_for("sdlc-workflow.json") == []


def test_scrum_story_lanes_workflow_also_escalates_long_structured_required_inputs():
    # The per-story delivery lane (STORY_LLD etc.) is no longer a
    # WorkflowNode-based template — see app/services/story_delivery.py —
    # so only the project-level Scrum template is checked here.
    assert _violations_for("scrum-story-lanes-workflow.json") == []
