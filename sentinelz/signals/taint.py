"""Signal 4 — taint. The most important one.

The distinction that matters is not "the agent read a poisoned document" but
"the agent is now acting on it". Taint is how that becomes mechanical:

  * a tool result that scores above the injection threshold is attacker
    controlled, so every identifier extracted from it enters the taint set,
    and the session's context is marked poisoned;
  * a later call is **directly** tainted if any of its argument values
    overlaps the taint set;
  * a later call is **indirectly** tainted if it was chosen after the context
    was poisoned — the agent's plan from that point on is downstream of
    attacker-controlled content, whether or not the attacker's own strings
    appear in the arguments;
  * a tainted call's own result is itself tainted, so influence propagates
    across hops without the classifier having to fire again.

Returns 1.0 if tainted (either way), else 0.0, as the spec requires. The
explanation dict records which kind, because they mean different things.

**Why the indirect clause exists.** With direct overlap alone, taint first
fires *at* the harmful call — measured at 42 of 46 attacked sessions, and
before it in 2. That makes taint a coincident indicator, gives it a negative
hazard coefficient, and leaves the advance-warning claim with nothing to stand
on. The indirect clause fires at the poisoned *read*, which is one or more
steps earlier, and that gap is the warning.

The cost is that every call after a poisoned read is flagged. That is what the
graduated policy is for: taint alone is worth SCOPE_DOWN, not REVOKE.
"""

from __future__ import annotations

import re
from typing import Any

from sentinelz.config import TAINT_THRESHOLD
from sentinelz.context import DecisionContext

#: Identifier shapes worth tracking. Deliberately narrow: tainting every word
#: would make the signal fire on everything and mean nothing.
_ENTITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                 # email addresses
    re.compile(r"https?://[^\s\"'<>)\]]+"),                  # urls
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),         # ibans
    re.compile(r"\b\d{4,}\b"),                               # codes, ids, amounts
    re.compile(r"\b[\w-]+\.(?:docx|xlsx|pptx|csv|pdf|txt)\b", re.I),  # filenames
)

_STOP_ENTITIES = frozenset({"2024", "2025", "2026", "0000", "1234"})
MIN_OVERLAP_LEN = 4


class TaintTracker:
    """Per-session taint state. One tracker per session, never shared."""

    def __init__(self, threshold: float = TAINT_THRESHOLD) -> None:
        self.threshold = threshold
        self.tainted: set[str] = set()
        #: True once attacker-controlled content has entered the context. From
        #: this point the agent's plan is downstream of it.
        self.context_poisoned = False
        #: Step index at which the context was first poisoned.
        self.poisoned_at_hop: int | None = None
        #: (hop, entity, source_tool) — the provenance the UI shows.
        self.provenance: list[tuple[int, str, str]] = []
        self._hop = 0

    def observe_result(self, tool_name: str, result_text: str, injection_score: float, call_was_tainted: bool = False) -> set[str]:
        """Absorb one tool result. Returns the entities newly tainted.

        A result taints when either the injection classifier fired on it, or
        the call that produced it was already tainted — that second clause is
        what carries influence past hop one.
        """
        if injection_score < self.threshold and not call_was_tainted:
            return set()

        self._hop += 1
        if not self.context_poisoned:
            self.context_poisoned = True
            self.poisoned_at_hop = self._hop
        found = extract_entities(result_text)
        new = found - self.tainted
        self.tainted |= new
        for entity in sorted(new):
            self.provenance.append((self._hop, entity, tool_name))
        return new

    def is_tainted(self, values: list[str]) -> tuple[bool, list[str]]:
        """Does any argument value overlap the taint set?"""
        matched: list[str] = []
        for value in values:
            lowered = value.lower()
            for entity in self.tainted:
                if len(entity) >= MIN_OVERLAP_LEN and entity.lower() in lowered:
                    matched.append(entity)
        return bool(matched), sorted(set(matched))

    def reset(self) -> None:
        self.tainted.clear()
        self.provenance.clear()
        self.context_poisoned = False
        self.poisoned_at_hop = None
        self._hop = 0


class TaintSignal:
    """taint, 1.0 or 0.0.

    Reads the tracker's set through the context so the signal itself stays
    stateless and the session owns the state.
    """

    name = "taint"

    def __init__(self, tracker: TaintTracker | None = None) -> None:
        self.tracker = tracker or TaintTracker()

    def score(self, ctx: DecisionContext) -> tuple[float, dict[str, Any]]:
        # The context's taint_set is authoritative; the tracker keeps
        # provenance for the explanation.
        self.tracker.tainted |= ctx.taint_set
        direct, matched = self.tracker.is_tainted(ctx.pending.arg_values())
        indirect = self.tracker.context_poisoned
        tainted = direct or indirect
        return (1.0 if tainted else 0.0), {
            "tainted": tainted,
            "kind": "direct" if direct else ("indirect" if indirect else "none"),
            "matched_entities": matched[:8],
            "context_poisoned": indirect,
            "poisoned_at_hop": self.tracker.poisoned_at_hop,
            "taint_set_size": len(self.tracker.tainted),
            "provenance": [
                {"hop": hop, "entity": entity, "from_tool": tool}
                for hop, entity, tool in self.tracker.provenance[-6:]
            ],
        }


def extract_entities(text: str) -> set[str]:
    """Identifiers worth propagating, extracted from one tool result."""
    entities: set[str] = set()
    for pattern in _ENTITY_PATTERNS:
        for match in pattern.findall(text or ""):
            value = match if isinstance(match, str) else match[0]
            value = value.strip().strip(".,;:'\"")
            if len(value) >= MIN_OVERLAP_LEN and value.lower() not in _STOP_ENTITIES:
                entities.add(value)
    return entities
