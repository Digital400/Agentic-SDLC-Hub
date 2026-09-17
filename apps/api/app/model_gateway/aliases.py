"""ModelAlias — the vendor-neutral names business agents and ModelPolicy
ask for, instead of a literal provider model string. See
app/model_gateway/__init__.py's module docstring.

A caller (or a ModelPolicy) names an alias; a concrete ModelGateway
adapter is the only thing that ever resolves an alias to an actual
provider + model string (see each adapter's own ALIAS_MODEL_MAP) — this
is what keeps "which model" a policy/deployment decision instead of
something scattered across call sites.
"""

import enum


class ModelAlias(str, enum.Enum):
    """Six aliases, spanning general-purpose and coding-specialized tiers
    plus one embedding alias (see app/services/embeddings.py's own
    docstring for why this codebase doesn't currently call a real hosted
    embedding model — SDLC_EMBEDDING is the seam a future adapter would
    fill in)."""

    SDLC_SMALL = "sdlc-small"
    SDLC_STANDARD = "sdlc-standard"
    SDLC_PREMIUM = "sdlc-premium"
    SDLC_CODING_SMALL = "sdlc-coding-small"
    SDLC_CODING_STANDARD = "sdlc-coding-standard"
    SDLC_EMBEDDING = "sdlc-embedding"
