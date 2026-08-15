"""Sensitivity tiers, read once from `configs/sensitivity.json`.

Lives in `sentinelz` rather than `arena` because the defense must not depend
on the harness. The arena imports these; nothing here imports the arena.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sentinelz.config import CONFIGS_DIR, MAX_TIER, TIER_NAMES
from sentinelz.evidence.canonical import loads

_CFG: dict[str, Any] = loads(Path(CONFIGS_DIR / "sensitivity.json").read_text(encoding="utf-8"))

_TOOL_TIER: dict[str, int] = {k: int(v) for k, v in _CFG["tool_tier"].items()}
_DEFAULT_TIER: int = int(_CFG["default_tool_tier"])
_TARGET_TAGS: dict[str, list[str]] = _CFG["target_tags"]
_EGRESS_TOOLS: frozenset[str] = frozenset(_CFG["egress_tools"])
_INTERNAL_DOMAINS: frozenset[str] = frozenset(d.lower() for d in _CFG["internal_domains"])
_EXTERNAL_IS_RESTRICTED: bool = bool(_CFG.get("external_domains_are_restricted", True))

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def tool_tier(tool_name: str) -> int:
    return _TOOL_TIER.get(tool_name, _DEFAULT_TIER)


def target_tier(text: str) -> int:
    """Highest tier implied by anything mentioned in `text`."""
    lowered = text.lower()
    tier = 0
    for tier_name, patterns in _TARGET_TAGS.items():
        candidate = TIER_NAMES[tier_name]
        if candidate > tier and any(p in lowered for p in patterns):
            tier = candidate
    return tier


def is_external_target(text: str) -> bool:
    if not _EXTERNAL_IS_RESTRICTED:
        return False
    addresses = EMAIL_RE.findall(text)
    return any(addr.rsplit("@", 1)[-1].lower() not in _INTERNAL_DOMAINS for addr in addresses)


def is_egress_tool(tool_name: str) -> bool:
    return tool_name in _EGRESS_TOOLS


def call_tier(tool_name: str, target_text: str) -> int:
    """Tier this call reaches: the tool's own, raised by what it touches and
    by whether the destination is outside the organisation.

    Naming an outside address raises the tier for ANY tool, not only the
    obvious egress ones. Adding an external participant to a calendar event
    is a reach beyond the organisation just as much as emailing them is, and
    that is exactly the shape several AgentDojo injection tasks take.
    """
    tier = max(tool_tier(tool_name), target_tier(target_text))
    external = is_external_target(target_text)
    if external:
        tier = max(tier, TIER_NAMES["confidential"])
    if tool_name in _EGRESS_TOOLS:
        tier = max(tier, TIER_NAMES["confidential"])
        if external:
            tier = MAX_TIER
    return min(tier, MAX_TIER)
