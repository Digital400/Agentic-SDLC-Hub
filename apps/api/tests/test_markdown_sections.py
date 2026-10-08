"""app/services/markdown_sections.py — split/join round-tripping, and
specifically the synthetic "Content" section never becoming a literal
heading on save.

Regression coverage for a real bug found in production: a Story Crafting
backlog's intro paragraph (free text before the first real `## Story:`
heading) turned into a literal "## Content" heading the first time the
document was saved through the section editor, because join_sections
wrote every section back with a `## {title}` heading unconditionally —
including the synthesized placeholder title that never existed in the
source text.
"""

from app.services.markdown_sections import find_section, has_real_sections, join_sections, split_into_sections

BACKLOG = (
    "# Story Backlog — Pilot\n\nSome intro text explaining scope, no heading yet.\n\n"
    "## Story: S1 — First\n\nDetails for S1.\n\n"
    "## Story: S2 — Second\n\nDetails for S2.\n"
)


def test_leading_text_before_any_heading_is_marked_synthetic():
    sections = split_into_sections(BACKLOG)
    assert sections[0]["title"] == "Content"
    assert sections[0]["is_synthetic"] is True
    assert "Some intro text" in sections[0]["content"]
    assert sections[1]["title"] == "Story: S1 — First"
    assert sections[1]["is_synthetic"] is False
    assert sections[2]["title"] == "Story: S2 — Second"
    assert sections[2]["is_synthetic"] is False


def test_join_sections_never_invents_a_literal_content_heading():
    sections = split_into_sections(BACKLOG)
    rejoined = join_sections(sections)

    assert "## Content" not in rejoined
    assert "Some intro text explaining scope" in rejoined
    assert "## Story: S1 — First" in rejoined
    assert "## Story: S2 — Second" in rejoined


def test_round_trip_is_stable_across_multiple_saves():
    """The exact failure mode: split -> join -> split -> join must not
    accumulate a literal "## Content" heading on each pass."""
    once = join_sections(split_into_sections(BACKLOG))
    twice = join_sections(split_into_sections(once))
    assert once == twice
    assert "## Content" not in twice


def test_a_document_with_no_headings_at_all_still_round_trips_without_a_content_heading():
    plain = "Just plain text.\nNo headings anywhere.\n"
    sections = split_into_sections(plain)
    assert sections == [{"title": "Content", "content": "Just plain text.\nNo headings anywhere.", "is_synthetic": True}]
    assert "## Content" not in join_sections(sections)


def test_a_genuine_heading_literally_titled_content_is_preserved_as_real():
    """Distinguishes the synthesized placeholder from an agent that
    genuinely writes "## Content" as one of its own real headings —
    only the leading, pre-heading text is ever synthetic."""
    doc = "## Content\n\nThis heading was written by the agent itself.\n"
    sections = split_into_sections(doc)
    assert len(sections) == 1
    assert sections[0]["is_synthetic"] is False
    assert "## Content" in join_sections(sections)


def test_find_section_and_has_real_sections_unaffected_by_the_new_field():
    assert has_real_sections(BACKLOG) is True
    assert has_real_sections("no headings here") is False
    found = find_section(BACKLOG, "story: s1 — first")
    assert found is not None
    assert "Details for S1." in found["content"]
