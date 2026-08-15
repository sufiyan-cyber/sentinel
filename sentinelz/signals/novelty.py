"""Signal 5 — sequence_novelty.

`1 - P(this tool-name trigram | benign runs)`, Laplace smoothed. Has this
agent, on benign work, ever done this three-step sequence before?

The trigram table is built from benign sessions by
`python -m sentinelz.signals.novelty --build` and stored in
`models/trigrams_v1.json`. It is loaded once at startup. A missing table
means "everything is novel", which is the safe direction, and it is reported
in the explanation rather than silently returning zero.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from sentinelz.config import MODELS_DIR
from sentinelz.context import DecisionContext
from sentinelz.evidence.canonical import dumps_str, loads
from sentinelz.signals.base import clip01

TRIGRAM_PATH = MODELS_DIR / "trigrams_v1.json"
START = "<start>"
LAPLACE_ALPHA = 1.0


class NoveltySignal:
    """sequence_novelty, in [0,1]."""

    name = "sequence_novelty"

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or TRIGRAM_PATH
        self.counts: dict[str, int] = {}
        self.total = 0
        self.vocabulary = 1
        self.loaded = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.counts = {str(k): int(v) for k, v in data.get("counts", {}).items()}
        self.total = int(data.get("total", sum(self.counts.values())))
        self.vocabulary = max(1, int(data.get("vocabulary", len(self.counts) or 1)))
        self.loaded = True

    def score(self, ctx: DecisionContext) -> tuple[float, dict[str, Any]]:
        key = trigram_key([s.tool_name for s in ctx.history], ctx.pending.tool_name)
        if not self.loaded or self.total <= 0:
            # No benign baseline yet, so nothing has been seen before. Fail
            # towards "novel" rather than towards "familiar".
            return 1.0, {"trigram": key, "count": 0, "table_loaded": False, "table_total": 0, "probability": 0.0}

        count = self.counts.get(key, 0)
        denominator = self.total + LAPLACE_ALPHA * self.vocabulary
        probability = (count + LAPLACE_ALPHA) / denominator if denominator > 0 else 0.0

        # Normalise against the most frequent trigram so a common sequence
        # lands near 0 rather than near 1 just because the table is large.
        max_count = max(self.counts.values(), default=0)
        max_probability = (max_count + LAPLACE_ALPHA) / denominator if denominator > 0 else 1.0
        relative = probability / max_probability if max_probability > 0 else 0.0
        value = clip01(1.0 - relative)

        return value, {
            "trigram": key,
            "count": count,
            "table_loaded": self.loaded,
            "table_total": self.total,
            "probability": round(probability, 6),
        }


def trigram_key(history_tools: list[str], pending_tool: str) -> str:
    sequence = [*history_tools, pending_tool]
    window = sequence[-3:]
    while len(window) < 3:
        window.insert(0, START)
    return "|".join(window)


# ------------------------------------------------------------------ build


def build_table(sessions: list[Any], path: Path | None = None) -> dict[str, Any]:
    """Count tool-name trigrams over benign sessions and write the table."""
    path = path or TRIGRAM_PATH
    counts: dict[str, int] = {}
    used = 0
    for session in sessions:
        if getattr(session, "injection_task_id", None) is not None:
            continue  # benign runs only, by definition of the signal
        used += 1
        tools = [step.tool_name for step in session.steps]
        for i in range(len(tools)):
            key = trigram_key(tools[:i], tools[i])
            counts[key] = counts.get(key, 0) + 1

    payload = {
        "counts": counts,
        "total": sum(counts.values()),
        "vocabulary": max(1, len(counts)),
        "n_benign_sessions": used,
        "version": 1,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_str(payload), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    parser = argparse.ArgumentParser(description="Build the benign tool-trigram table.")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--runs", type=Path, default=None)
    args = parser.parse_args(argv)
    if not args.build:
        parser.print_help()
        return 0

    from arena.agent.runner import load_labelled_sessions as load_sessions

    sessions = load_sessions(args.runs)
    payload = build_table(sessions)
    print(f"wrote {TRIGRAM_PATH}: {len(payload['counts'])} trigrams "
          f"from {payload['n_benign_sessions']} benign sessions")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
