"""The five states and five actions. Indices are fixed forever.

Renaming or reordering either list invalidates every saved matrix, policy and
decision log. AGENTS.md forbids it; `tests/test_guardrails.py` checks it.
"""

from __future__ import annotations

BENIGN = 0
RECON = 1
ESCALATION = 2
HARM = 3
CONTAINED = 4

STATE_NAMES: tuple[str, ...] = ("BENIGN", "RECON", "ESCALATION", "HARM", "CONTAINED")
N_STATES = 5

#: HARM and CONTAINED absorb: once entered, the session cannot leave.
ABSORBING_STATES: tuple[int, ...] = (HARM, CONTAINED)

ALLOW = 0
MONITOR = 1
SCOPE_DOWN = 2
STEP_UP = 3
REVOKE = 4

ACTION_NAMES: tuple[str, ...] = ("ALLOW", "MONITOR", "SCOPE_DOWN", "STEP_UP", "REVOKE")
N_ACTIONS = 5

#: Order of increasing restriction. Used by the policy monotonicity test:
#: as belief mass moves towards HARM the chosen action must never get looser.
ACTION_STRICTNESS: dict[int, int] = {ALLOW: 0, MONITOR: 1, SCOPE_DOWN: 2, STEP_UP: 3, REVOKE: 4}

#: |O| = 10: five hazard quintiles crossed with the binary taint flag.
N_OBSERVATIONS = 10


def state_name(index: int) -> str:
    return STATE_NAMES[index]


def action_name(index: int) -> str:
    return ACTION_NAMES[index]


def action_index(name: str) -> int:
    return ACTION_NAMES.index(name)
