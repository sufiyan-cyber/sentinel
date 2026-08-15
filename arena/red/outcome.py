"""Ground truth for "did the attack succeed?".

AgentDojo's security checker is the authority. We never decide this ourselves
— a defense that grades its own homework proves nothing.
"""

from __future__ import annotations

from arena.agent.session import Session


def did_attack_succeed(session: Session, injection_task_id: str | None = None) -> bool:
    """Whether the injection task was accomplished during `session`.

    The verdict is computed by AgentDojo at the end of the run and stored on
    the session; this function is the named accessor the PRD asks for, and it
    refuses to guess when no injection was configured.
    """
    if session.injection_task_id is None:
        return False
    if injection_task_id is not None and injection_task_id != session.injection_task_id:
        raise ValueError(
            f"session ran injection {session.injection_task_id!r}, asked about {injection_task_id!r}"
        )
    return session.attack_succeeded


def attack_success_rate(sessions: list[Session]) -> float:
    attacked = [s for s in sessions if s.injection_task_id is not None]
    if not attacked:
        return 0.0
    return sum(1 for s in attacked if s.attack_succeeded) / len(attacked)


def utility_rate(sessions: list[Session]) -> float:
    if not sessions:
        return 0.0
    return sum(1.0 for s in sessions if s.completed) / len(sessions)
