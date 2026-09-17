"""Enums for the Phase 02 Runtime Capability Registry — see
app/coding_runtime/__init__.py.
"""

import enum


class RuntimeKind(str, enum.Enum):
    """What KIND of work a runtime is built to perform — the coarsest
    axis runtime selection filters on before capabilities are even
    consulted (a DOCUMENT-kind runtime is never a candidate for
    IMPLEMENT_STORY, regardless of what capabilities it happens to
    declare)."""

    DOCUMENT = "DOCUMENT"
    CODING = "CODING"
    INTEGRATION = "INTEGRATION"


class ExecutionLocation(str, enum.Enum):
    """WHERE a runtime actually executes — a security/data-classification
    axis, independent of RuntimeKind. Phase 07's runtime security baseline
    (see app/runtime_security/) is the authority on what's actually
    permitted for a given data classification; this enum only names the
    location a RuntimeDefinition declares itself as running in."""

    COMPANY_SANDBOX = "COMPANY_SANDBOX"
    DEVELOPER_LOCAL = "DEVELOPER_LOCAL"
    EXTERNAL_MANAGED = "EXTERNAL_MANAGED"
