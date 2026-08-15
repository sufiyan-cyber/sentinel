"""Feature construction for the hazard model.

For each of the five signals: its current value, its running maximum over the
session so far, and its slope over the last three steps. Plus the step index
and the number of distinct tools used. Seventeen features, order fixed.

The running max and the slope are what make this a *session-level* model
rather than a per-call classifier — a single call can look innocent while the
trajectory it belongs to does not.
"""

from __future__ import annotations

import numpy as np

from sentinelz.signals.base import SIGNAL_NAMES

#: Fixed forever. Saved into every model sidecar and checked on load.
FEATURE_NAMES: tuple[str, ...] = (
    *(f"{name}_now" for name in SIGNAL_NAMES),
    *(f"{name}_max" for name in SIGNAL_NAMES),
    *(f"{name}_slope" for name in SIGNAL_NAMES),
    "step_index",
    "n_distinct_tools",
)
N_FEATURES = len(FEATURE_NAMES)

SLOPE_WINDOW = 3
STEP_INDEX_SCALE = 15.0
TOOL_COUNT_SCALE = 10.0


def build(signals_history: list[dict[str, float]], tools_so_far: list[str]) -> np.ndarray:
    """Build the feature vector for the step that just produced the last entry
    of `signals_history`.

    Args:
        signals_history: one dict of signal scores per step, oldest first,
            including the current step.
        tools_so_far: tool names for the same steps, in the same order.
    """
    if not signals_history:
        return np.zeros(N_FEATURES, dtype=np.float64)

    current = signals_history[-1]
    values = np.empty(N_FEATURES, dtype=np.float64)

    for i, name in enumerate(SIGNAL_NAMES):
        series = [float(entry.get(name, 0.0)) for entry in signals_history]
        values[i] = series[-1]
        values[len(SIGNAL_NAMES) + i] = max(series)
        values[2 * len(SIGNAL_NAMES) + i] = _slope(series[-SLOPE_WINDOW:])

    values[3 * len(SIGNAL_NAMES)] = min(len(signals_history) - 1, STEP_INDEX_SCALE) / STEP_INDEX_SCALE
    values[3 * len(SIGNAL_NAMES) + 1] = min(len(set(tools_so_far)), TOOL_COUNT_SCALE) / TOOL_COUNT_SCALE
    assert current is not None
    return values


def _slope(window: list[float]) -> float:
    """Least-squares slope over up to `SLOPE_WINDOW` points, in [-1, 1].

    One point has no slope; the convention is 0.0 rather than undefined.
    """
    n = len(window)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=np.float64)
    y = np.asarray(window, dtype=np.float64)
    x_mean = x.mean()
    denominator = float(((x - x_mean) ** 2).sum())
    if denominator <= 0.0:
        return 0.0
    slope = float(((x - x_mean) * (y - y.mean())).sum() / denominator)
    return max(-1.0, min(1.0, slope))


def build_session_matrix(signals_history: list[dict[str, float]], tools: list[str]) -> np.ndarray:
    """Feature vectors for every prefix of a session, shape (T, N_FEATURES)."""
    rows = [build(signals_history[: t + 1], tools[: t + 1]) for t in range(len(signals_history))]
    return np.vstack(rows) if rows else np.zeros((0, N_FEATURES), dtype=np.float64)
