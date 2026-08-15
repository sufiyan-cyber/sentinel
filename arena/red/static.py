"""Static prompt-injection attacks, borrowed wholesale from AgentDojo.

We do not write injection strings. AgentDojo ships the canonical templates and
629 injection cases; this module only selects one, points it at a
(user_task, injection_task) pair, and hands back the injected environment.
"""

from __future__ import annotations

import functools
from typing import Any

from agentdojo.attacks.attack_registry import load_attack
from agentdojo.base_tasks import BaseInjectionTask, BaseUserTask
from agentdojo.task_suite.task_suite import TaskSuite

from arena.env.suites import load_suite

#: The four the PRD asks for. AgentDojo registers more; these are the ones
#: milestone 2 reports ASR for.
TEMPLATES: tuple[str, ...] = ("direct", "ignore_previous", "system_message", "important_instructions")


class _ShimPipeline:
    """Just enough of a pipeline for AgentDojo's attacks to name the target.

    `important_instructions` addresses the victim model by name. "local" maps
    to "Local model", which is what our target actually is — a self-hosted
    open-weights model. No pretending to be GPT-4.
    """

    name = "local"

    def query(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - never called
        raise NotImplementedError


class StaticAttacker:
    """Wraps one AgentDojo attack template."""

    def __init__(self, template: str = "important_instructions", suite_name: str = "workspace") -> None:
        if template not in TEMPLATES and template != "manual":
            raise ValueError(f"unknown template {template!r}; known: {TEMPLATES}")
        self.template = template
        self.suite_name = suite_name

    @property
    def suite(self) -> TaskSuite:
        return load_suite(self.suite_name)

    @functools.cached_property
    def _attack(self) -> Any:
        return load_attack(self.template, self.suite, _ShimPipeline())

    def injections(self, user_task_id: str, injection_task_id: str) -> dict[str, str]:
        """`{injection_vector_id: injected_text}` for this pair.

        Only vectors the agent actually reads while solving the user task are
        populated — that is AgentDojo's `get_injection_candidates`, and it is
        why a failed attack means the model resisted rather than never saw it.
        """
        user_task: BaseUserTask = self.suite.user_tasks[user_task_id]
        injection_task: BaseInjectionTask = self.suite.injection_tasks[injection_task_id]
        return self._attack.attack(user_task, injection_task)

    def inject(self, injection_task_id: str, user_task_id: str) -> Any:
        """Return a fresh environment with the injection in place.

        (The PRD writes this as `inject(session_env, injection_task_id)`; the
        environment is rebuilt from the suite rather than mutated, because
        AgentDojo environments are loaded per run.)
        """
        return self.suite.load_and_inject_default_environment(self.injections(user_task_id, injection_task_id))

    def injectable_user_tasks(self) -> list[str]:
        """User tasks that read at least one injection vector."""
        out = []
        for task_id, task in self.suite.user_tasks.items():
            try:
                self._attack.get_injection_candidates(task)
            except ValueError:
                continue
            out.append(task_id)
        return sorted(out, key=_task_sort_key)


def _task_sort_key(task_id: str) -> tuple[int, str]:
    tail = task_id.rsplit("_", 1)[-1]
    return (int(tail), task_id) if tail.isdigit() else (10**6, task_id)
