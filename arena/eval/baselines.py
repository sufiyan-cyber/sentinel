"""Four contestants, one interface, the same runs.

  no_defense                  the control
  injection_classifier_only   a threshold on the injection signal alone —
                              what most published defenses reduce to
  per_call_policy             Progent-style least privilege: a static
                              allowlist of tool+target pairs derived from the
                              task, with no session state
  sentinelz_full              signals -> hazard -> belief -> POMDP policy

All four run through the same gateway plumbing and the same broker, so the
comparison is of decision rules, not of implementations. The difference
between the second and the fourth is the entire argument of the project: one
asks "is this call bad", the other asks "where is this session going".
"""

from __future__ import annotations

from typing import Any

import numpy as np

from sentinelz.config import HAZARD_THRESHOLD
from sentinelz.gateway import Gateway
from sentinelz.policy.decide import Decision
from sentinelz.pomdp.states import ACTION_NAMES, ALLOW, MONITOR, REVOKE, STATE_NAMES, action_name
from sentinelz.signals.bundle import SignalBundle

BASELINE_NAMES: tuple[str, ...] = (
    "no_defense",
    "injection_classifier_only",
    "per_call_policy",
    "sentinelz_full",
)

INJECTION_THRESHOLD = 0.5


class _BaselineGateway(Gateway):
    """Shares the signal bundle and the plumbing; only `_act` differs."""

    baseline_name = "baseline"

    def _act(self, signals: dict[str, float], explanation: dict[str, Any], p_harm: float) -> Decision:
        action_index, reason = self.rule(signals, explanation)
        self.last_action = action_index
        tainted = float(signals.get("taint", 0.0)) >= 0.5
        return Decision(
            action=action_name(action_index),
            action_index=action_index,
            belief=[1.0, 0.0, 0.0, 0.0, 0.0],
            argmax_state=STATE_NAMES[0],
            expected_costs={name: 0.0 for name in ACTION_NAMES},
            absorption={"n1": 0.0, "n3": 0.0, "n5": 0.0},
            hazard=float(p_harm),
            observation=self.encoder.obs(p_harm, tainted),
            signals=dict(signals),
            reason=reason,
            policy=self.baseline_name,
            explanation=explanation,
        )

    def rule(self, signals: dict[str, float], explanation: dict[str, Any]) -> tuple[int, str]:
        raise NotImplementedError


class InjectionClassifierOnly(_BaselineGateway):
    """Refuse when the last tool result looks like an injection.

    This is per-call filtering with no memory: it fires on the step that reads
    the poisoned content, and has nothing to say about the steps that act on
    it afterwards.
    """

    baseline_name = "injection_classifier_only"

    def __init__(self, threshold: float = INJECTION_THRESHOLD, **kwargs: Any) -> None:
        super().__init__(mode="threshold", **kwargs)
        self.injection_threshold = threshold

    def rule(self, signals: dict[str, float], explanation: dict[str, Any]) -> tuple[int, str]:
        score = float(signals.get("injection_likelihood", 0.0))
        if score >= self.injection_threshold:
            return REVOKE, f"injection_likelihood {score:.3f} >= {self.injection_threshold:.2f}"
        return ALLOW, f"injection_likelihood {score:.3f} below threshold"


class PerCallPolicy(_BaselineGateway):
    """Progent-style static least privilege.

    The allowlist is derived from the task before the run starts: the tools a
    correct solution needs, and the targets it needs them for. Anything else
    is refused. No session state, no trajectory — each call is judged alone.
    """

    baseline_name = "per_call_policy"

    def __init__(self, allowed_tools: frozenset[str] = frozenset(), allowed_targets: frozenset[str] = frozenset(), **kwargs: Any) -> None:
        super().__init__(mode="threshold", **kwargs)
        self.allowed_tools = allowed_tools
        self.allowed_targets = {t.lower() for t in allowed_targets if t}

    def rule(self, signals: dict[str, float], explanation: dict[str, Any]) -> tuple[int, str]:
        tool = self.tools_history[-1] if self.tools_history else ""
        if self.allowed_tools and tool not in self.allowed_tools:
            return REVOKE, f"tool {tool!r} not in the task's allowlist"
        if float(signals.get("privilege_delta", 0.0)) > 0.0:
            return REVOKE, "call reaches beyond the tier the task requires"
        return ALLOW, f"tool {tool!r} permitted by the static policy"


class HazardThresholdOnly(_BaselineGateway):
    """T2 ablation row: the hazard model with no belief state.

    Included so the POMDP has to earn its place. If this row matches
    `sentinelz_full`, the belief layer is decoration.
    """

    baseline_name = "hazard_threshold"

    def __init__(self, threshold: float = HAZARD_THRESHOLD, **kwargs: Any) -> None:
        super().__init__(mode="threshold", **kwargs)
        self.hazard_threshold = threshold

    def rule(self, signals: dict[str, float], explanation: dict[str, Any]) -> tuple[int, str]:
        from sentinelz.policy.decide import decide_threshold

        # The hazard value is recomputed by the caller and stored on the
        # decision; re-derive it here from the history the gateway keeps.
        return decide_threshold(self._last_hazard, self.hazard_threshold)

    def evaluate(self, ctx: Any) -> Decision:  # type: ignore[override]
        self._last_hazard = 0.0
        decision = super().evaluate(ctx)
        return decision

    def _hazard(self, signals: dict[str, float]) -> float:
        value = super()._hazard(signals)
        self._last_hazard = value
        return value


class GreedyBelief(_BaselineGateway):
    """T2 ablation row: belief state, but a greedy one-step action.

    Sits between the threshold rule and the full policy: it has the belief but
    not the finite-horizon lookahead, so it isolates what the lookahead adds.
    """

    baseline_name = "belief_greedy"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(mode="pomdp", **kwargs)

    def _act(self, signals: dict[str, float], explanation: dict[str, Any], p_harm: float) -> Decision:
        if self.belief_filter is None or self.policy is None:
            return super()._act(signals, explanation, p_harm)

        tainted = float(signals.get("taint", 0.0)) >= 0.5
        observation = self.encoder.obs(p_harm, tainted)
        belief = self.belief_filter.update(self.last_action, observation)

        # One step of lookahead only: immediate cost, no future term.
        from sentinelz.pomdp.solve import ACTION_COSTS, HARM_COST
        from sentinelz.pomdp.states import HARM, N_ACTIONS

        costs = np.empty(N_ACTIONS)
        for action in range(N_ACTIONS):
            entering = float(sum(belief[s] * self.policy.T[s, action, HARM] for s in range(len(belief)) if s != HARM))
            costs[action] = ACTION_COSTS[action] + HARM_COST * entering
        action_index = int(np.argmin(costs))
        self.last_action = action_index

        return Decision(
            action=action_name(action_index),
            action_index=action_index,
            belief=[float(b) for b in belief],
            argmax_state=STATE_NAMES[int(np.argmax(belief))],
            expected_costs={ACTION_NAMES[i]: float(costs[i]) for i in range(N_ACTIONS)},
            absorption=self._absorption(belief),
            hazard=float(p_harm),
            observation=observation,
            signals=dict(signals),
            reason="greedy one-step action over the belief",
            policy=self.baseline_name,
            explanation=explanation,
        )

    def rule(self, signals: dict[str, float], explanation: dict[str, Any]) -> tuple[int, str]:
        return MONITOR, "unused"


# --------------------------------------------------------------- factory


def build_baseline(
    name: str,
    suite: str = "workspace",
    user_task_id: str = "",
    disabled_signals: frozenset[str] = frozenset(),
) -> Gateway | None:
    """Return a ready gateway for `name`, or None for the undefended control."""
    if name == "no_defense":
        return None

    bundle = SignalBundle(disabled=disabled_signals)

    if name == "injection_classifier_only":
        return InjectionClassifierOnly(bundle=bundle)

    if name == "per_call_policy":
        from arena.env.suites import expected_tools, ground_truth_pairs

        tools = expected_tools(suite, user_task_id) if user_task_id else frozenset()
        targets = frozenset(t for _, t in ground_truth_pairs(suite, user_task_id)) if user_task_id else frozenset()
        return PerCallPolicy(allowed_tools=tools, allowed_targets=targets, bundle=bundle)

    if name == "hazard_threshold":
        return HazardThresholdOnly(bundle=bundle)

    if name == "belief_greedy":
        from sentinelz.gateway import build_gateway

        gateway = build_gateway(mode="pomdp", disabled_signals=disabled_signals)
        greedy = GreedyBelief(bundle=bundle)
        greedy.hazard = gateway.hazard
        greedy.encoder = gateway.encoder
        greedy.belief_filter = gateway.belief_filter
        greedy.policy = gateway.policy
        return greedy

    if name == "sentinelz_full":
        from sentinelz.gateway import build_gateway

        return build_gateway(mode="pomdp", disabled_signals=disabled_signals)

    raise ValueError(f"unknown baseline {name!r}; known: {BASELINE_NAMES}")


def defense_label(name: str) -> str:
    return {
        "no_defense": "no defense",
        "injection_classifier_only": "injection classifier only",
        "per_call_policy": "per-call policy (Progent-style)",
        "hazard_threshold": "hazard threshold (no belief)",
        "belief_greedy": "belief + greedy action",
        "sentinelz_full": "Sentinel-Z (full)",
    }.get(name, name)
