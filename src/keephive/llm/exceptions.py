"""Shared LLM error types."""

from __future__ import annotations


class ClaudePipeError(Exception):
    """Generic LLM execution error (backward-compatible name)."""

    pass


class BackendNotAvailable(ClaudePipeError):
    """Raised when the requested backend cannot be used."""

    pass


class CapabilityError(ClaudePipeError):
    """Raised when the selected backend lacks required capabilities."""

    pass
