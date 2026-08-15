"""Turning a recorded session into a hash-chained decision log.

Only decision-relevant fields go in. Wall-clock timings are deliberately left
out: requirement N5 says a fixed seed and a fixed trace must reproduce a
byte-identical log, and latency does not reproduce.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sentinelz.evidence.chain import DecisionChain
from sentinelz.evidence.export import write_chain


def session_records(session: Any) -> list[dict[str, Any]]:
    """The decision records for one session, in order."""
    records: list[dict[str, Any]] = []
    for step in session.steps:
        decision = dict(step.decision or {})
        decision.pop("timings_ms", None)
        records.append(
            {
                "session_id": session.session_id,
                "task_id": session.task_id,
                "suite": session.suite,
                "seed": session.seed,
                "backend": session.backend,
                "step": step.idx,
                "tool": step.tool_name,
                "blocked": step.blocked,
                "decision": decision,
            }
        )
    return records


def build_chain(session: Any) -> DecisionChain:
    chain = DecisionChain()
    for record in session_records(session):
        chain.append(record)
    return chain


def log_session(session: Any) -> Path:
    """Write `runs/<session_id>/decisions.jsonl` and return its path."""
    return write_chain(session.session_id, build_chain(session).records)
