"""Loading AgentDojo suites, plus the metadata the defense needs from them.

Everything here is derived offline from the suite definitions — ground-truth
tool sets, sensitivity tiers. No LLM is involved, so none of it can appear in
the decision path's latency budget in a way that would be dishonest.
"""

from __future__ import annotations

import functools

from agentdojo.base_tasks import BaseInjectionTask, BaseUserTask
from agentdojo.functions_runtime import Function, make_function
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.task_suite.task_suite import TaskSuite

from arena.config import SUITE_VERSION
from arena.env.tools import send_to_external
from sentinelz.config import TIER_NAMES
from sentinelz.tiers import call_tier, is_external_target, target_tier, tool_tier

__all__ = [
    "DEMO_INJECTION_TASK_ID",
    "all_tool_names",
    "call_tier",
    "expected_tools",
    "extra_tools",
    "ground_truth_pairs",
    "harmful_tools",
    "is_external_target",
    "load_suite",
    "target_tier",
    "task_max_tier",
    "tool_tier",
]

DEMO_INJECTION_TASK_ID = "injection_task_100"
"""The one injection task this project adds. Everything else is AgentDojo's.

It exists because the PRD requires a real `send_to_external` exfiltration path
for the demo. It is excluded from the headline evaluation tables and reported
separately — see `arena/eval/tables.py`.
"""


@functools.lru_cache(maxsize=8)
def load_suite(name: str, with_demo_task: bool = True) -> TaskSuite:
    """Load an AgentDojo suite, optionally with the demo exfiltration task."""
    suite = get_suite(SUITE_VERSION, name)
    if with_demo_task and name == "workspace" and DEMO_INJECTION_TASK_ID not in suite.injection_tasks:
        from arena.env.demo_task import register_demo_injection_task

        register_demo_injection_task(suite)
    return suite


def extra_tools() -> list[Function]:
    """Tools the arena adds to every suite runtime."""
    return [make_function(send_to_external)]


def all_tool_names(suite: TaskSuite) -> set[str]:
    return {t.name for t in suite.tools} | {"send_to_external"}


# --------------------------------------------------------------- ground truth


@functools.lru_cache(maxsize=512)
def _ground_truth_calls(suite_name: str, task_id: str, is_injection: bool) -> tuple[tuple[str, str], ...]:
    """(tool_name, target) pairs of a task's ground truth, computed once.

    Returns a tuple so it can be cached and cannot be mutated by callers.
    """
    suite = load_suite(suite_name)
    task: BaseUserTask | BaseInjectionTask
    if is_injection:
        task = suite.injection_tasks[task_id]
        env = suite.load_and_inject_default_environment({})
    else:
        task = suite.user_tasks[task_id]
        env = task.init_environment(suite.load_and_inject_default_environment({}))
    try:
        calls = task.ground_truth(env)
    except Exception:
        return ()

    from arena.env.brokered_runtime import call_target

    return tuple((c.function, call_target(c.function, dict(c.args))) for c in calls)


def expected_tools(suite_name: str, task_id: str) -> frozenset[str]:
    """Tools a correct solution of this user task actually needs."""
    return frozenset(name for name, _ in _ground_truth_calls(suite_name, task_id, False))


def harmful_tools(suite_name: str, injection_task_id: str) -> frozenset[str]:
    return frozenset(name for name, _ in _ground_truth_calls(suite_name, injection_task_id, True))


def ground_truth_pairs(suite_name: str, task_id: str, is_injection: bool = False) -> list[tuple[str, str]]:
    return [tuple(p) for p in _ground_truth_calls(suite_name, task_id, is_injection)]  # type: ignore[misc]


# ------------------------------------------------------------ sensitivity
# Tier logic lives in sentinelz.tiers: the defense must not depend on the
# arena, and both sides must read the same table.


@functools.lru_cache(maxsize=512)
def task_max_tier(suite_name: str, task_id: str) -> int:
    """Highest tier the user task legitimately requires."""
    pairs = _ground_truth_calls(suite_name, task_id, False)
    if not pairs:
        return TIER_NAMES["internal"]
    return max(call_tier(name, target) for name, target in pairs)
