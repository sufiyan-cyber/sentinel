"""The Bayes belief filter.

Predict then correct, once per step, never folded together. Preallocated
float64 arrays, no allocation inside `update()` — this runs on every tool
call and shares a 100ms budget with the signals.
"""

from __future__ import annotations

import numpy as np

from sentinelz.pomdp.states import N_ACTIONS, N_OBSERVATIONS, N_STATES

MIN_NORMALISER = 1e-12


class BeliefFilter:
    """Belief over the five states, updated per (action, observation) pair."""

    def __init__(self, T: np.ndarray, O: np.ndarray, prior: np.ndarray | list[float] | None = None) -> None:
        if T.shape != (N_STATES, N_ACTIONS, N_STATES):
            raise ValueError(f"T must be {(N_STATES, N_ACTIONS, N_STATES)}, got {T.shape}")
        if O.shape != (N_STATES, N_OBSERVATIONS):
            raise ValueError(f"O must be {(N_STATES, N_OBSERVATIONS)}, got {O.shape}")

        self.T = np.ascontiguousarray(T, dtype=np.float64)
        self.O = np.ascontiguousarray(O, dtype=np.float64)
        self.prior = np.asarray(prior if prior is not None else [1.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float64)

        self._belief = self.prior.copy()
        self._predicted = np.empty(N_STATES, dtype=np.float64)
        self._scratch = np.empty(N_STATES, dtype=np.float64)
        #: How many times the normaliser was too small to divide by. A
        #: non-zero count here is a real diagnostic, not a rounding artefact.
        self.degenerate_updates = 0

    @property
    def belief(self) -> np.ndarray:
        return self._belief.copy()

    def reset(self) -> None:
        self._belief[:] = self.prior
        self.degenerate_updates = 0

    def update(self, action: int, observation: int) -> np.ndarray:
        """One predict-then-correct step. Returns a copy of the new belief."""
        # predict: b_pred[s'] = sum_s T[s, a, s'] b[s]
        np.dot(self._belief, self.T[:, action, :], out=self._predicted)

        # correct: b_new[s'] = O[s', o] * b_pred[s']
        np.multiply(self._predicted, self.O[:, observation], out=self._scratch)

        total = float(self._scratch.sum())
        if total < MIN_NORMALISER:
            # This observation is impossible under the predicted belief. Keep
            # the prediction rather than dividing by zero, and say so.
            self.degenerate_updates += 1
            self._belief[:] = self._predicted
        else:
            np.divide(self._scratch, total, out=self._belief)
        return self._belief.copy()

    def set_belief(self, belief: np.ndarray) -> None:
        """For replay and for the UI's timeline scrubber."""
        self._belief[:] = np.asarray(belief, dtype=np.float64)


def update_belief(T: np.ndarray, O: np.ndarray, belief: np.ndarray, action: int, observation: int) -> np.ndarray:
    """Pure-function form, used by the solver where there is no session state."""
    predicted = belief @ T[:, action, :]
    corrected = predicted * O[:, observation]
    total = corrected.sum()
    return predicted.copy() if total < MIN_NORMALISER else corrected / total
