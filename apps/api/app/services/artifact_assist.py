"""Read-only helpers behind the Documents page's "Summarize changes" and
"Ask questions" agent actions (neither ever modifies the document), plus the
shared lookup of a stage's original freeform input that "Regenerate section"
uses.

- summarize_changes: a deterministic, section-by-section comparison of two
  versions of an artifact — no model call, so it is instant, free and can
  never invent a change that didn't happen.
- answer_question: answers a question about the document from its own text
  (and the user's original request), citing the sections used. Uses the
  active provider; the mock provider falls back to returning the most
  relevant passages, clearly labelled as such.
"""

import difflib
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import AgentPromptRole, AgentRun, Artifact, ArtifactVersion, WorkflowNode
from app.services.ai_generation import AIGenerationError, generate_raw_text, get_active_provider
from app.services.markdown_sections import split_into_sections

_MAX_LINES_SHOWN = 8
# Characters of document text sent to the model for one question (~6k tokens).
_MAX_DOCUMENT_CHARS = 24_000
_MAX_REQUEST_CHARS = 8_000


class ArtifactAssistError(Exception):
    """A caller error (e.g. an unknown version number)."""


# --- Shared: the stage's original input ---------------------------------------------------


def latest_freeform_input(db: Session, node: WorkflowNode) -> dict[str, str]:
    """The text inputs (e.g. the stakeholder request, plus any clarification
    answers) of this stage's most recent DRAFT run — what the user originally
    provided. Empty when the stage was never run through the agent."""
    run = (
        db.query(AgentRun)
        .filter(AgentRun.workflow_node_id == node.id, AgentRun.action == AgentPromptRole.DRAFT)
        .order_by(AgentRun.created_at.desc())
        .all()
    )
    for candidate in run:
        strings = {k: v for k, v in (candidate.input_context or {}).items() if isinstance(v, str) and v.strip()}
        if strings:
            return strings
    return {}


# --- Summarize changes --------------------------------------------------------------------


@dataclass
class SectionChange:
    title: str
    kind: str  # "added" | "removed" | "changed"
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    added_line_count: int = 0
    removed_line_count: int = 0


@dataclass
class ChangeSummary:
    from_version: int | None
    to_version: int
    headline: str
    is_first_version: bool
    changes: list[SectionChange]
    change_note: str | None  # the version's own change_summary, if any


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]


def _compare_sections(old_md: str, new_md: str) -> list[SectionChange]:
    old = {str(s["title"]): str(s["content"]) for s in split_into_sections(old_md)}
    new = {str(s["title"]): str(s["content"]) for s in split_into_sections(new_md)}
    changes: list[SectionChange] = []
    for title, content in new.items():
        if title not in old:
            lines = _lines(content)
            changes.append(SectionChange(title, "added", added_lines=lines[:_MAX_LINES_SHOWN], added_line_count=len(lines)))
        elif old[title].strip() != content.strip():
            old_lines, new_lines = _lines(old[title]), _lines(content)
            matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
            added: list[str] = []
            removed: list[str] = []
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag in ("replace", "delete"):
                    removed += old_lines[i1:i2]
                if tag in ("replace", "insert"):
                    added += new_lines[j1:j2]
            changes.append(SectionChange(
                title, "changed", added_lines=added[:_MAX_LINES_SHOWN], removed_lines=removed[:_MAX_LINES_SHOWN],
                added_line_count=len(added), removed_line_count=len(removed),
            ))
    for title, content in old.items():
        if title not in new:
            lines = _lines(content)
            changes.append(SectionChange(title, "removed", removed_lines=lines[:_MAX_LINES_SHOWN], removed_line_count=len(lines)))
    return changes


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def summarize_changes(artifact: Artifact, *, from_version_number: int | None = None, to_version_number: int | None = None) -> ChangeSummary:
    versions = sorted(artifact.versions, key=lambda v: v.version_number)
    if not versions:
        raise ArtifactAssistError("This document has no versions yet.")
    by_number = {v.version_number: v for v in versions}

    to_version: ArtifactVersion = (
        artifact.current_version if to_version_number is None and artifact.current_version is not None else by_number.get(to_version_number or versions[-1].version_number)
    )
    if to_version is None:
        raise ArtifactAssistError(f"Version {to_version_number} was not found.")

    if from_version_number is None:
        earlier = [v for v in versions if v.version_number < to_version.version_number]
        from_version = earlier[-1] if earlier else None
    else:
        from_version = by_number.get(from_version_number)
        if from_version is None:
            raise ArtifactAssistError(f"Version {from_version_number} was not found.")

    if from_version is None:
        sections = split_into_sections(to_version.content_markdown)
        return ChangeSummary(
            from_version=None, to_version=to_version.version_number, is_first_version=True, change_note=to_version.change_summary,
            headline=f"This is the first version — nothing earlier to compare with ({_plural(len(sections), 'section')}).",
            changes=[],
        )

    changes = _compare_sections(from_version.content_markdown, to_version.content_markdown)
    if not changes:
        headline = f"No differences between v{from_version.version_number} and v{to_version.version_number}."
    else:
        parts = []
        for kind in ("changed", "added", "removed"):
            n = sum(1 for c in changes if c.kind == kind)
            if n:
                parts.append(f"{_plural(n, 'section')} {kind}")
        headline = f"v{from_version.version_number} → v{to_version.version_number}: " + ", ".join(parts) + "."
    return ChangeSummary(
        from_version=from_version.version_number, to_version=to_version.version_number, headline=headline,
        is_first_version=False, changes=changes, change_note=to_version.change_summary,
    )


# --- Ask questions ------------------------------------------------------------------------


@dataclass
class QuestionAnswer:
    answer: str
    sources: list[str]
    used_mock: bool
    truncated: bool


_ASK_SYSTEM_PROMPT = (
    "You answer questions about ONE document from a software-delivery workflow. Use ONLY the document text and the "
    "user's original request that are provided. If the answer is not there, say so plainly and suggest what "
    "information would be needed — never guess or invent facts. Be concise and specific. "
    "When you use a section, name it in square brackets, e.g. [Constraints]. Do not rewrite or propose edits to the "
    "document unless asked."
)


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower())}


def _mock_answer(document: str, question: str) -> tuple[str, list[str]]:
    """Offline fallback: rank sections by word overlap with the question and
    return the best passages verbatim."""
    q = _tokens(question)
    scored = []
    for section in split_into_sections(document):
        overlap = len(q & _tokens(f"{section['title']} {section['content']}"))
        if overlap:
            scored.append((overlap, section))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if not scored:
        return ("I couldn't find anything in this document that relates to that question.", [])
    top = [s for _, s in scored[:2]]
    body = "\n\n".join(f"**{s['title']}**\n{str(s['content']).strip()[:800]}" for s in top)
    return (f"(No AI provider is connected, so here are the most relevant passages.)\n\n{body}", [str(s["title"]) for s in top])


def answer_question(db: Session, *, artifact: Artifact, question: str) -> QuestionAnswer:
    if artifact.current_version is None:
        raise ArtifactAssistError("This document has no content yet.")
    document = artifact.current_version.content_markdown
    truncated = len(document) > _MAX_DOCUMENT_CHARS
    document_for_model = document[:_MAX_DOCUMENT_CHARS]

    if get_active_provider() == "mock":
        answer, sources = _mock_answer(document, question)
        return QuestionAnswer(answer=answer, sources=sources, used_mock=True, truncated=False)

    request_text = "\n\n".join(f"{k.replace('_', ' ')}:\n{v}" for k, v in latest_freeform_input(db, artifact.workflow_node).items())
    user_content = (
        f"DOCUMENT ({artifact.title}):\n{document_for_model}\n\n"
        + (f"USER'S ORIGINAL REQUEST AND ANSWERS:\n{request_text[:_MAX_REQUEST_CHARS]}\n\n" if request_text else "")
        + f"QUESTION: {question}"
    )
    try:
        answer = generate_raw_text(system_prompt=_ASK_SYSTEM_PROMPT, user_content=user_content, output_token_budget=900).strip()
    except AIGenerationError as exc:
        raise ArtifactAssistError(f"The AI could not answer right now: {exc}") from exc
    titles = [str(s["title"]) for s in split_into_sections(document)]
    sources = [t for t in titles if f"[{t}]" in answer]
    return QuestionAnswer(answer=answer, sources=sources, used_mock=False, truncated=truncated)

