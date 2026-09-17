"""SecretProvider — the abstraction app.runtime_security.credential_broker
resolves configuration-level secrets (a GitHub App private key, etc.)
through, instead of reading Settings/env directly.

Same "disclosed bridge, not the real end state" framing as
app/core/security.py's own docstring: EnvSecretProvider (the only
implementation today) reads from this process's own Settings/environment
— appropriate for this codebase's current MVP posture, and exactly what
docs/architecture.md's MCP integrations section already names as the
gap ("credentials in a vault... never persisted in this table or
logged", not yet built). Introducing this interface NOW — even with only
one, env-backed implementation — is what makes swapping in a real vault
client later a one-class change instead of a grep-and-replace across
every caller.
"""

from __future__ import annotations

import abc
import os


class SecretNotFoundError(Exception):
    pass


class SecretProvider(abc.ABC):
    @abc.abstractmethod
    def get_secret(self, key: str) -> str:
        """Raises SecretNotFoundError if `key` has no value — never
        returns None or an empty string as a stand-in for "not
        configured"."""


class EnvSecretProvider(SecretProvider):
    """Reads from `os.environ` (which `Settings`' own `env_file = ".env"`
    already populates at process start — see app/core/config.py) — no
    separate config surface, just a typed lookup with a clear failure
    mode instead of `os.environ.get(key)` silently returning None at each
    call site."""

    def get_secret(self, key: str) -> str:
        value = os.environ.get(key)
        if not value:
            raise SecretNotFoundError(f"Secret '{key}' is not set in the environment.")
        return value
