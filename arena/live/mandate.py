"""What the user authorised the agent to spend — parsed from their own words.

A pure function, no model. The result becomes a spend limit on the session's
capability token, so it is enforced by the broker underneath the agent rather
than by the agent remembering the budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from arena import config

_NUM = r"(?P<amount>\d[\d,]*(?:\.\d{1,2})?)\s*(?P<k>k\b)?"
_CUR = r"(?:₹|rs\.?|inr|rupees?)"
_PATTERNS = (
    re.compile(rf"{_CUR}\s*{_NUM}", re.I),
    re.compile(rf"{_NUM}\s*{_CUR}", re.I),
    re.compile(
        rf"(?:under|below|within|max(?:imum)?|budget(?:\s+of|\s+is)?|upto|up\s+to|around|about|for|at)\s+{_NUM}\b",
        re.I,
    ),
)


@dataclass(frozen=True)
class SpendMandate:
    max_paise: int
    stated_paise: int
    source: str  # "user" | "default"
    text: str

    def payload(self) -> dict[str, object]:
        return {
            "max_paise": self.max_paise,
            "stated_paise": self.stated_paise,
            "source": self.source,
            "text": self.text,
        }


def parse_mandate(user_text: str) -> SpendMandate:
    """The spend cap implied by `user_text`, or the configured default."""
    for pattern in _PATTERNS:
        match = pattern.search(user_text)
        if match is None:
            continue
        value = float(match.group("amount").replace(",", ""))
        if match.group("k"):
            value *= 1000
        if value <= 0:
            continue
        stated = int(round(value * 100))
        cap = stated * (100 + config.MANDATE_TOLERANCE_PCT) // 100
        return SpendMandate(cap, stated, "user", match.group(0).strip())
    default = config.DEFAULT_SPEND_CAP_INR * 100
    return SpendMandate(default, default, "default", "")
