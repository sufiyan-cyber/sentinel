"""Signal 3 — privilege_delta.

`sensitivity_tier(target) - max_tier_required_by(task)`, clipped to [0,1].

Zero when the call stays inside what the task legitimately needs. It climbs
when the agent reaches for something more sensitive than the job requires —
which is what "reaching beyond the task" looks like mechanically.
"""

from __future__ import annotations

from typing import Any

from sentinelz.config import MAX_TIER
from sentinelz.context import DecisionContext
from sentinelz.signals.base import clip01
from sentinelz.tiers import call_tier, is_egress_tool, is_external_target, target_tier, tool_tier


class PrivilegeSignal:
    """privilege_delta, in [0,1]."""

    name = "privilege_delta"

    def score(self, ctx: DecisionContext) -> tuple[float, dict[str, Any]]:
        target_text = " ".join(ctx.pending.arg_values())
        reached = call_tier(ctx.pending.tool_name, target_text)
        required = int(ctx.task_max_tier)
        delta = reached - required
        value = clip01(delta / float(MAX_TIER))

        return value, {
            "tool": ctx.pending.tool_name,
            "tool_tier": tool_tier(ctx.pending.tool_name),
            "target_tier": target_tier(target_text),
            "reached_tier": reached,
            "task_required_tier": required,
            "delta": delta,
            "egress": is_egress_tool(ctx.pending.tool_name),
            "external_destination": is_external_target(target_text),
        }
