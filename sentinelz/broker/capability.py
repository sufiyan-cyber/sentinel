"""A simulated capability broker.

This is contribution C2. When the defense blocks, it does not refuse one call
— it revokes the session's token, and every later call of every kind fails
against a dead token. The agent cannot route around it, because the check
happens in the runtime, not in the agent.

The broker is simulated: no cloud, no IAM, no cost. The claim is identical.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

#: Reasons a check can fail. Stable strings — they are logged and displayed.
DENY_UNKNOWN_TOKEN = "unknown_token"
DENY_REVOKED = "revoked"
DENY_OUT_OF_SCOPE = "out_of_scope"
DENY_RESTRICTED_TOOL = "restricted_tool"
DENY_RESTRICTED_TARGET = "restricted_target"


@dataclass
class Capability:
    """One session's authority."""

    session_id: str
    token: str
    scopes: set[str]
    alive: bool = True
    revoked_reason: str = ""
    restrictions: dict[str, Any] = field(default_factory=dict)
    denials: list[dict[str, str]] = field(default_factory=list)


class CapabilityBroker:
    """Issues, checks, narrows and kills session tokens."""

    def __init__(self) -> None:
        self._by_token: dict[str, Capability] = {}
        self._by_session: dict[str, Capability] = {}

    # ------------------------------------------------------------ issue
    def issue(self, session_id: str, scopes: set[str]) -> str:
        """Issue a token for `session_id` covering `scopes` (tool names).

        Re-issuing for a live session returns the existing token. Re-issuing
        for a revoked session is refused — revocation is final for that
        session, which is the whole point.
        """
        existing = self._by_session.get(session_id)
        if existing is not None:
            if not existing.alive:
                raise PermissionError(f"session {session_id} is revoked; refusing to re-issue")
            return existing.token

        token = secrets.token_hex(16)
        capability = Capability(session_id=session_id, token=token, scopes=set(scopes))
        self._by_token[token] = capability
        self._by_session[session_id] = capability
        return token

    # ------------------------------------------------------------ check
    def check(self, token: str, tool_name: str, target: str = "") -> bool:
        """True when this call is permitted. Every denial is recorded."""
        capability = self._by_token.get(token)
        if capability is None:
            return False
        if not capability.alive:
            self._deny(capability, tool_name, target, DENY_REVOKED)
            return False
        if capability.scopes and tool_name not in capability.scopes:
            self._deny(capability, tool_name, target, DENY_OUT_OF_SCOPE)
            return False

        restrictions = capability.restrictions
        if tool_name in set(restrictions.get("deny_tools", [])):
            self._deny(capability, tool_name, target, DENY_RESTRICTED_TOOL)
            return False
        lowered = target.lower()
        for pattern in restrictions.get("deny_targets", []):
            if str(pattern).lower() in lowered:
                self._deny(capability, tool_name, target, DENY_RESTRICTED_TARGET)
                return False
        return True

    def deny_reason(self, token: str, tool_name: str, target: str = "") -> str:
        """Why `check` would fail, without recording a denial."""
        capability = self._by_token.get(token)
        if capability is None:
            return DENY_UNKNOWN_TOKEN
        if not capability.alive:
            return DENY_REVOKED
        if capability.scopes and tool_name not in capability.scopes:
            return DENY_OUT_OF_SCOPE
        if tool_name in set(capability.restrictions.get("deny_tools", [])):
            return DENY_RESTRICTED_TOOL
        lowered = target.lower()
        for pattern in capability.restrictions.get("deny_targets", []):
            if str(pattern).lower() in lowered:
                return DENY_RESTRICTED_TARGET
        return ""

    # ----------------------------------------------------------- revoke
    def revoke(self, session_id: str, reason: str = "policy") -> None:
        """Kill the session's token. Idempotent, and irreversible."""
        capability = self._by_session.get(session_id)
        if capability is None:
            return
        capability.alive = False
        capability.revoked_reason = reason

    def is_alive(self, session_id: str) -> bool:
        capability = self._by_session.get(session_id)
        return bool(capability and capability.alive)

    # ------------------------------------------------------- scope_down
    def scope_down(self, session_id: str, restriction: dict[str, Any]) -> None:
        """Narrow a live token. Restrictions accumulate; they never widen."""
        capability = self._by_session.get(session_id)
        if capability is None:
            return
        current = capability.restrictions
        for key, value in restriction.items():
            if key in {"deny_tools", "deny_targets", "redact_fields"}:
                merged = set(current.get(key, [])) | set(value)
                current[key] = sorted(merged)
            elif key == "max_rows":
                current[key] = min(int(value), int(current.get(key, 10**9)))
            else:
                current[key] = value

    def restrictions(self, session_id: str) -> dict[str, Any]:
        capability = self._by_session.get(session_id)
        return dict(capability.restrictions) if capability else {}

    def token_for(self, session_id: str) -> str | None:
        capability = self._by_session.get(session_id)
        return capability.token if capability else None

    def denials(self, session_id: str) -> list[dict[str, str]]:
        capability = self._by_session.get(session_id)
        return list(capability.denials) if capability else []

    # ------------------------------------------------------------ misc
    def reset(self) -> None:
        self._by_token.clear()
        self._by_session.clear()

    @staticmethod
    def _deny(capability: Capability, tool_name: str, target: str, reason: str) -> None:
        capability.denials.append({"tool": tool_name, "target": target, "reason": reason})


def apply_scope_down(restrictions: dict[str, Any], tool_args: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Return a restricted copy of `tool_args` plus a human-readable note.

    SCOPE_DOWN is the interesting action: the call still happens, so utility
    can survive, but what it can carry is cut down.
    """
    if not restrictions:
        return dict(tool_args), ""

    scoped = dict(tool_args)
    notes: list[str] = []

    redact_fields = set(restrictions.get("redact_fields", []))
    for field_name in sorted(redact_fields):
        if field_name in scoped:
            scoped[field_name] = "[REDACTED]"
            notes.append(f"redacted {field_name}")

    max_rows = restrictions.get("max_rows")
    if max_rows is not None:
        for key, value in list(scoped.items()):
            if isinstance(value, str) and value.count("\n") >= int(max_rows):
                lines = value.splitlines()
                scoped[key] = "\n".join(lines[: int(max_rows)]) + f"\n[{len(lines) - int(max_rows)} rows withheld]"
                notes.append(f"capped {key} to {max_rows} rows")
            elif isinstance(value, list) and len(value) > int(max_rows):
                scoped[key] = value[: int(max_rows)]
                notes.append(f"capped {key} to {max_rows} items")

    return scoped, "; ".join(notes)
