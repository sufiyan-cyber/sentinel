"""Signal 2 — task_alignment.

`1 - cosine(embed(tool_call_text), embed(user_task))`, plus a penalty when the
tool is outside the set a correct solution needs. High score = this call does
not plausibly serve what the user asked for.

Embeddings come from a small local sentence encoder when one is installed,
otherwise from a hashed character-n-gram vectoriser. The fallback is weaker
but it is deterministic, offline, and about 40 microseconds — and the
implementation used is reported alongside the score.

Task embeddings are cached: the user task does not change within a session.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

from sentinelz.config import FORCE_LEXICAL
from sentinelz.context import DecisionContext
from sentinelz.signals.base import clip01

EMBED_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
HASH_DIM = 512
CHAR_NGRAM = 4

#: The two components of the score. Word-level overlap between a tool call and
#: a task sentence is very sparse, so distance alone saturates at 1.0 and
#: carries no information; the scope term is what keeps the signal graded.
W_DISTANCE = 0.6
W_OUT_OF_SCOPE = 0.4

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class AlignmentSignal:
    """task_alignment, in [0,1], higher = less aligned."""

    name = "task_alignment"

    def __init__(self, prefer_neural: bool = True) -> None:
        self._encoder: Any | None = None
        self.implementation = "hashed-ngram"
        if prefer_neural and not FORCE_LEXICAL:
            self._encoder = _try_load_encoder()
            if self._encoder is not None:
                self.implementation = "sentence-transformer"
        self._task_cache: dict[str, np.ndarray] = {}

    def score(self, ctx: DecisionContext) -> tuple[float, dict[str, Any]]:
        task_vec = self._task_vector(ctx.user_task_text)
        call_vec = self._embed(ctx.pending.as_text())
        cosine = float(np.dot(task_vec, call_vec))
        distance = clip01(1.0 - cosine)

        out_of_scope = bool(ctx.expected_tools) and ctx.pending.tool_name not in ctx.expected_tools
        value = clip01(W_DISTANCE * distance + W_OUT_OF_SCOPE * (1.0 if out_of_scope else 0.0))

        return value, {
            "implementation": self.implementation,
            "cosine": round(cosine, 4),
            "distance": round(distance, 4),
            "out_of_scope": out_of_scope,
            "expected_tools": sorted(ctx.expected_tools)[:8],
            "penalty": W_OUT_OF_SCOPE if out_of_scope else 0.0,
        }

    # ----------------------------------------------------------- caching
    def _task_vector(self, text: str) -> np.ndarray:
        cached = self._task_cache.get(text)
        if cached is None:
            cached = self._embed(text)
            self._task_cache[text] = cached
        return cached

    # --------------------------------------------------------- embedding
    def _embed(self, text: str) -> np.ndarray:
        if self._encoder is not None:
            try:
                vector = np.asarray(self._encoder.encode(text[:1000]), dtype=np.float64)
                return _unit(vector)
            except Exception:
                self._encoder = None
                self.implementation = "hashed-ngram"
        return _hash_embed(text)


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-12 else vector


def _hash_embed(text: str) -> np.ndarray:
    """Hashed bag of word unigrams, word bigrams and character 4-grams.

    The character n-grams matter: without them, "search_calendar_events" and
    "What events do I have on May 15th?" share no whole token and the cosine
    is exactly zero for almost every call, which makes the signal a constant.

    Deterministic across processes: uses FNV-1a, not Python's salted `hash()`,
    so a fixed seed reproduces byte-identical logs.
    """
    lowered = text.lower()
    tokens = _TOKEN_RE.findall(lowered)
    vector = np.zeros(HASH_DIM, dtype=np.float64)
    if not tokens:
        return vector

    grams = list(tokens)
    grams += [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]
    letters = " ".join(tokens)
    grams += [letters[i : i + CHAR_NGRAM] for i in range(max(0, len(letters) - CHAR_NGRAM + 1))]

    for gram in grams:
        vector[_stable_hash(gram) % HASH_DIM] += 1.0
    vector = np.log1p(vector)
    return _unit(vector)


def _stable_hash(text: str) -> int:
    """FNV-1a. Small, dependency-free, and identical on every platform."""
    value = 0xCBF29CE484222325
    for byte in text.encode("utf-8"):
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return value


def _try_load_encoder() -> Any | None:
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        return SentenceTransformer(EMBED_MODEL_ID, device="cpu", local_files_only=True)
    except Exception:
        return None


def cosine_similarity(a: str, b: str) -> float:
    """Convenience for tests and the UI."""
    va, vb = _hash_embed(a), _hash_embed(b)
    return float(np.dot(va, vb)) if math.isfinite(float(np.dot(va, vb))) else 0.0
