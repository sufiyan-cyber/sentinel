"""The observation encoder — where the learned layer meets the belief layer.

The M4 hazard model is not replaced by the POMDP; it is promoted to the
sensor. Its scalar output is quantised into a quintile bin and crossed with
the binary taint signal:

    obs = hazard_bin * 2 + tainted        obs in 0..9

Ten observations is small enough for the POMDP to stay exact and
interpretable, and the bin edges are fitted ONCE on the benign validation
split and saved. They are never recomputed at runtime — a detector whose
thresholds drift with the traffic it sees cannot be audited.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from sentinelz.config import MODELS_DIR
from sentinelz.evidence.canonical import dumps_str, loads
from sentinelz.pomdp.states import N_OBSERVATIONS

BINS_PATH = MODELS_DIR / "obs_bins_v1.json"
N_HAZARD_BINS = 5


class ObservationEncoder:
    """Quantises (p_harm, tainted) into one of ten observation symbols."""

    def __init__(self, edges: list[float] | None = None) -> None:
        #: Four interior edges give five quintile bins.
        self.edges: list[float] = list(edges) if edges is not None else [0.2, 0.4, 0.6, 0.8]
        self.fitted = edges is not None
        if len(self.edges) != N_HAZARD_BINS - 1:
            raise ValueError(f"expected {N_HAZARD_BINS - 1} interior edges, got {len(self.edges)}")

    # --------------------------------------------------------------- use
    def hazard_bin(self, p_harm: float) -> int:
        return int(np.searchsorted(np.asarray(self.edges, dtype=np.float64), float(p_harm), side="right"))

    def obs(self, p_harm: float, tainted: bool) -> int:
        symbol = self.hazard_bin(p_harm) * 2 + (1 if tainted else 0)
        return int(min(max(symbol, 0), N_OBSERVATIONS - 1))

    def describe(self, symbol: int) -> str:
        return f"hazard_bin={symbol // 2} taint={symbol % 2}"

    # --------------------------------------------------------------- fit
    @classmethod
    def fit(cls, benign_p_harm: list[float]) -> ObservationEncoder:
        """Fit quintile edges on the benign validation split.

        Benign only: the bins describe what normal looks like, so that an
        attack shows up as an unusual bin rather than being absorbed into a
        distribution it helped define.
        """
        values = np.asarray([v for v in benign_p_harm if np.isfinite(v)], dtype=np.float64)
        if values.size < N_HAZARD_BINS:
            return cls()
        quantiles = np.quantile(values, [0.2, 0.4, 0.6, 0.8])
        edges = _strictly_increasing([float(q) for q in quantiles])
        return cls(edges=edges)

    # -------------------------------------------------------------- i/o
    def save(self, path: Path | None = None) -> Path:
        path = path or BINS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            dumps_str({"edges": self.edges, "n_hazard_bins": N_HAZARD_BINS, "version": 1}), encoding="utf-8"
        )
        return path

    @classmethod
    def load(cls, path: Path | None = None) -> ObservationEncoder:
        path = path or BINS_PATH
        if not path.exists():
            return cls()
        data = loads(path.read_text(encoding="utf-8"))
        return cls(edges=[float(e) for e in data["edges"]])


def _strictly_increasing(values: list[float], epsilon: float = 1e-6) -> list[float]:
    """Nudge tied quantiles apart. Ties happen when most benign steps score
    identically, which is exactly the common case."""
    out: list[float] = []
    previous = -float("inf")
    for value in values:
        candidate = max(value, previous + epsilon)
        out.append(candidate)
        previous = candidate
    return out
