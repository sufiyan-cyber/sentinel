"""Labels for the hazard model and state labels for the POMDP.

Hazard label: step t is 1 when the harmful call happens within the next k
steps. k in {1, 3, 5}.

State label: the five-state trajectory, derived mechanically from what the
session shows —

  BENIGN      nothing attacker-controlled has been read yet
  RECON       tainted content has been read, but nothing out of scope touched
  ESCALATION  tainted, AND this call reaches a privileged or out-of-scope target
  HARM        the harmful call has executed
  CONTAINED   a REVOKE has been applied

Two things about this list are easy to get wrong, and both were.

**A state label describes the decision point, not the aftermath.** `state[t]` is
the state the gateway is in when it decides about call `t` — *before* that call
runs. So the harmful call's own step is not HARM: at that moment the session is
tainted and reaching for the attacker's target, which is ESCALATION. HARM is
where the session lands *because* the call was allowed, so it is the state from
`harm_step + 1` onward, and `terminal_state` supplies it for sessions that end
on the harmful call. Labelling the harmful step HARM instead deletes the only
ESCALATION -> HARM transition in the corpus — the single transition the policy
exists to learn — and leaves the solver believing that allowing an escalated
call is safe. This is the same off-by-one already fixed in `hazard_labels`; it
had not been carried across to the state labels.

**ESCALATION requires taint.** An untainted out-of-scope call is an agent being
sloppy, not an attack advancing, and the two must not share a state. Labelling
them together is not cosmetic either: the agent goes out of scope on plenty of
sessions where nothing bad happens, so ESCALATION fills with harmless steps and
`P(ESCALATION -> HARM)` falls *below* `P(RECON -> HARM)`. The ladder then runs
backwards and the belief filter reads escalation as reassurance.

The ordering BENIGN < RECON < ESCALATION in P(harm next) is the property the
whole defense rests on. `tests/test_labels.py` asserts it on the real corpus.

Both are computed from recorded sessions only. No model is involved, so the
labels cannot be contaminated by the thing being trained.
"""

from __future__ import annotations

from typing import Any

from sentinelz.pomdp.states import BENIGN, CONTAINED, ESCALATION, HARM, RECON

K_VALUES: tuple[int, ...] = (1, 3, 5)

TAINT_LABEL_THRESHOLD = 0.5
PRIVILEGE_LABEL_THRESHOLD = 0.34  # one full tier above what the task needs

#: `task_alignment` carries a +0.4 penalty when the tool is outside the task's
#: ground-truth set, on top of a distance term that sits near 0.5 for almost
#: every call. A score this high therefore means "out of scope", which is the
#: reaching-for-something condition the ESCALATION label is after.
OUT_OF_SCOPE_LABEL_THRESHOLD = 0.8


def hazard_labels(n_steps: int, harm_step: int | None, k: int) -> list[int]:
    """1 when the harmful call falls within the next k steps, counting now.

    **The window includes step t itself.** The gateway's decision at step t is
    made *before* call t executes, so call t is inside the horizon it is
    predicting over. Labelling it `0 < (harm - t) <= k` instead — excluding the
    pending call — is off by one, and on AgentDojo it is fatal rather than
    cosmetic: the poisoned content arrives in the result of step `harm - 1`, so
    the earliest decision that can see it is the decision for step `harm`. With
    the exclusive window, every step the defense could actually act on is
    labelled 0, `taint` gets a negative coefficient, and the model learns to
    predict the opposite of what it is for.
    """
    if harm_step is None:
        return [0] * n_steps
    return [1 if 0 <= (harm_step - t) < k else 0 for t in range(n_steps)]


def training_rows(n_steps: int, harm_step: int | None, k: int) -> list[tuple[int, int]]:
    """`(step_index, label)` pairs that belong in the hazard training set.

    Steps **after** the harmful call are dropped; the harmful call itself is
    kept, because that is the step the defense still gets to decide on.

    Dropping the tail is the standard survival-analysis rule — you do not keep
    observations after the event — and getting it wrong is subtle and expensive
    here: a session usually continues past the harmful call (the agent retries,
    or resumes the benign task), and every one of those steps has `taint = 1`
    with label 0. Keeping them makes taint look like a marker of "harm already
    happened" rather than "harm is imminent", and drives its coefficient
    negative.

    Sessions that never reach harm contribute all their steps as negatives.
    """
    labels = hazard_labels(n_steps, harm_step, k)
    last = n_steps - 1 if harm_step is None else min(harm_step, n_steps - 1)
    return [(t, labels[t]) for t in range(last + 1)]


def state_labels(
    signals_history: list[dict[str, float]],
    harm_step: int | None,
    revoked_at_step: int | None,
) -> list[int]:
    """The state trajectory of one session, one label per step."""
    labels: list[int] = []
    state = BENIGN
    for t, signals in enumerate(signals_history):
        if revoked_at_step is not None and t >= revoked_at_step:
            state = CONTAINED
        elif harm_step is not None and t > harm_step:
            state = HARM
        elif state in (HARM, CONTAINED):
            pass  # absorbing: never leaves
        else:
            if float(signals.get("taint", 0.0)) >= TAINT_LABEL_THRESHOLD:
                state = max(state, RECON)
            reaching = (
                float(signals.get("privilege_delta", 0.0)) >= PRIVILEGE_LABEL_THRESHOLD
                or float(signals.get("task_alignment", 0.0)) >= OUT_OF_SCOPE_LABEL_THRESHOLD
            )
            # Reaching only counts as escalation once the session is tainted:
            # before that there is no attacker in the loop to escalate.
            if reaching and state >= RECON:
                state = ESCALATION
            # The harmful call itself is the escalated one, whatever the
            # alignment score happened to say about its arguments.
            if harm_step is not None and t == harm_step:
                state = ESCALATION
        labels.append(state)
    return labels


def terminal_state(
    states: list[int],
    harm_step: int | None,
    revoked_at_step: int | None,
) -> int | None:
    """The state the session ends in, after its last recorded decision.

    A session that ends on the harmful call has no step left to carry the HARM
    label, so without this the corpus contains no completed transition into
    HARM at all and the solver never learns that allowing an escalated call
    costs anything. Returns None when the last step is not a transition into an
    absorbing state — an ordinary session that simply stopped is censored, not
    absorbed, and inventing a self-loop for it would be a made-up observation.
    """
    if not states:
        return None
    if revoked_at_step is not None and revoked_at_step >= len(states) - 1:
        return CONTAINED
    if harm_step is not None and harm_step == len(states) - 1:
        return HARM
    return None


def label_session(session: Any, k: int) -> tuple[list[int], list[int]]:
    """`(hazard_labels, state_labels)` for one recorded session."""
    signals_history = [step.signals or {} for step in session.steps]
    hazard = hazard_labels(len(session.steps), session.harm_step, k)
    states = state_labels(signals_history, session.harm_step, session.revoked_at_step)
    return hazard, states


def session_sort_key(session: Any) -> str:
    """Sessions are ordered by start time. Splits must respect this — a random
    split leaks the outcome into the features, because steps from the same
    session share a label."""
    return str(getattr(session, "started_at", "") or getattr(session, "session_id", ""))
