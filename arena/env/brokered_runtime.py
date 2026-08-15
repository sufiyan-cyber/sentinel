"""A `FunctionsRuntime` that will not run anything the broker refuses.

This is where capability revocation becomes real. The check sits below the
agent: a revoked token blocks every subsequent call, of every kind, whether
or not the agent decides to stop. AgentDojo takes the runtime class as a
parameter, so no fork is needed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from agentdojo.functions_runtime import Function, FunctionReturnType, FunctionsRuntime, TaskEnvironment

from arena.env import egress
from sentinelz.broker.capability import CapabilityBroker, apply_scope_down


def call_target(tool_name: str, tool_args: Mapping[str, Any]) -> str:
    """A single string describing what this call reaches.

    Used for broker target matching and for the privilege signal. Recipients
    first, then URLs and identifiers, then everything else.
    """
    parts: list[str] = []
    for address in egress.recipients(dict(tool_args)):
        parts.append(address)
    for key in ("url", "file_id", "email_id", "recipient", "iban", "filename", "query"):
        value = tool_args.get(key)
        if value is not None:
            parts.append(str(value))
    if not parts:
        parts = [str(v) for v in tool_args.values()][:3]
    return " ".join(parts)[:400]


def make_brokered_runtime(
    broker: CapabilityBroker,
    session_id: str,
    on_call: Callable[[str, dict[str, Any], Any, str | None], None] | None = None,
    extra_functions: Sequence[Function] = (),
) -> type[FunctionsRuntime]:
    """Build a runtime class bound to one broker and one session.

    `on_call(tool_name, args, result, error)` is invoked after every attempt,
    including refused ones, so the arena can record what happened.
    """

    class BrokeredFunctionsRuntime(FunctionsRuntime):
        def __init__(self, functions: Sequence[Function] = ()) -> None:
            super().__init__([*functions, *extra_functions])

        def run_function(
            self,
            env: TaskEnvironment | None,
            function: str,
            kwargs: Mapping[str, Any],
            raise_on_error: bool = False,
        ) -> tuple[FunctionReturnType, str | None]:
            token = broker.token_for(session_id)
            if token is None:
                # No capability was ever issued for this session: the defense
                # is off. Run unmodified.
                return super().run_function(env, function, kwargs, raise_on_error)

            target = call_target(function, kwargs)
            if not broker.check(token, function, target):
                reason = broker.deny_reason(token, function, target)
                message = (
                    f"CapabilityError: the session's capability token has been revoked "
                    f"({reason}); no further tool calls will be authorised."
                    if reason == "revoked"
                    else f"CapabilityError: call to `{function}` refused by the capability broker ({reason})."
                )
                if on_call is not None:
                    on_call(function, dict(kwargs), None, message)
                if raise_on_error:
                    raise PermissionError(message)
                return "", message

            scoped_kwargs, note = apply_scope_down(broker.restrictions(session_id), dict(kwargs))
            result, error = super().run_function(env, function, scoped_kwargs, raise_on_error)
            if note and isinstance(result, str):
                result = f"{result}\n[scope_down: {note}]"
            if on_call is not None:
                on_call(function, scoped_kwargs, result, error)
            return result, error

    return BrokeredFunctionsRuntime
