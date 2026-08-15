"""Signal 1 — injection_likelihood.

Scores the most recent tool RESULT, never the call. The question is whether
returned content is trying to issue instructions to the agent.

Two implementations behind one name:

  transformers  `protectai/deberta-v3-base-prompt-injection-v2` on CPU,
                loaded ONCE at construction. Used when `torch` and
                `transformers` are installed (`pip install -e ".[neural]"`)
                and the weights are already in the local HF cache.

  lexical       A deterministic pattern scorer over the instruction-giving
                constructions the AgentDojo templates use. No download, no
                torch, works offline on an 8GB laptop.

Which one ran is reported in the explanation dict and printed in the latency
table, so no result is ever ambiguous about its detector.
"""

from __future__ import annotations

import re
from typing import Any

from sentinelz.config import FORCE_LEXICAL
from sentinelz.context import DecisionContext
from sentinelz.signals.base import clip01

MODEL_ID = "protectai/deberta-v3-base-prompt-injection-v2"

#: The lexical scorer reads the WHOLE result. Scoring only a prefix is a
#: trivially exploitable hole — pad the poisoned field with benign text and
#: the detector never sees the payload. AgentDojo's own email results already
#: run past 2000 characters with the injection near the end.
MAX_CHARS = 40_000

#: The neural classifier has a 512-token window, so it gets overlapping
#: chunks and the maximum score across them, rather than a prefix.
CHUNK_CHARS = 1500
CHUNK_OVERLAP = 200
MAX_CHUNKS = 8

#: (compiled pattern, weight). Weights are hand-set and additive; the sum is
#: squashed. Chosen to fire on instruction-giving, not on mere mention.
#:
#: These were written against the literal AgentDojo template strings, which
#: are terser than one expects — `ignore_previous` contains a typo
#: ("iunstructions"), `system_message` is `###(system_message)`, and `direct`
#: is a bare `TODO:`. `tests/test_signals.py::test_injection_fires_on_real_
#: agentdojo_templates` pins all four so a rewrite cannot silently regress to
#: catching only the verbose one.
_PATTERNS: tuple[tuple[re.Pattern[str], float], ...] = (
    # Explicit instruction override. Matched on "ignore ... previous" alone
    # rather than on the following noun: AgentDojo's own template misspells it
    # as "iunstructions", and any attacker can do the same.
    (re.compile(r"ignore\s+(all\s+)?(your\s+|the\s+)?(previous|prior|above|earlier)\b", re.I), 0.95),
    (re.compile(r"disregard\s+(all\s+)?(the\s+)?(previous|prior|above)", re.I), 0.90),
    # forged framing: pretending to be a system or out-of-band channel
    (re.compile(r"<\s*information\s*>|<\s*/?\s*system\s*>|\[\s*system\s*\]", re.I), 0.80),
    (re.compile(r"#{2,}\s*\(?\s*system[\s_-]?message\s*\)?", re.I), 0.85),
    (re.compile(r"this\s+is\s+an?\s+important\s+message\s+(from|to)", re.I), 0.75),
    (re.compile(r"new\s+(instructions?|task|directive)\s*:", re.I), 0.70),
    (re.compile(r"^\s*(todo|note to (the )?(ai|assistant|model)|action required)\s*[:\-]", re.I | re.M), 0.55),
    # task-hijack framing
    (re.compile(r"before\s+you\s+(can\s+)?(solve|continue|answer|proceed)", re.I), 0.70),
    (re.compile(r"after\s+you\s+do\s+that,?\s+you\s+can\s+(solve|continue)", re.I), 0.75),
    (re.compile(r"\byou\s+(must|should|have to|need to)\s+(now\s+)?(first\s+)?\w+", re.I), 0.45),
    (re.compile(r"do\s+not\s+(tell|mention|inform|reveal)\s+the\s+user", re.I), 0.85),
    # a bare imperative where data was expected
    (
        re.compile(
            r"(?:^|[.;:)\]]\s{0,3})(?:do\s+|please\s+){0,2}"
            r"(create|schedule|send|email|forward|transfer|delete|share|post|change|update|modify)"
            r"\s+(a|an|the|every|all|as much|\d)",
            re.I | re.M,
        ),
        0.35,
    ),
    (re.compile(r"\b(click|visit|go to)\s+(this|the following)?\s*link", re.I), 0.35),
    (re.compile(r"as\s+an?\s+(ai|assistant|language model)\b", re.I), 0.25),
    (re.compile(r"signed,?\s*$", re.I | re.M), 0.20),
)

#: A directive that also names somewhere outside the organisation to send
#: things to is much more likely to be an injection than a directive alone.
#: Gated on the directive weight so ordinary prose ("please send me the
#: report") in an email body does not trip it.
_CORROBORATION_WEIGHT = 0.30
_CORROBORATION_MIN_DIRECTIVE = 0.50


class InjectionSignal:
    """injection_likelihood, computed over the last tool result."""

    name = "injection_likelihood"

    def __init__(self, prefer_neural: bool = True) -> None:
        self._pipeline: Any | None = None
        self.implementation = "lexical"
        if prefer_neural and not FORCE_LEXICAL:
            self._pipeline = _try_load_classifier()
            if self._pipeline is not None:
                self.implementation = "transformers"

    def score(self, ctx: DecisionContext) -> tuple[float, dict[str, Any]]:
        text = ctx.last_result_text
        if not text.strip():
            return 0.0, {"implementation": self.implementation, "reason": "no tool result yet", "hits": []}

        snippet = text[:MAX_CHARS]
        if self._pipeline is not None:
            return self._score_neural(snippet)
        return self._score_lexical(snippet)

    # ------------------------------------------------------------ neural
    def _score_neural(self, text: str) -> tuple[float, dict[str, Any]]:
        chunks = _chunks(text)
        try:
            results = self._pipeline(chunks, truncation=True, max_length=512)  # type: ignore[misc]
        except Exception as exc:  # a broken model must not open the gate
            return 1.0, {"implementation": "transformers", "error": str(exc)[:200], "hits": []}

        best_value, best_label, best_confidence = 0.0, "", 0.0
        for result in results:
            label = str(result.get("label", "")).upper()
            confidence = float(result.get("score", 0.0))
            value = confidence if label in {"INJECTION", "LABEL_1"} else 1.0 - confidence
            if value > best_value:
                best_value, best_label, best_confidence = value, label, confidence

        return clip01(best_value), {
            "implementation": "transformers",
            "model": MODEL_ID,
            "label": best_label,
            "confidence": round(best_confidence, 4),
            "n_chunks": len(chunks),
            "hits": [],
        }

    # ----------------------------------------------------------- lexical
    def _score_lexical(self, text: str) -> tuple[float, dict[str, Any]]:
        total = 0.0
        hits: list[dict[str, Any]] = []
        for pattern, weight in _PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            total += weight
            hits.append({"pattern": pattern.pattern[:48], "weight": weight, "match": match.group(0)[:60]})

        corroborated = False
        if total >= _CORROBORATION_MIN_DIRECTIVE and _names_external_destination(text):
            total += _CORROBORATION_WEIGHT
            corroborated = True

        # Saturating: three strong hits should read as certain, not as 2.7.
        value = 1.0 - pow(2.718281828459045, -1.35 * total)
        return clip01(value), {
            "implementation": "lexical",
            "raw_weight": round(total, 3),
            "corroborated_by_external_destination": corroborated,
            "hits": hits[:6],
            "n_hits": len(hits),
        }


def _chunks(text: str) -> list[str]:
    """Overlapping windows covering the whole result, capped for the budget."""
    if len(text) <= CHUNK_CHARS:
        return [text]
    stride = CHUNK_CHARS - CHUNK_OVERLAP
    windows = [text[i : i + CHUNK_CHARS] for i in range(0, len(text), stride)]
    return windows[:MAX_CHUNKS]


def _names_external_destination(text: str) -> bool:
    """Does the text name somewhere outside the organisation to send to?"""
    from sentinelz.tiers import is_external_target

    if is_external_target(text):
        return True
    return bool(re.search(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b", text))


def _try_load_classifier() -> Any | None:
    """Load the HF classifier once, or return None and stay lexical.

    Never downloads during a run: `local_files_only=True`. Fetching the
    weights is a setup step (`python -m sentinelz.signals.injection --fetch`),
    because the demo has to work in airplane mode.
    """
    try:
        from transformers import pipeline  # type: ignore

        return pipeline(
            "text-classification",
            model=MODEL_ID,
            tokenizer=MODEL_ID,
            device=-1,
            truncation=True,
            model_kwargs={"local_files_only": True},
        )
    except Exception:
        return None


def _fetch() -> int:  # pragma: no cover - setup helper
    """Download the classifier weights into the local HF cache."""
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer  # type: ignore

        AutoTokenizer.from_pretrained(MODEL_ID)
        AutoModelForSequenceClassification.from_pretrained(MODEL_ID)
    except Exception as exc:
        print(f"could not fetch {MODEL_ID}: {exc}")
        print("the lexical fallback will be used; this is fine and is reported in every table")
        return 1
    print(f"cached {MODEL_ID}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(_fetch() if "--fetch" in sys.argv else 0)
