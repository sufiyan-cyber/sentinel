"""Label correctness, checked against the collected corpus.

Every number the project reports is downstream of these labels, and the two
failures they guard against are both silent: the pipeline runs, the tables
render, the figures look plausible, and the defense has learned the wrong
thing. They are worth a slow test.

Skips when `runs/` is empty — a fresh clone has no corpus until `sz collect`.
"""

from __future__ import annotations

import pytest

from arena.agent.runner import load_labelled_sessions
from sentinelz.hazard.label import (
    hazard_labels,
    state_labels,
    terminal_state,
    training_rows,
)
from sentinelz.pomdp.states import (
    BENIGN,
    CONTAINED,
    ESCALATION,
    HARM,
    N_STATES,
    RECON,
    STATE_NAMES,
)


@pytest.fixture(scope="module")
def corpus():
    """The trainable corpus: sessions the defense actually watched.

    Undefended runs — both gates, the control arm of `run-arena` — land in the
    same directory and carry no signals at all, so every claim here about what
    the defense could see is vacuous for them.
    """
    sessions = load_labelled_sessions()
    if len(sessions) < 50:
        pytest.skip(f"only {len(sessions)} labelled sessions; run `sz collect` first")
    return sessions


@pytest.fixture(scope="module")
def transitions(corpus):
    """Observed (state -> next_state) counts over the whole corpus."""
    counts = [[0 for _ in range(N_STATES)] for _ in range(N_STATES)]
    for session in corpus:
        history = [step.signals or {} for step in session.steps]
        if not history:
            continue
        states = state_labels(history, session.harm_step, session.revoked_at_step)
        for current, following in zip(states, states[1:], strict=False):
            counts[current][following] += 1
        final = terminal_state(states, session.harm_step, session.revoked_at_step)
        if final is not None:
            counts[states[-1]][final] += 1
    return counts


def _p_harm(counts, state: int) -> float:
    total = sum(counts[state])
    return counts[state][HARM] / total if total else 0.0


# ------------------------------------------------------- the risk ladder


def test_risk_ladder_is_monotone(transitions):
    """P(harm next step) must rise along BENIGN < RECON < ESCALATION.

    If it does not, the belief filter reads escalation as reassurance and the
    solved policy is worse than no policy. The specific way this broke before:
    ESCALATION did not require taint, so it filled up with the agent's harmless
    out-of-scope wandering.
    """
    benign, recon, escalation = (_p_harm(transitions, s) for s in (BENIGN, RECON, ESCALATION))
    ladder = f"BENIGN={benign:.3f} RECON={recon:.3f} ESCALATION={escalation:.3f}"
    assert benign <= recon <= escalation, f"risk ladder is not monotone: {ladder}"
    assert escalation > benign, f"escalation carries no more risk than benign: {ladder}"


def test_escalation_to_harm_is_actually_observed(transitions):
    """The one transition the policy exists to learn.

    Zero here means the harmful call is being labelled HARM at its own decision
    point rather than ESCALATION, so the transition into HARM never completes
    and the solver concludes that allowing an escalated call is free.
    """
    assert transitions[ESCALATION][HARM] > 0, (
        "no ESCALATION -> HARM transition in the corpus: the harmful step is "
        "mislabelled, or `terminal_state` is not being applied"
    )


def test_benign_never_jumps_straight_to_harm(transitions):
    """Harm is reached through escalation, not from a clean state in one step."""
    assert transitions[BENIGN][HARM] == 0, (
        f"{transitions[BENIGN][HARM]} sessions go BENIGN -> HARM with no state in "
        "between; harm_step is probably matching the wrong call"
    )


# ------------------------------------------------- decision-point semantics


def test_the_harmful_step_is_labelled_escalation_not_harm(corpus):
    """`state[t]` is the state when deciding about call `t`, before it runs."""
    checked = 0
    for session in corpus:
        if session.harm_step is None:
            continue
        history = [step.signals or {} for step in session.steps]
        states = state_labels(history, session.harm_step, session.revoked_at_step)
        assert states[session.harm_step] == ESCALATION, (
            f"{session.task_id}/{session.injection_task_id}: harmful step labelled "
            f"{STATE_NAMES[states[session.harm_step]]}, expected ESCALATION"
        )
        checked += 1
    assert checked > 0, "no session in the corpus reaches harm"


def test_harm_is_absorbing_after_the_harmful_call(corpus):
    for session in corpus:
        if session.harm_step is None or session.revoked_at_step is not None:
            continue
        history = [step.signals or {} for step in session.steps]
        states = state_labels(history, session.harm_step, session.revoked_at_step)
        for t in range(session.harm_step + 1, len(states)):
            assert states[t] == HARM, f"state left HARM at step {t}"


def test_terminal_state_supplies_the_missing_transition(corpus):
    """Sessions ending on the harmful call must still record entering HARM."""
    ended_on_harm = [
        s for s in corpus if s.harm_step is not None and s.harm_step == len(s.steps) - 1
    ]
    if not ended_on_harm:
        pytest.skip("no session in this corpus ends on the harmful call")
    for session in ended_on_harm:
        history = [step.signals or {} for step in session.steps]
        states = state_labels(history, session.harm_step, session.revoked_at_step)
        assert terminal_state(states, session.harm_step, session.revoked_at_step) == HARM


def test_censored_sessions_get_no_invented_transition(corpus):
    """A session that merely stopped is censored, not absorbed."""
    for session in corpus:
        if session.harm_step is not None or session.revoked_at_step is not None:
            continue
        history = [step.signals or {} for step in session.steps]
        states = state_labels(history, session.harm_step, session.revoked_at_step)
        assert terminal_state(states, session.harm_step, session.revoked_at_step) is None


def test_revocation_contains_from_the_revoked_step_onward(corpus):
    revoked = [s for s in corpus if s.revoked_at_step is not None]
    if not revoked:
        pytest.skip("no revocation in this corpus")
    for session in revoked:
        history = [step.signals or {} for step in session.steps]
        states = state_labels(history, session.harm_step, session.revoked_at_step)
        for t in range(session.revoked_at_step, len(states)):
            assert states[t] == CONTAINED


# ------------------------------------------------------- hazard labelling


def test_hazard_window_includes_the_pending_call():
    """The decision at step t is made before call t runs, so call t is inside
    the horizon that decision is predicting over."""
    assert hazard_labels(5, harm_step=2, k=1) == [0, 0, 1, 0, 0]
    assert hazard_labels(5, harm_step=2, k=3) == [1, 1, 1, 0, 0]


def test_training_rows_drop_the_tail_after_harm():
    """Standard survival rule. Keeping post-harm steps — all tainted, all
    labelled 0 — is what drove taint's coefficient negative."""
    rows = training_rows(6, harm_step=2, k=1)
    assert [t for t, _ in rows] == [0, 1, 2]
    assert [label for _, label in rows] == [0, 0, 1]


def test_sessions_without_harm_contribute_every_step_as_negative():
    rows = training_rows(4, harm_step=None, k=3)
    assert [t for t, _ in rows] == [0, 1, 2, 3]
    assert all(label == 0 for _, label in rows)


# ------------------------------------------------------- corpus coverage


def test_taint_fires_no_later_than_the_harmful_call(corpus):
    """The defense cannot act on evidence it never receives.

    This does not require advance warning — on the scripted backend there is
    none, because the poisoned read lands in the result immediately before the
    harmful call. It requires that the signal has arrived by the time the
    gateway decides about the harmful call itself, which is the last moment
    blocking it is still possible.
    """
    late = []
    for session in corpus:
        if session.harm_step is None:
            continue
        signals = session.steps[session.harm_step].signals or {}
        if float(signals.get("taint", 0.0)) < 0.5:
            late.append(f"{session.task_id}/{session.injection_task_id}")
    assert len(late) <= len([s for s in corpus if s.harm_step is not None]) * 0.1, (
        f"taint had not fired at the harmful call in {len(late)} sessions: {late[:5]}"
    )
