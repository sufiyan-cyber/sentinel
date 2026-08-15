"""The signal interface.

Five signals, fixed names, fixed order. Each returns a score in [0,1] and an
explanation dict the UI renders. No signal may call an LLM, touch the network,
or load a model outside `__init__` — the whole bundle has 80ms.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from sentinelz.context import DecisionContext

#: Fixed forever. Renaming one of these invalidates every stored model.
SIGNAL_NAMES: tuple[str, ...] = (
    "injection_likelihood",
    "task_alignment",
    "privilege_delta",
    "taint",
    "sequence_novelty",
)


@runtime_checkable
class Signal(Protocol):
    name: str

    def score(self, ctx: DecisionContext) -> tuple[float, dict[str, Any]]:
        """Returns (score in [0,1], explanation dict for the UI)."""
        ...


def clip01(value: float) -> float:
    if value != value:  # NaN
        return 0.0
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else float(value))
