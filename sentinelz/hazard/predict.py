"""Loading and applying the trained hazard model.

`p_harm` runs on every tool call and must stay under 1ms. The model is loaded
once, at startup; `HazardModel.load()` is never called inside a request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from sentinelz.evidence.canonical import loads
from sentinelz.hazard.features import FEATURE_NAMES


class HazardModel:
    """A fitted logistic hazard model plus its provenance."""

    def __init__(self, model: Any, metadata: dict[str, Any] | None = None) -> None:
        self.model = model
        self.metadata = metadata or {}
        stored = self.metadata.get("feature_names")
        if stored is not None and list(stored) != list(FEATURE_NAMES):
            raise ValueError(
                "hazard model was trained on a different feature order; "
                "retrain rather than reordering FEATURE_NAMES"
            )

    @classmethod
    def load(cls, path: Path | None = None, k: int = 3) -> HazardModel:
        import joblib

        from sentinelz.hazard.train import model_path, sidecar_path

        path = path or model_path(k)
        if not path.exists():
            raise FileNotFoundError(f"no hazard model at {path}; run `sz train-hazard`")
        model = joblib.load(path)

        sidecar = sidecar_path(k)
        metadata = loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        return cls(model, metadata)

    def p_harm(self, features: np.ndarray) -> float:
        """P(harm within k steps). Must stay under 1ms."""
        vector = np.asarray(features, dtype=np.float64).reshape(1, -1)
        return float(self.model.predict_proba(vector)[0, 1])

    def p_harm_batch(self, features: np.ndarray) -> np.ndarray:
        matrix = np.asarray(features, dtype=np.float64)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        return self.model.predict_proba(matrix)[:, 1]

    @property
    def k(self) -> int:
        return int(self.metadata.get("k", 3))

    def coefficients(self) -> dict[str, float]:
        return dict(self.metadata.get("coefficients", {}))
