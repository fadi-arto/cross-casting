from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from domain.models import InterpretedFinding, ValidationResult


class ErrorInterpreter(Protocol):
    """LLM adapter: convert validation errors into human-readable audit findings."""

    def interpret(self, results: Sequence[ValidationResult]) -> tuple[InterpretedFinding, ...]: ...
