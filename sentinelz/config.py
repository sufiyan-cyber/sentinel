"""Defense-side configuration: paths, budgets, sensitivity tiers.

Everything here is read from the environment once, at import time. Nothing in
`sentinelz/` may reach the network or read the environment inside a request.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

MODELS_DIR = REPO_ROOT / "models"
RUNS_DIR = REPO_ROOT / "runs"
CONFIGS_DIR = REPO_ROOT / "configs"

MODELS_DIR.mkdir(exist_ok=True)
RUNS_DIR.mkdir(exist_ok=True)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


#: Hard wall for signals + hazard + policy. Exceeding it is an error path,
#: and every error path returns REVOKE.
DECISION_BUDGET_MS: int = _int_env("SENTINELZ_DECISION_BUDGET_MS", 100)

#: Budget for the signal bundle alone (PRD milestone 3 acceptance criterion).
SIGNAL_BUDGET_MS: int = _int_env("SENTINELZ_SIGNAL_BUDGET_MS", 80)

SEED: int = _int_env("SENTINELZ_SEED", 0)

#: A tool result scoring above this on `injection_likelihood` taints every
#: entity extracted from it.
TAINT_THRESHOLD: float = _float_env("SENTINELZ_TAINT_THRESHOLD", 0.5)

#: Ablation baseline only. The POMDP policy is the real decision maker.
HAZARD_THRESHOLD: float = _float_env("SENTINELZ_HAZARD_THRESHOLD", 0.5)

#: Set to "1" to force the lexical fallbacks even when torch/transformers exist.
FORCE_LEXICAL: bool = os.environ.get("SENTINELZ_FORCE_LEXICAL", "").strip() == "1"

#: Sensitivity tiers. Tier of a target minus the max tier a task needs is the
#: `privilege_delta` signal. Declared here so the mapping is auditable.
TIER_NAMES: dict[str, int] = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}
MAX_TIER: int = 3
