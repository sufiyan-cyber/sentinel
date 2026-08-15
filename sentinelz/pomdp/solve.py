"""Finite-horizon policy by grid value iteration on the 5-simplex.

|S| = 5, so the regular grid at resolution 20 has C(24,4) = 10626 points and
exact backward induction is about a second of numpy. A POMDP solver library
would add a dependency and take away the thing that makes this worth doing:
the policy is a table you can print, inspect and argue with.

Costs are stated, not tuned into invisibility, and every constant used is
saved next to the policy.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path
from typing import Any

import numpy as np

from sentinelz.config import MODELS_DIR
from sentinelz.pomdp.states import (
    ACTION_NAMES,
    ALLOW,
    HARM,
    MONITOR,
    N_ACTIONS,
    N_OBSERVATIONS,
    N_STATES,
    REVOKE,
    SCOPE_DOWN,
    STEP_UP,
)

POLICY_PATH = MODELS_DIR / "policy_v1.npz"

RESOLUTION = 20
HORIZON = 5
GAMMA = 1.0

#: Cost of taking each action, per step. ALLOW is free; REVOKE ends the
#: session's usefulness, so it is expensive but far cheaper than harm.
ACTION_COSTS: dict[int, float] = {
    ALLOW: 0.0,
    MONITOR: 0.01,
    SCOPE_DOWN: 0.3,
    STEP_UP: 0.6,
    REVOKE: 3.0,
}

#: Charged ON THE TRANSITION into HARM, not per step while sitting in it.
#: Charging per step would make the total depend on the horizon, which is an
#: implementation detail and not a statement about how bad harm is.
HARM_COST = 100.0


class GridPolicy:
    """A solved policy: argmin action at each grid point, looked up in O(1)."""

    def __init__(
        self,
        grid: np.ndarray,
        actions: np.ndarray,
        values: np.ndarray,
        q_values: np.ndarray,
        T: np.ndarray,
        O: np.ndarray,
        constants: dict[str, Any],
    ) -> None:
        self.grid = grid
        self.actions = actions
        self.values = values
        self.q_values = q_values
        self.T = T
        self.O = O
        self.constants = constants
        self.resolution = int(constants.get("resolution", RESOLUTION))
        self._lookup = _build_lookup(self.resolution)

    # ------------------------------------------------------------- use
    def index_of(self, belief: np.ndarray) -> int:
        """Nearest grid point to `belief`."""
        return int(_nearest_indices(np.asarray(belief, dtype=np.float64).reshape(1, -1), self.resolution, self._lookup)[0])

    def act(self, belief: np.ndarray) -> int:
        """The chosen action. A table lookup — no search at runtime."""
        return int(self.actions[self.index_of(belief)])

    def expected_costs(self, belief: np.ndarray) -> np.ndarray:
        """Expected cost of all five actions at this belief, for the UI."""
        return self.q_values[self.index_of(belief)].copy()

    def value(self, belief: np.ndarray) -> float:
        return float(self.values[self.index_of(belief)])

    # ------------------------------------------------------------- i/o
    def save(self, path: Path | None = None) -> Path:
        path = path or POLICY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        arrays: dict[str, Any] = {
            "grid": self.grid,
            "actions": self.actions,
            "values": self.values,
            "q_values": self.q_values,
        }
        # Every cost constant is saved next to the policy, so a stored policy
        # can always be traced back to the trade-off that produced it.
        arrays.update({f"const_{k}": np.asarray(v) for k, v in self.constants.items()})
        np.savez(path, **arrays)
        return path

    @classmethod
    def load(cls, path: Path | None = None, T: np.ndarray | None = None, O: np.ndarray | None = None) -> GridPolicy:
        path = path or POLICY_PATH
        if not path.exists():
            raise FileNotFoundError(f"no policy at {path}; run `sz solve-pomdp`")
        data = np.load(path)
        constants = {key[len("const_") :]: data[key].item() for key in data.files if key.startswith("const_")}
        if T is None or O is None:
            from sentinelz.pomdp.estimate import load_matrices

            T, O = load_matrices()
        return cls(data["grid"], data["actions"], data["values"], data["q_values"], T, O, constants)


# ---------------------------------------------------------------- solving


def simplex_grid(resolution: int = RESOLUTION, n: int = N_STATES) -> np.ndarray:
    """Every point of the regular grid on the (n-1)-simplex."""
    compositions = [
        c for c in itertools.product(range(resolution + 1), repeat=n) if sum(c) == resolution
    ]
    return np.asarray(compositions, dtype=np.float64) / float(resolution)


def solve(
    T: np.ndarray,
    O: np.ndarray,
    resolution: int = RESOLUTION,
    horizon: int = HORIZON,
    gamma: float = GAMMA,
    harm_cost: float = HARM_COST,
    action_costs: dict[int, float] | None = None,
) -> GridPolicy:
    """Backward induction over `horizon` steps."""
    action_costs = action_costs or ACTION_COSTS
    grid = simplex_grid(resolution)
    n_points = grid.shape[0]
    lookup = _build_lookup(resolution)

    immediate = np.empty((n_points, N_ACTIONS), dtype=np.float64)
    successors = np.empty((n_points, N_ACTIONS, N_OBSERVATIONS), dtype=np.int64)
    obs_probabilities = np.empty((n_points, N_ACTIONS, N_OBSERVATIONS), dtype=np.float64)

    for action in range(N_ACTIONS):
        predicted = grid @ T[:, action, :]                      # (G, S)
        # Harm is charged when the transition enters HARM, and only from a
        # state that is not already HARM.
        entering_harm = grid[:, :HARM] @ T[:HARM, action, HARM] + grid[:, HARM + 1 :] @ T[HARM + 1 :, action, HARM]
        immediate[:, action] = action_costs[action] + harm_cost * entering_harm

        joint = predicted[:, :, None] * O[None, :, :]           # (G, S, Obs)
        probabilities = joint.sum(axis=1)                       # (G, Obs)
        obs_probabilities[:, action, :] = probabilities

        for observation in range(N_OBSERVATIONS):
            column = joint[:, :, observation]
            total = probabilities[:, observation : observation + 1]
            posterior = np.where(total > 1e-12, column / np.where(total > 0, total, 1.0), predicted)
            successors[:, action, observation] = _nearest_indices(posterior, resolution, lookup)

    values = np.zeros(n_points, dtype=np.float64)
    q_values = np.zeros((n_points, N_ACTIONS), dtype=np.float64)
    for _ in range(horizon):
        for action in range(N_ACTIONS):
            future = (obs_probabilities[:, action, :] * values[successors[:, action, :]]).sum(axis=1)
            q_values[:, action] = immediate[:, action] + gamma * future
        values = q_values.min(axis=1)

    actions = q_values.argmin(axis=1).astype(np.int64)
    constants = {
        "resolution": resolution,
        "horizon": horizon,
        "gamma": gamma,
        "harm_cost": harm_cost,
        **{f"cost_{ACTION_NAMES[a]}": c for a, c in action_costs.items()},
    }
    return GridPolicy(grid, actions, values, q_values, T, O, constants)


def solve_frontier(
    T: np.ndarray,
    O: np.ndarray,
    harm_costs: tuple[float, ...] = (10.0, 30.0, 100.0, 300.0, 1000.0),
    **kwargs: Any,
) -> dict[float, GridPolicy]:
    """One policy per harm cost — figure F3, the security/utility frontier.

    The threshold is never a bare constant: it is a consequence of the cost
    ratio, and the frontier shows what happens as that ratio moves.
    """
    return {cost: solve(T, O, harm_cost=cost, **kwargs) for cost in harm_costs}


# ---------------------------------------------------------------- lookup


def _build_lookup(resolution: int) -> np.ndarray:
    """Dense map from a grid composition to its index.

    Encodes the first four coordinates in mixed radix (the fifth is implied by
    the sum), giving an array of (R+1)^4 entries — 194k at R=20.
    """
    radix = resolution + 1
    table = np.full(radix**4, -1, dtype=np.int64)
    compositions = [c for c in itertools.product(range(radix), repeat=N_STATES) if sum(c) == resolution]
    for index, composition in enumerate(compositions):
        code = (
            composition[0] * radix**3
            + composition[1] * radix**2
            + composition[2] * radix
            + composition[3]
        )
        table[code] = index
    return table


def _nearest_indices(beliefs: np.ndarray, resolution: int, lookup: np.ndarray) -> np.ndarray:
    """Nearest grid point for each row, as an index into `simplex_grid`.

    Rounds each coordinate down, then hands the leftover units to the largest
    fractional parts. That keeps the result on the simplex, which plain
    rounding does not.
    """
    scaled = np.asarray(beliefs, dtype=np.float64) * resolution
    floor = np.floor(scaled).astype(np.int64)
    np.clip(floor, 0, resolution, out=floor)
    remainder = resolution - floor.sum(axis=1)

    fractional = scaled - floor
    order = np.argsort(-fractional, axis=1, kind="stable")
    rows = np.arange(beliefs.shape[0])
    for slot in range(N_STATES):
        take = remainder > slot
        if not take.any():
            break
        columns = order[:, slot]
        floor[rows[take], columns[take]] += 1

    # Any residual (from clipping) goes to the first coordinate.
    residual = resolution - floor.sum(axis=1)
    floor[:, 0] += residual
    np.clip(floor, 0, resolution, out=floor)

    radix = resolution + 1
    codes = floor[:, 0] * radix**3 + floor[:, 1] * radix**2 + floor[:, 2] * radix + floor[:, 3]
    indices = lookup[codes]
    return np.where(indices >= 0, indices, 0)


# ------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Solve the POMDP policy (M4b).")
    parser.add_argument("--resolution", type=int, default=RESOLUTION)
    parser.add_argument("--horizon", type=int, default=HORIZON)
    parser.add_argument("--harm-cost", type=float, default=HARM_COST)
    parser.add_argument("--frontier", action="store_true", help="also solve the F3 frontier")
    args = parser.parse_args(argv)

    from sentinelz.pomdp.estimate import load_matrices

    T, O = load_matrices()
    policy = solve(T, O, resolution=args.resolution, horizon=args.horizon, harm_cost=args.harm_cost)
    policy.save()

    counts = np.bincount(policy.actions, minlength=N_ACTIONS)
    print(f"solved on {policy.grid.shape[0]} grid points, horizon {args.horizon}, harm cost {args.harm_cost}")
    for action in range(N_ACTIONS):
        share = counts[action] / policy.grid.shape[0]
        print(f"  {ACTION_NAMES[action]:<11} {counts[action]:>6} grid points ({share:5.1%})")
    print(f"  wrote {POLICY_PATH}")

    print("\nsanity check — action at some named beliefs:")
    for name, belief in _NAMED_BELIEFS.items():
        vector = np.asarray(belief, dtype=np.float64)
        print(f"  {name:<28} -> {ACTION_NAMES[policy.act(vector)]}")

    if args.frontier:
        frontier = solve_frontier(T, O, resolution=args.resolution, horizon=args.horizon)
        for cost, solved in frontier.items():
            solved.save(MODELS_DIR / f"policy_harm{int(cost)}_v1.npz")
        print(f"\nwrote {len(frontier)} frontier policies for F3")
    return 0


_NAMED_BELIEFS: dict[str, list[float]] = {
    "pure BENIGN": [1.0, 0.0, 0.0, 0.0, 0.0],
    "mostly BENIGN": [0.8, 0.15, 0.05, 0.0, 0.0],
    "RECON": [0.2, 0.7, 0.1, 0.0, 0.0],
    "RECON -> ESCALATION": [0.1, 0.4, 0.5, 0.0, 0.0],
    "ESCALATION": [0.05, 0.15, 0.8, 0.0, 0.0],
    "ESCALATION, harm near": [0.0, 0.1, 0.6, 0.3, 0.0],
    "CONTAINED": [0.0, 0.0, 0.0, 0.0, 1.0],
}


if __name__ == "__main__":
    sys.exit(main())
