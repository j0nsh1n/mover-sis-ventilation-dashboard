"""Typed exceptions for guardrail failures."""

from __future__ import annotations


class GuardrailError(Exception):
    """Base class for all guardrail / safety failures."""


class ConfigValidationError(GuardrailError):
    """Invalid thresholds.yaml or preset configuration."""


class DataValidationError(GuardrailError):
    """DataFrame schema, nullability, or invariant failure."""


class SafetyLimitError(GuardrailError):
    """Parameter or resource usage outside allowed bounds."""


class PipelineError(GuardrailError):
    """Pipeline stage failure (I/O, empty sample, write failure)."""


class PathSafetyError(GuardrailError):
    """Path does not exist, is not a directory, or escapes allowed roots."""
