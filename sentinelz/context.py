"""The input to every decision.

`DecisionContext` is deliberately plain: the defense must not depend on the
arena's types, so anything that can build one of these can be defended.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ObservedStep:
    """A completed step, as the defense saw it."""

    tool_name: str
    tool_args: dict[str, Any]
    result_text: str
    result_is_untrusted: bool = False


@dataclass(slots=True)
class PendingCall:
    """The tool call awaiting a decision."""

    tool_name: str
    tool_args: dict[str, Any]

    def as_text(self) -> str:
        """Flat text rendering used by the alignment signal."""
        parts = [self.tool_name.replace("_", " ")]
        for key, value in sorted(self.tool_args.items()):
            parts.append(f"{key}: {_stringify(value)}")
        return " | ".join(parts)

    def arg_values(self) -> list[str]:
        out: list[str] = []
        for value in self.tool_args.values():
            out.append(_stringify(value))
        return out


@dataclass(slots=True)
class DecisionContext:
    """Everything the five signals are allowed to look at."""

    user_task_text: str
    pending: PendingCall
    history: list[ObservedStep] = field(default_factory=list)
    #: Identifiers already known to be attacker-influenced. Owned by the
    #: session's TaintTracker; the signals read it, taint.py writes it.
    taint_set: set[str] = field(default_factory=set)
    #: Tools the user task is expected to need. Empty means "unknown", which
    #: the alignment signal treats as no penalty rather than as everything
    #: being out of scope.
    expected_tools: frozenset[str] = frozenset()
    #: Max sensitivity tier the user task legitimately requires.
    task_max_tier: int = 1
    suite: str = ""
    step_index: int = 0

    @property
    def last_result_text(self) -> str:
        """Text of the most recent tool result. The injection signal scores
        this — the result, never the call."""
        return self.history[-1].result_text if self.history else ""


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple):
        return " ".join(_stringify(v) for v in value)
    if isinstance(value, dict):
        return " ".join(f"{k} {_stringify(v)}" for k, v in sorted(value.items()))
    return str(value)
