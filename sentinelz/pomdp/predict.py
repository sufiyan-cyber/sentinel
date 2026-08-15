"""The absorbing-DTMC view of the same transition matrix.

Condition T on the passive MONITOR action and the POMDP's transition matrix
becomes an absorbing Markov chain over the same five states. Reorder it to

    [ Q  R ]
    [ 0  I ]

and the fundamental matrix N = (I - Q)^-1 gives expected steps to absorption,
while Q^n gives the n-step absorption probability into HARM.

This is a *view* of the POMDP's own matrix, not a second model. That is what
makes the survey paper's DTMC and this project's POMDP the same object rather
than two contradictory descriptions of one system.
"""

from __future__ import annotations

import numpy as np

from sentinelz.pomdp.states import ABSORBING_STATES, CONTAINED, HARM, MONITOR, N_STATES

TRANSIENT_STATES: tuple[int, ...] = tuple(s for s in range(N_STATES) if s not in ABSORBING_STATES)


def marginal_chain(T: np.ndarray, action: int = MONITOR) -> np.ndarray:
    """The policy-conditioned marginal: a 5x5 row-stochastic matrix."""
    return np.ascontiguousarray(T[:, action, :], dtype=np.float64)


def canonical_form(T: np.ndarray, action: int = MONITOR) -> tuple[np.ndarray, np.ndarray]:
    """`(Q, R)` — transient-to-transient and transient-to-absorbing blocks."""
    P = marginal_chain(T, action)
    transient = list(TRANSIENT_STATES)
    absorbing = list(ABSORBING_STATES)
    Q = P[np.ix_(transient, transient)]
    R = P[np.ix_(transient, absorbing)]
    return Q, R


def fundamental_matrix(T: np.ndarray, action: int = MONITOR) -> np.ndarray:
    """N = (I - Q)^-1. Entry (i,j) is the expected visits to j starting at i."""
    Q, _ = canonical_form(T, action)
    identity = np.eye(Q.shape[0], dtype=np.float64)
    return np.linalg.inv(identity - Q)


def absorption_prob(T: np.ndarray, belief: np.ndarray, n_steps: int, action: int = MONITOR) -> float:
    """P(reach HARM within `n_steps`), starting from `belief`.

    Non-decreasing in `n_steps` by construction: HARM is absorbing, so mass
    that arrives never leaves.
    """
    P = marginal_chain(T, action)
    state = np.asarray(belief, dtype=np.float64).copy()
    for _ in range(max(0, int(n_steps))):
        state = state @ P
    return float(state[HARM])


def absorption_curve(T: np.ndarray, belief: np.ndarray, max_steps: int = 5, action: int = MONITOR) -> dict[int, float]:
    """`{n: P(HARM within n)}` for n = 1..max_steps."""
    P = marginal_chain(T, action)
    state = np.asarray(belief, dtype=np.float64).copy()
    out: dict[int, float] = {}
    for n in range(1, max_steps + 1):
        state = state @ P
        out[n] = float(state[HARM])
    return out


def expected_steps_to_absorption(T: np.ndarray, belief: np.ndarray, action: int = MONITOR) -> float:
    """Expected number of steps before reaching HARM or CONTAINED."""
    N = fundamental_matrix(T, action)
    transient_mass = np.asarray([belief[s] for s in TRANSIENT_STATES], dtype=np.float64)
    total = float(transient_mass.sum())
    if total <= 1e-12:
        return 0.0
    return float((transient_mass @ N).sum())


def absorption_split(T: np.ndarray, belief: np.ndarray, action: int = MONITOR) -> dict[str, float]:
    """Eventual absorption probabilities, B = N R, split by destination."""
    N = fundamental_matrix(T, action)
    _, R = canonical_form(T, action)
    B = N @ R
    transient_mass = np.asarray([belief[s] for s in TRANSIENT_STATES], dtype=np.float64)
    absorbed = transient_mass @ B
    already = {HARM: float(belief[HARM]), CONTAINED: float(belief[CONTAINED])}
    return {
        "HARM": float(absorbed[list(ABSORBING_STATES).index(HARM)]) + already[HARM],
        "CONTAINED": float(absorbed[list(ABSORBING_STATES).index(CONTAINED)]) + already[CONTAINED],
    }
