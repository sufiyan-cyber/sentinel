"""Sentinel-Z as an AgentDojo pipeline element.

Sits between the model and the tool executor, so it sees every pending call
before it runs. It does not stop the call itself — it acts on the broker, and
the broker stops the call. That distinction is contribution C2: the agent
cannot route around a dead token, because refusal happens below it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.types import ChatMessage, get_text_content_as_str

from arena.agent.session import Session
from arena.env.brokered_runtime import call_target
from arena.env.suites import expected_tools, task_max_tier
from sentinelz.broker.capability import CapabilityBroker
from sentinelz.context import DecisionContext, ObservedStep, PendingCall
from sentinelz.gateway import Gateway
from sentinelz.pomdp.states import ALLOW, MONITOR, REVOKE, SCOPE_DOWN, STEP_UP

#: SCOPE_DOWN's concrete effect. Narrow enough to break an exfiltration,
#: loose enough that a legitimate call still returns something useful.
SCOPE_DOWN_RESTRICTION: dict[str, Any] = {
    "max_rows": 3,
    "redact_fields": ["body", "data", "content"],
    "deny_targets": [],
}


class SentinelElement(BasePipelineElement):
    """Runs the gateway on each pending tool call and applies the result."""

    def __init__(
        self,
        gateway: Gateway,
        broker: CapabilityBroker,
        session_id: str,
        suite_name: str,
        user_task_id: str,
        session: Session,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        step_up_policy: str = "simulated_user",
    ) -> None:
        self.gateway = gateway
        self.broker = broker
        self.session_id = session_id
        self.suite_name = suite_name
        self.user_task_id = user_task_id
        self.session = session
        self.on_event = on_event
        self.step_up_policy = step_up_policy

        self._pending: dict[str, Any] | None = None
        self.gateway.start_session()

        self._expected = expected_tools(suite_name, user_task_id)
        self._task_tier = task_max_tier(suite_name, user_task_id)

    def take_pending(self) -> dict[str, Any] | None:
        """Hand the decision to the step recorder, once."""
        pending, self._pending = self._pending, None
        return pending

    # ------------------------------------------------------------ query
    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        if not messages or messages[-1].get("role") != "assistant":
            return query, runtime, env, messages, extra_args
        tool_calls = messages[-1].get("tool_calls")
        if not tool_calls:
            return query, runtime, env, messages, extra_args

        # Taint absorption happens inside Gateway.evaluate, which must score
        # the previous result before judging this call. Doing it here would
        # put it one step behind.
        history = _history_from_messages(messages)
        for tool_call in tool_calls:
            pending = PendingCall(tool_name=tool_call.function, tool_args=dict(tool_call.args))

            # Once the token is dead the decision has already been made and the
            # broker is enforcing it, so re-running the policy here would be
            # both wasted budget and actively misleading: the belief is now
            # CONTAINED, where the cheapest action is correctly ALLOW, and the
            # transcript would show ALLOW beside a call that was refused.
            if not self.broker.is_alive(self.session_id):
                self._record(self.gateway.standing_revocation(), pending, "REVOKE: token already destroyed")
                continue
            ctx = DecisionContext(
                user_task_text=query,
                pending=pending,
                history=history,
                taint_set=set(self.gateway.bundle.tracker.tainted),
                expected_tools=self._expected,
                task_max_tier=self._task_tier,
                suite=self.suite_name,
                step_index=len(history),
            )
            decision = self.gateway.evaluate(ctx)
            self._record(decision, pending, self._apply(decision, pending))

        return query, runtime, env, messages, extra_args

    def _record(self, decision: Any, pending: PendingCall, note: str) -> None:
        """Hand the decision to the step recorder and to any live UI."""
        self._pending = {
            "decision": decision.to_record() | {"timings_ms": decision.timings_ms},
            "signals": decision.signals,
            "note": note,
        }
        if self.on_event is not None:
            self.on_event(
                "decision",
                {
                    "tool": pending.tool_name,
                    "decision": decision.to_record(),
                    "timings_ms": decision.timings_ms,
                    "explanation": decision.explanation,
                },
            )

    # ------------------------------------------------------------ apply
    def _apply(self, decision: Any, pending: PendingCall) -> str:
        """Turn the chosen action into broker state. Returns a note."""
        action = decision.action_index

        if action in (ALLOW, MONITOR):
            return "" if action == ALLOW else f"MONITOR: {decision.reason}"

        if action == SCOPE_DOWN:
            self.broker.scope_down(self.session_id, dict(SCOPE_DOWN_RESTRICTION))
            return f"SCOPE_DOWN: {decision.reason}"

        if action == STEP_UP:
            approved = self._simulated_user_approves(pending)
            if approved:
                return f"STEP_UP: approved by user ({decision.reason})"
            target = call_target(pending.tool_name, pending.tool_args)
            self.broker.scope_down(self.session_id, {"deny_targets": [target[:120]] if target else [], "deny_tools": [pending.tool_name]})
            return f"STEP_UP: refused by user ({decision.reason})"

        if action == REVOKE:
            self.broker.revoke(self.session_id, reason=decision.reason or "policy")
            if self.session.revoked_at_step is None:
                self.session.revoked_at_step = len(self.session.steps)
            return f"REVOKE: {decision.reason}"

        return ""

    def _simulated_user_approves(self, pending: PendingCall) -> bool:
        """STEP_UP pauses for a human. In batch evaluation there is no human,
        so we model one: a user approves a call they recognise as part of the
        task they asked for, and refuses one they do not.

        This is a modelling assumption and it is stated in the results.
        """
        if self.step_up_policy == "auto_allow":
            return True
        if self.step_up_policy == "auto_deny":
            return False
        return pending.tool_name in self._expected

def _history_from_messages(messages: Sequence[ChatMessage]) -> list[ObservedStep]:
    """Reconstruct the observed history the defense is allowed to see."""
    history: list[ObservedStep] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        text = get_text_content_as_str(content) if content is not None else ""
        tool_call = message.get("tool_call")
        history.append(
            ObservedStep(
                tool_name=getattr(tool_call, "function", "") if tool_call is not None else "",
                tool_args=dict(getattr(tool_call, "args", {}) or {}) if tool_call is not None else {},
                result_text=text,
                result_is_untrusted=True,
            )
        )
    return history
