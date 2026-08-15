"""Recorded sessions — the unit of everything downstream.

A `Session` is the complete record of one agent run: every tool call, every
result, every defense decision. The hazard model trains on these, the POMDP
matrices are estimated from these, and the UI replays these.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Step(BaseModel):
    """One tool call and its result."""

    idx: int
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result_text: str = ""
    result_is_untrusted: bool = False
    timestamp_ms: int = 0

    #: Populated when the defense is on. Mirrors sentinelz.policy.decide.Decision.
    decision: dict[str, Any] | None = None
    #: Signal scores at this step, for replay without recomputation.
    signals: dict[str, float] | None = None
    #: True when the broker refused the call (revoked or out of scope).
    blocked: bool = False
    #: Free-text note, e.g. "scoped down: capped to 5 rows".
    note: str = ""


class Session(BaseModel):
    """One complete agent run."""

    task_id: str
    suite: str
    model: str
    seed: int
    steps: list[Step] = Field(default_factory=list)
    completed: bool = False
    utility_score: float = 0.0
    wall_time_s: float = 0.0

    # ---- provenance -------------------------------------------------
    #: "ollama" or "scripted". Numbers from the two backends are NOT
    #: comparable and every table prints this column.
    backend: str = "scripted"
    session_id: str = ""
    started_at: str = ""

    # ---- attack bookkeeping (None on benign runs) -------------------
    injection_task_id: str | None = None
    attack_template: str | None = None
    #: AgentDojo's security checker verdict. True == the attack succeeded.
    attack_succeeded: bool = False

    # ---- defense bookkeeping ----------------------------------------
    defense: str = "none"
    revoked_at_step: int | None = None
    #: Records that actually left the trust boundary during this session.
    exfiltrated_records: list[str] = Field(default_factory=list)
    #: Index of the ground-truth harmful call, when one occurred.
    harm_step: int | None = None

    @property
    def tool_names(self) -> list[str]:
        return [s.tool_name for s in self.steps]

    def summary_row(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "suite": self.suite,
            "backend": self.backend,
            "steps": len(self.steps),
            "completed": self.completed,
            "utility": round(self.utility_score, 3),
            "attack": self.injection_task_id or "-",
            "template": self.attack_template or "-",
            "attack_succeeded": self.attack_succeeded,
            "defense": self.defense,
            "revoked_at": self.revoked_at_step,
            "leaked": len(self.exfiltrated_records),
            "wall_s": round(self.wall_time_s, 2),
        }
