"""Recommends an implementation order for a project's stories, from their
own Dependencies field — nobody states this explicitly today; a team just
picks whatever looks reasonable from a flat list.

Dependencies is free text written by the Story Crafting agent (or a human),
naming other stories "by exact story title" per the prompt's own rule —
never a real foreign key (see Story's own docstring on why titles are the
identity this codebase already matches stories by elsewhere: Jira push,
ImplementationTask.linked_story). It is ONE field that may name several
other stories, and a title can itself contain commas (a real one seen in
practice: "Scaffold New Subscription & Entitlement API, Admin Portal, and
Isolated Database Projects") — so this does NOT simply split on commas.
Instead it matches known titles as substrings, longest title first (so a
short title can't falsely "steal" a match from inside a longer one that
contains it), and reports whatever text is left over as unresolved rather
than silently dropping it — a typo'd or renamed dependency should be
visible, not invisible.

The result is a sequence of "waves": wave 1 is every story with no unmet
dependency, wave 2 is everything that becomes unblocked once wave 1 is
done, and so on. Stories in the same wave have no ordering constraint
between them and can be worked in parallel. A story already DONE is
treated as satisfied and excluded from the waves entirely — it's not
something to "do next".
"""

import re
from dataclasses import dataclass, field

from app.models import Story
from app.models.enums import StoryStatus

_TRIVIAL_DEPENDENCY_TEXT = re.compile(r"^(none\.?|n/a|-|—|tbd)$", re.IGNORECASE)
_LEFTOVER_NOISE = re.compile(r"[,.;:&\-\s]+|(?:\band\b)", re.IGNORECASE)


def _resolve_dependencies(dependencies_text: str, titles_by_length: list[tuple[str, str]], self_title: str) -> tuple[set[str], str | None]:
    """`titles_by_length` is [(title, story_id), ...] sorted longest-title
    first. Returns (matched story ids, leftover unresolved text or None)."""
    text = (dependencies_text or "").strip()
    if not text or _TRIVIAL_DEPENDENCY_TEXT.match(text):
        return set(), None

    matched: set[str] = set()
    remaining = text
    for title, story_id in titles_by_length:
        if title.strip().lower() == self_title.strip().lower():
            continue  # a story can't depend on itself
        lower_remaining = remaining.lower()
        idx = lower_remaining.find(title.strip().lower())
        if idx != -1:
            matched.add(story_id)
            remaining = remaining[:idx] + remaining[idx + len(title) :]

    leftover = _LEFTOVER_NOISE.sub(" ", remaining).strip()
    return matched, (leftover or None)


@dataclass
class StoryOrderResult:
    # One list of stories per wave — wave 1 first, no ordering within a wave.
    waves: list[list[Story]] = field(default_factory=list)
    # story_id -> the unresolved leftover text from its Dependencies field
    # (a likely typo or renamed story) — present only when something didn't
    # match a known title.
    unresolved_dependencies: dict[str, str] = field(default_factory=dict)
    # Stories that never became schedulable — a dependency cycle among them.
    circular: list[Story] = field(default_factory=list)


def compute_story_order(stories: list[Story]) -> StoryOrderResult:
    pending = [s for s in stories if s.status != StoryStatus.DONE]
    pending_ids = {str(s.id) for s in pending}
    # Matched against ALL stories (including DONE ones), so a dependency on
    # an already-finished story is recognized and consumed from the text —
    # otherwise it would wrongly show up as "unresolved" — but only a match
    # against a still-pending story becomes an actual ordering constraint;
    # a DONE dependency is already satisfied, not something to wait on.
    titles_by_length = sorted(((s.title, str(s.id)) for s in stories), key=lambda pair: len(pair[0]), reverse=True)

    deps_by_id: dict[str, set[str]] = {}
    unresolved: dict[str, str] = {}
    for s in pending:
        matched, leftover = _resolve_dependencies(s.dependencies, titles_by_length, s.title)
        deps_by_id[str(s.id)] = matched & pending_ids
        if leftover:
            unresolved[str(s.id)] = leftover

    by_id = {str(s.id): s for s in pending}
    scheduled: set[str] = set()
    waves: list[list[Story]] = []
    remaining_ids = set(by_id)

    while remaining_ids:
        ready = [
            sid for sid in remaining_ids
            if deps_by_id[sid] <= scheduled  # every dependency already scheduled (or was never a pending story, i.e. already DONE/unresolved-but-matched)
        ]
        if not ready:
            break  # whatever's left is a cycle
        wave = [by_id[sid] for sid in ready]
        wave.sort(key=lambda s: (s.title.lower()))
        waves.append(wave)
        scheduled.update(ready)
        remaining_ids -= set(ready)

    circular = [by_id[sid] for sid in sorted(remaining_ids, key=lambda sid: by_id[sid].title.lower())]
    return StoryOrderResult(waves=waves, unresolved_dependencies=unresolved, circular=circular)
