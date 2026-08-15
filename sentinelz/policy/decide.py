"""Turning a belief into an action.

The POMDP policy is the decision maker. There is no threshold rule here, and
adding one would quietly replace the model with a constant. `decide_threshold`
exists solely as the ablation baseline and is selected by a flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from sentinelz.config import HAZARD_THRESHOLD
from sentinelz.pomdp.states import (
    ACTION_NAMES,
    ALLOW,
    MONITOR,
    REVOKE,
    SCOPE_DOWN,
    STATE_NAMES,
    STEP_UP,
    action_name,
)


@dataclass
class Decision:
    """Everything the UI, the log and the evaluation need about one call."""

    action: str
    action_index: int
    belief: list[float]
    argmax_state: str
    #: Expected cost of each of the five actions at this belief. The UI draws
    #: this as a bar chart so the audience sees why the winner won.
    expected_costs: dict[str, float]
    #: P(reach HARM within n) for n in 1, 3, 5 — the advance-warning claim.
    absorption: dict[str, float]
    #: Logged and displayed, but NOT the decision input.
    hazard: float
    observation: int
    signals: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    policy: str = "pomdp"
    timings_ms: dict[str, float] = field(default_factory=dict)
    explanation: dict[str, Any] = field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        return self.action_index in (STEP_UP, REVOKE)

    def to_record(self) -> dict[str, Any]:
        """The canonical, hashable form written to the evidence log.

        Deliberately excludes timings: wall-clock varies run to run, and the
        determinism requirement is that a fixed seed and trace reproduce a
        byte-identical log.
        """
        return {
            "action": self.action,
            "belief": [round(float(b), 9) for b in self.belief],
            "argmax_state": self.argmax_state,
            "expected_costs": {k: round(float(v), 6) for k, v in sorted(self.expected_costs.items())},
            "absorption": {k: round(float(v), 9) for k, v in sorted(self.absorption.items())},
            "hazard": round(float(self.hazard), 9),
            "observation": int(self.observation),
            "signals": {k: round(float(v), 9) for k, v in sorted(self.signals.items())},
            "policy": self.policy,
            "reason": self.reason,
        }


def decide(belief: np.ndarray, policy: Any, hazard: float, observation: int, signals: dict[str, float],
           absorption: dict[str, float], reason: str = "") -> Decision:
    """The POMDP decision. `policy` is a solved `GridPolicy`."""
    action_index = policy.act(belief)
    costs = policy.expected_costs(belief)
    return Decision(
        action=action_name(action_index),
        action_index=action_index,
        belief=[float(b) for b in belief],
        argmax_state=STATE_NAMES[int(np.argmax(belief))],
        expected_costs={ACTION_NAMES[i]: float(costs[i]) for i in range(len(ACTION_NAMES))},
        absorption=absorption,
        hazard=float(hazard),
        observation=int(observation),
        signals=dict(signals),
        reason=reason,
        policy="pomdp",
    )


def decide_threshold(p_harm: float, threshold: float = HAZARD_THRESHOLD) -> tuple[int, str]:
    """ABLATION BASELINE ONLY — a plain hazard threshold, no belief state.

    Selected with `--policy=threshold`. It exists so the T2 ablation can show
    what the belief layer adds; it is never the shipped decision rule.
    """
    if p_harm >= threshold + 0.30:
        return REVOKE, f"hazard {p_harm:.3f} >= {threshold + 0.30:.3f}"
    if p_harm >= threshold + 0.15:
        return STEP_UP, f"hazard {p_harm:.3f} >= {threshold + 0.15:.3f}"
    if p_harm >= threshold:
        return SCOPE_DOWN, f"hazard {p_harm:.3f} >= {threshold:.3f}"
    if p_harm >= threshold * 0.5:
        return MONITOR, f"hazard {p_harm:.3f} >= {threshold * 0.5:.3f}"
    return ALLOW, f"hazard {p_harm:.3f} below all thresholds"


def decide_bootstrap(signals: dict[str, float]) -> tuple[int, str]:
    """DATA COLLECTION ONLY — a rule over raw signals, no learned model.

    The transition matrix cannot be estimated from sessions in which the
    defense never acted: SCOPE_DOWN, STEP_UP and REVOKE rows would be pure
    smoothing. This rule exercises all five actions during collection so those
    rows are backed by observations. It is not a baseline and never appears in
    a results table.
    """
    taint = float(signals.get("taint", 0.0))
    privilege = float(signals.get("privilege_delta", 0.0))
    injection = float(signals.get("injection_likelihood", 0.0))
    alignment = float(signals.get("task_alignment", 0.0))

    if taint >= 0.5 and (privilege >= 0.33 or alignment >= 0.7):
        return REVOKE, "tainted call reaching beyond the task"
    if taint >= 0.5 and injection >= 0.5:
        return STEP_UP, "tainted call following attacker-controlled content"
    if taint >= 0.5:
        return SCOPE_DOWN, "tainted call within task scope"
    if injection >= 0.5 or privilege >= 0.33:
        return MONITOR, "suspicious content or privilege reach"
    return ALLOW, "nothing notable"
