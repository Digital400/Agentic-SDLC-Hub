"""Shared base model + versioning primitives for every contract in this
package — see app/agent_runtime/__init__.py's module docstring for the
hard rules this exists to enforce.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# The set of schema versions this codebase currently knows how to read.
# Additive only: a breaking contract change adds a new literal here (and a
# new value in app.agent_runtime.SCHEMA_VERSION-consuming code), it never
# replaces "1.0.0" with different semantics — see
# tests/test_agent_runtime_schema_versioning.py.
SchemaVersion = Literal["1.0.0"]

CURRENT_SCHEMA_VERSION: SchemaVersion = "1.0.0"


class AgentRuntimeModel(BaseModel):
    """Base class for every Work Packet / Result contract.

    `extra="forbid"` is what makes hard rule 2 (see package docstring)
    structural rather than a convention someone can quietly violate: any
    field not explicitly declared on a contract is a validation error, not
    a silently-accepted passthrough — so a vendor integration literally
    cannot bolt a mandatory-in-practice field onto a shared contract. Vendor
    metadata belongs in the `extensions` field every top-level contract
    exposes (see `VendorExtensible` below).
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class VendorExtensible(AgentRuntimeModel):
    """Adds the one, and only, escape hatch for vendor/runtime-specific
    metadata: a dict namespaced by a short vendor/runtime key (e.g.
    "anthropic", "langgraph", "internal-ci"), each value itself a plain
    dict of that vendor's own fields.

    This is deliberately untyped beyond "a dict of dicts" — the whole point
    is that this package's own contracts never need to change shape to
    accommodate a new runtime's quirks, and no single vendor's fields are
    ever mandatory for another vendor's integration to satisfy. Contracts
    that inherit this never gain a *required* vendor field; `extensions`
    itself always defaults to empty.
    """

    extensions: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Namespaced vendor/runtime-specific metadata, keyed by a short "
            "vendor identifier (e.g. 'anthropic', 'langgraph'). Never "
            "required, never interpreted by this codebase's own workflow "
            "engine — purely a passthrough for a concrete runtime "
            "integration. Must never contain secrets (tokens, keys, "
            "passwords) — see test_no_contract_field_is_secret_shaped."
        ),
    )
