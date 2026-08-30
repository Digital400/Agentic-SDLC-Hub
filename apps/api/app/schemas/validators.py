"""Shared field-level validators for request schemas.

`Field(..., min_length=1)` alone accepts a whitespace-only string (`"   "`
has length 3) — a real gap found during the MVP hardening pass: a project,
artifact, prompt, or integration could be created with a name that looks
blank everywhere it's displayed. `NonBlankStr` closes that by stripping and
then requiring at least one real character; apply it to any required
"name"-shaped field a human types into a form.
"""

from typing import Annotated

from pydantic import AfterValidator


def _non_blank(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


NonBlankStr = Annotated[str, AfterValidator(_non_blank)]
