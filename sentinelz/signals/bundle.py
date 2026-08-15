"""All five signals, evaluated together, once per pending tool call.

Every model is constructed here, at startup, and never inside a request. The
whole bundle has an 80ms budget; the injection classifier is the only part
that can threaten it, which is why it is loaded once and benchmarked.
"""

from __future__ import annotations

import time
from typing import Any

from sentinelz.context import DecisionContext
from sentinelz.signals.alignment import AlignmentSignal
from sentinelz.signals.base import SIGNAL_NAMES, Signal
from sentinelz.signals.injection import InjectionSignal
from sentinelz.signals.novelty import NoveltySignal
from sentinelz.signals.privilege import PrivilegeSignal
from sentinelz.signals.taint import TaintSignal, TaintTracker


class SignalBundle:
    """The five detectors, in fixed order.

    Args:
        disabled: signal names to force to 0.0. This is how the T2 ablation
            runs — a flag, not a forked code path.
    """

    def __init__(
        self,
        tracker: TaintTracker | None = None,
        prefer_neural: bool = True,
        disabled: frozenset[str] = frozenset(),
    ) -> None:
        self.tracker = tracker or TaintTracker()
        self.disabled = frozenset(disabled)
        unknown = self.disabled - set(SIGNAL_NAMES)
        if unknown:
            raise ValueError(f"unknown signal(s) to disable: {sorted(unknown)}")

        self.injection = InjectionSignal(prefer_neural=prefer_neural)
        self.alignment = AlignmentSignal(prefer_neural=prefer_neural)
        self.privilege = PrivilegeSignal()
        self.taint = TaintSignal(tracker=self.tracker)
        self.novelty = NoveltySignal()

        self._signals: tuple[Signal, ...] = (
            self.injection,
            self.alignment,
            self.privilege,
            self.taint,
            self.novelty,
        )
        assert tuple(s.name for s in self._signals) == SIGNAL_NAMES

    # ------------------------------------------------------------ evaluate
    def evaluate(
        self,
        ctx: DecisionContext,
        precomputed: dict[str, tuple[float, dict[str, Any]]] | None = None,
    ) -> tuple[dict[str, float], dict[str, Any]]:
        """Returns (scores by name, merged explanation).

        `precomputed` lets the gateway reuse a score it already needed — the
        injection score has to be known before taint can be updated, and
        running that classifier twice would double the budget.

        The explanation carries per-signal timings so the M6 latency table is
        decomposed by signal without a second instrumented code path.
        """
        scores: dict[str, float] = {}
        explanation: dict[str, Any] = {"per_signal": {}, "timings_ms": {}}
        precomputed = precomputed or {}

        for signal in self._signals:
            if signal.name in precomputed:
                value, detail = precomputed[signal.name]
                scores[signal.name] = float(value)
                explanation["per_signal"][signal.name] = detail
                explanation["timings_ms"][signal.name] = float(detail.get("_elapsed_ms", 0.0))
                continue
            if signal.name in self.disabled:
                scores[signal.name] = 0.0
                explanation["per_signal"][signal.name] = {"disabled": True}
                explanation["timings_ms"][signal.name] = 0.0
                continue
            started = time.perf_counter()
            try:
                value, detail = signal.score(ctx)
            except Exception as exc:
                # A broken signal must not look like a clean one. Score it at
                # the top; the gateway turns that into a REVOKE.
                value, detail = 1.0, {"error": str(exc)[:200]}
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            scores[signal.name] = float(value)
            explanation["per_signal"][signal.name] = detail
            explanation["timings_ms"][signal.name] = round(elapsed_ms, 3)

        explanation["total_ms"] = round(sum(explanation["timings_ms"].values()), 3)
        explanation["implementations"] = {
            "injection_likelihood": self.injection.implementation,
            "task_alignment": self.alignment.implementation,
            "sequence_novelty": "table" if self.novelty.loaded else "no-table",
        }
        return scores, explanation

    # ------------------------------------------------------ taint plumbing
    def absorb_result(self, tool_name: str, result_text: str, injection_score: float, call_was_tainted: bool) -> set[str]:
        """Feed one completed tool result back into the taint tracker."""
        return self.tracker.observe_result(tool_name, result_text, injection_score, call_was_tainted)

    def reset(self) -> None:
        self.tracker.reset()

    def as_vector(self, scores: dict[str, float]) -> list[float]:
        """Scores in the fixed signal order. The feature builder depends on it."""
        return [float(scores[name]) for name in SIGNAL_NAMES]
