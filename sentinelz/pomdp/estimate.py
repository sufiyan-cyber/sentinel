"""Estimating the POMDP's transition and observation matrices from sessions.

  T: (5, 5, 5) indexed [state, action, next_state]
  O: (5, 10)   P(observation | state)

Counted from the state labels in `sentinelz/hazard/label.py`, Laplace add-1
smoothed, rows normalised. HARM and CONTAINED are absorbing, so their rows are
set exactly rather than estimated — an absorbing state that leaks probability
mass is not absorbing.

**Not every row is estimated, and pretending otherwise produces a broken
policy.** A defended run applies an intervention on a handful of steps, so the
SCOPE_DOWN / STEP_UP / REVOKE rows have almost no counts behind them; add-1
smoothing then makes each of them a uniform row, which says "REVOKE sends a
BENIGN session to HARM with probability 0.2". Value iteration reads that
literally and never revokes anything. Three provenances, each labelled in the
report:

  estimated  the passive dynamics — what the session does when nothing
             intervenes. ALLOW and MONITOR share one estimate, because
             MONITOR only logs; it does not touch the broker. That shared row
             is also exactly what `predict.py` conditions on for the
             absorbing-DTMC view.
  derived    SCOPE_DOWN and STEP_UP: the passive row with a stated fraction of
             the advancing mass held back, since a restricted or
             confirmation-gated call is the advance that does not happen.
  structural REVOKE: the broker destroys the token, so every later call fails
             and the session is CONTAINED. That is enforced in code and proved
             by the revocation test, not inferred from 21 samples.

`models/pomdp_report.md` prints BOTH the raw counts and the resulting
probabilities, per action, with its provenance. The raw counts are the
interpretability claim: they let a reviewer see which cells are backed by data
and which are not.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np

from sentinelz.config import MODELS_DIR
from sentinelz.hazard.label import state_labels, terminal_state
from sentinelz.pomdp.observe import ObservationEncoder
from sentinelz.pomdp.states import (
    ABSORBING_STATES,
    ACTION_NAMES,
    ALLOW,
    CONTAINED,
    HARM,
    MONITOR,
    N_ACTIONS,
    N_OBSERVATIONS,
    N_STATES,
    REVOKE,
    SCOPE_DOWN,
    STATE_NAMES,
    STEP_UP,
    action_index,
)

MATRICES_PATH = MODELS_DIR / "pomdp_v1.npz"
LAPLACE_ALPHA = 1.0

#: Fraction of sessions that carry an injection *in deployment*.
#:
#: The collection corpus is deliberately attack-heavy — roughly two sessions in
#: three — because that is how you get enough harmful transitions to estimate
#: anything at all. Counting it raw makes P(BENIGN -> HARM) about 0.13 per step,
#: and a solver told that a benign-looking session harms one step in eight will
#: correctly conclude that revoking everything immediately is the cheapest
#: policy. That is a true statement about the corpus and a useless defense.
#:
#: So transitions are re-weighted to a stated prevalence: standard prevalence
#: correction, one constant, printed in the report and adjustable from the CLI.
#: 0.05 is already pessimistic for real traffic; `--prevalence` sweeps it.
DEPLOYMENT_PREVALENCE = 0.05

#: Actions that do not touch the broker. MONITOR raises the logging level and
#: nothing else, so the session evolves exactly as it would under ALLOW; the
#: two share one pooled estimate rather than splitting the evidence.
PASSIVE_ACTIONS: tuple[int, ...] = (ALLOW, MONITOR)

#: How much of the advancing probability mass each intervention holds back.
#: SCOPE_DOWN narrows the capability, so a call to a target still in scope goes
#: through; STEP_UP requires a human, so almost nothing advances. Blocked mass
#: goes to *staying put*, not to CONTAINED: these actions buy a step, they do
#: not end the session. Stated here, saved with the matrices, printed in the
#: report — a number a reviewer can disagree with is better than a number
#: hidden inside a smoothing constant.
MITIGATION: dict[int, float] = {SCOPE_DOWN: 0.7, STEP_UP: 0.9}

#: Provenance of each action's row, for the report.
PROVENANCE: dict[int, str] = {
    ALLOW: "estimated (pooled passive dynamics)",
    MONITOR: "estimated (pooled passive dynamics — MONITOR only logs)",
    SCOPE_DOWN: f"derived: passive with {MITIGATION[SCOPE_DOWN]:.0%} of advancing mass held back",
    STEP_UP: f"derived: passive with {MITIGATION[STEP_UP]:.0%} of advancing mass held back",
    REVOKE: "structural: the broker destroys the token, so the session is CONTAINED",
}


def session_weights(sessions: list[Any], prevalence: float = DEPLOYMENT_PREVALENCE) -> dict[int, float]:
    """Per-session weight that maps the corpus onto a stated attack prevalence.

    Attacked sessions are over-sampled during collection on purpose, so counting
    them one-for-one bakes the collection ratio into the transition matrix. Each
    session is weighted by (target share / corpus share) for its own class, which
    leaves the total count unchanged and only moves mass between the two.
    """
    attacked = [s for s in sessions if getattr(s, "injection_task_id", None) is not None]
    benign = [s for s in sessions if getattr(s, "injection_task_id", None) is None]
    if not attacked or not benign:
        return {id(s): 1.0 for s in sessions}

    total = len(sessions)
    w_attacked = (prevalence * total) / len(attacked)
    w_benign = ((1.0 - prevalence) * total) / len(benign)
    return {
        **{id(s): w_attacked for s in attacked},
        **{id(s): w_benign for s in benign},
    }


def estimate(
    sessions: list[Any],
    hazard: Any | None = None,
    encoder: ObservationEncoder | None = None,
    prevalence: float = DEPLOYMENT_PREVALENCE,
) -> dict[str, Any]:
    """Count transitions and observations, re-weight, smooth, normalise."""
    encoder = encoder or ObservationEncoder.load()
    weights = session_weights(sessions, prevalence)

    transition_counts = np.zeros((N_STATES, N_ACTIONS, N_STATES), dtype=np.float64)
    raw_counts = np.zeros((N_STATES, N_ACTIONS, N_STATES), dtype=np.float64)
    observation_counts = np.zeros((N_STATES, N_OBSERVATIONS), dtype=np.float64)
    n_steps = 0
    n_sessions = 0

    for session in sessions:
        signals_history = [step.signals or {} for step in session.steps]
        if not signals_history:
            continue
        n_sessions += 1
        weight = weights.get(id(session), 1.0)
        states = state_labels(signals_history, session.harm_step, session.revoked_at_step)
        observations = _observations(session, signals_history, hazard, encoder)
        actions = _actions(session)

        for t, state in enumerate(states):
            # Observation counts are deliberately NOT re-weighted. `O` is
            # P(observation | state) — a property of the sensor, not of how
            # often the world is under attack. Prevalence belongs on the
            # transition dynamics; applying it here would only shrink the
            # sample for exactly the rare states whose rows are already thin.
            observation_counts[state, observations[t]] += 1.0
            n_steps += 1
            if t + 1 < len(states):
                transition_counts[state, actions[t], states[t + 1]] += weight
                raw_counts[state, actions[t], states[t + 1]] += 1.0

        # A session that ends *on* the harmful call still made that transition;
        # it just has no further step to carry the label. Dropping it would
        # remove most of the corpus's completed transitions into HARM.
        final = terminal_state(states, session.harm_step, session.revoked_at_step)
        if final is not None:
            transition_counts[states[-1], actions[-1], final] += weight
            raw_counts[states[-1], actions[-1], final] += 1.0

    passive = _passive_dynamics(transition_counts)
    T = _build_transitions(passive)
    O = _normalise_observations(observation_counts)

    return {
        "T": T,
        "O": O,
        "passive": passive,
        "transition_counts": raw_counts,
        "weighted_counts": transition_counts,
        "observation_counts": observation_counts,
        "n_sessions": n_sessions,
        "n_steps": n_steps,
        "prevalence": prevalence,
        "n_attacked": sum(1 for s in sessions if getattr(s, "injection_task_id", None) is not None),
    }


def _observations(session: Any, signals_history: list[dict[str, float]], hazard: Any, encoder: ObservationEncoder) -> list[int]:
    """Observation symbol per step, from the hazard model when available."""
    tainted = [float(s.get("taint", 0.0)) >= 0.5 for s in signals_history]

    if hazard is None:
        # Before the hazard model exists, use the strongest signal as a stand-in
        # so the matrices can still be estimated. Re-estimate after training.
        p_harm = [float(max(s.values(), default=0.0)) for s in signals_history]
    else:
        from sentinelz.hazard.features import build_session_matrix

        tools = [step.tool_name for step in session.steps]
        matrix = build_session_matrix(signals_history, tools)
        p_harm = hazard.p_harm_batch(matrix).tolist()

    return [encoder.obs(p, t) for p, t in zip(p_harm, tainted, strict=True)]


def _actions(session: Any) -> list[int]:
    """The action actually applied at each step."""
    out: list[int] = []
    for step in session.steps:
        decision = step.decision or {}
        name = decision.get("action", "MONITOR")
        try:
            out.append(action_index(str(name)))
        except ValueError:
            out.append(MONITOR)
    return out


def _passive_dynamics(counts: np.ndarray) -> np.ndarray:
    """P(next | state) with nothing intervening — the one row that is estimated.

    ALLOW and MONITOR counts are pooled: MONITOR changes what gets logged, not
    what the agent is allowed to do, so splitting the evidence between them
    would halve the sample for no gain.
    """
    pooled = counts[:, PASSIVE_ACTIONS, :].sum(axis=1) + LAPLACE_ALPHA
    for state in ABSORBING_STATES:
        # Absorbing rows are structural, not estimated. Smoothing them would
        # let probability mass leak out of HARM, which would be wrong in the
        # one place it matters most.
        pooled[state, :] = 0.0
        pooled[state, state] = 1.0
    return pooled / pooled.sum(axis=1, keepdims=True)


def _mitigate(passive: np.ndarray, strength: float) -> np.ndarray:
    """The passive row with `strength` of the mass that advances held back.

    "Advances" means moving to a strictly more compromised transient state, in
    the fixed order BENIGN < RECON < ESCALATION < HARM. CONTAINED is not an
    advance — it is the good outcome — so mass heading there is left alone.
    """
    out = passive.copy()
    for state in range(N_STATES):
        if state in ABSORBING_STATES:
            continue
        forward = slice(state + 1, HARM + 1)
        blocked = strength * out[state, forward]
        out[state, forward] -= blocked
        out[state, state] += float(blocked.sum())
    return out


def _build_transitions(passive: np.ndarray) -> np.ndarray:
    """Assemble T from the passive estimate, the two derived rows, and REVOKE."""
    T = np.zeros((N_STATES, N_ACTIONS, N_STATES), dtype=np.float64)
    for action in PASSIVE_ACTIONS:
        T[:, action, :] = passive
    for action, strength in MITIGATION.items():
        T[:, action, :] = _mitigate(passive, strength)

    # REVOKE is not estimated. The broker destroys the session's token, so every
    # subsequent call fails whatever it is — that is the C2 claim, and it is
    # enforced in code rather than hoped for.
    T[:, REVOKE, :] = 0.0
    T[:, REVOKE, CONTAINED] = 1.0

    for state in ABSORBING_STATES:
        T[state, :, :] = 0.0
        T[state, :, state] = 1.0
    return T


def _normalise_observations(counts: np.ndarray) -> np.ndarray:
    O = counts + LAPLACE_ALPHA
    sums = O.sum(axis=1, keepdims=True)
    return O / np.where(sums > 0, sums, 1.0)


# ------------------------------------------------------------------- i/o


def save_matrices(result: dict[str, Any], path: Path | None = None) -> Path:
    path = path or MATRICES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        T=result["T"],
        O=result["O"],
        passive=result["passive"],
        transition_counts=result["transition_counts"],
        weighted_counts=result["weighted_counts"],
        observation_counts=result["observation_counts"],
        n_sessions=result["n_sessions"],
        n_steps=result["n_steps"],
        prevalence=result["prevalence"],
        mitigation_scope_down=MITIGATION[SCOPE_DOWN],
        mitigation_step_up=MITIGATION[STEP_UP],
        laplace_alpha=LAPLACE_ALPHA,
    )
    return path


def load_matrices(path: Path | None = None) -> tuple[np.ndarray, np.ndarray]:
    path = path or MATRICES_PATH
    if not path.exists():
        raise FileNotFoundError(f"no POMDP matrices at {path}; run `sz estimate-pomdp`")
    data = np.load(path)
    return data["T"], data["O"]


def load_full(path: Path | None = None) -> dict[str, Any]:
    path = path or MATRICES_PATH
    data = np.load(path)
    return {key: data[key] for key in data.files}


# ---------------------------------------------------------------- report


def write_report(result: dict[str, Any], path: Path | None = None) -> Path:
    """T7b. Raw counts AND smoothed probabilities — never only the latter."""
    from arena.eval.report import markdown_table

    path = path or MODELS_DIR / "pomdp_report.md"
    T, O = result["T"], result["O"]
    transition_counts = result["transition_counts"]
    observation_counts = result["observation_counts"]

    lines = [
        "# POMDP matrices — estimation report (T7b)",
        "",
        f"Estimated from **{result['n_sessions']} sessions / {result['n_steps']} steps**, "
        f"of which {result['n_attacked']} carry an injection.",
        "",
        "Generated by `python -m sentinelz.pomdp.estimate`. Do not edit by hand.",
        "",
        "## Prevalence correction",
        "",
        f"The corpus is **{result['n_attacked'] / max(1, result['n_sessions']):.0%} attacked** by "
        f"design — that is how you get enough harmful transitions to estimate anything. "
        f"Transitions are re-weighted to a deployment prevalence of "
        f"**{result['prevalence']:.0%}** before the passive row is fitted.",
        "",
        "This is not cosmetic. Counted raw, `P(BENIGN -> HARM)` is the *corpus* attack rate,",
        "and a solver told that a benign-looking session harms one step in eight concludes,",
        "correctly for that corpus, that revoking every session immediately is cheapest. The",
        "re-weighting is one stated constant (`DEPLOYMENT_PREVALENCE`), it leaves the total",
        "count unchanged, and `--prevalence` sweeps it. Cells below print the **raw observed**",
        "count; the probability beside it is fitted on the re-weighted counts.",
        "",
        "## How to read the rest",
        "",
        "Three of the five action rows are not estimated, and the report says which.",
        "A defended session applies an intervention on a handful of steps, so the",
        "counts behind SCOPE_DOWN, STEP_UP and REVOKE are far too thin to carry a",
        "transition matrix. Add-1 smoothing on those rows does not express ignorance —",
        "it asserts that REVOKE sends a benign session to HARM one time in five, and a",
        "value-iteration solver believes it and never revokes anything.",
        "",
        "| provenance | rows | meaning |",
        "|---|---|---|",
        "| estimated | ALLOW, MONITOR | the passive dynamics, counted from the sessions. "
        "MONITOR raises the logging level and touches nothing else, so the two pool one "
        "estimate instead of splitting the evidence. This pooled row is also what the "
        "absorbing-DTMC view in `predict.py` conditions on. |",
        f"| derived | SCOPE_DOWN, STEP_UP | the passive row with "
        f"{MITIGATION[SCOPE_DOWN]:.0%} / {MITIGATION[STEP_UP]:.0%} of the *advancing* mass "
        "held back and returned to the current state. A restricted or human-gated call is "
        "the escalation that does not happen; it buys a step rather than ending the session. |",
        "| structural | REVOKE | the broker destroys the session's token, so every later "
        "call fails regardless of what it is. `T[s, REVOKE, CONTAINED] = 1`. This is "
        "enforced in code and proved by the revocation test in `tests/test_broker.py`, "
        "not inferred from the sessions. |",
        "",
        "HARM and CONTAINED are absorbing: `T[s,a,s] = 1` for those rows, set structurally.",
        "Smoothing them would let mass leak out of an absorbing state.",
        "",
        "**Raw observed counts are printed in every cell.** They are the counts for that",
        "(state, action) pair as it actually occurred; on a derived or structural row they",
        "are context, not the source of the number. A count of zero on an estimated row is",
        "smoothing, and should be read as such.",
        "",
        "## Transition matrix T[state, action, next_state]",
        "",
    ]

    for action in range(N_ACTIONS):
        total = float(transition_counts[:, action, :].sum())
        lines += [
            f"### action = {ACTION_NAMES[action]}",
            "",
            f"*{PROVENANCE[action]}* — {int(total)} transitions observed under this action.",
            "",
        ]
        rows = []
        for state in range(N_STATES):
            row: dict[str, Any] = {"from \\ to": STATE_NAMES[state]}
            for next_state in range(N_STATES):
                count = int(transition_counts[state, action, next_state])
                row[STATE_NAMES[next_state]] = f"{T[state, action, next_state]:.3f} ({count})"
            rows.append(row)
        lines += [markdown_table(rows), ""]

    unobserved = [STATE_NAMES[s] for s in range(N_STATES) if observation_counts[s].sum() == 0]
    lines += [
        "## Observation matrix O[state, observation]",
        "",
        "Observation symbol = `hazard_bin * 2 + tainted`, so even symbols are untainted",
        "and odd symbols are tainted; the bin rises with the hazard model's output.",
        "",
        "Counts here are **raw and unweighted**. `O` is P(observation | state) — a property",
        "of the sensor, not of how often traffic is under attack — so the prevalence",
        "correction that applies to the transition rows deliberately does not apply here.",
        "",
    ]
    if unobserved:
        lines += [
            f"**{', '.join(unobserved)} {'is' if len(unobserved) == 1 else 'are'} never observed "
            "directly**, so those rows are the uninformative add-1 prior. That is the honest "
            "state of the evidence and it is largely harmless: both are absorbing, the session "
            "normally ends on entering them, and belief mass arrives there through the "
            "transition matrix rather than by being inferred from a reading. The policy's job "
            "is to avoid entering them, not to recognise them once entered.",
            "",
        ]
    rows = []
    for state in range(N_STATES):
        row = {"state": STATE_NAMES[state]}
        for observation in range(N_OBSERVATIONS):
            count = int(observation_counts[state, observation])
            row[f"o{observation}"] = f"{O[state, observation]:.3f} ({count})"
        rows.append(row)
    lines += [markdown_table(rows), ""]

    passive_counts = transition_counts[:, PASSIVE_ACTIONS, :].sum(axis=1)
    transient = [s for s in range(N_STATES) if s not in ABSORBING_STATES]
    thin = [STATE_NAMES[s] for s in transient if passive_counts[s].sum() < 30]

    lines += [
        "## Coverage of the estimated row",
        "",
        "Only the passive row is estimated, so only its coverage limits the result.",
        f"Counts below are pooled over {' and '.join(ACTION_NAMES[a] for a in PASSIVE_ACTIONS)}.",
        "",
    ]
    rows = [
        {
            "from state": STATE_NAMES[s],
            "observed transitions": int(passive_counts[s].sum()),
            "distinct next states seen": int((passive_counts[s] > 0).sum()),
        }
        for s in transient
    ]
    lines += [markdown_table(rows), ""]
    lines += [
        f"- total observed transitions, all actions: **{int(transition_counts.sum())}**",
        f"- of those, under a passive action: **{int(passive_counts.sum())}**",
        "",
    ]
    if thin:
        lines += [
            f"**Limitation.** {', '.join(thin)} "
            f"{'has' if len(thin) == 1 else 'have'} fewer than 30 observed passive transitions, "
            "so those rows lean on the add-1 prior. Report this rather than letting a reviewer "
            "find it; the fix is more collected sessions, not a different estimator.",
            "",
        ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Estimate the POMDP matrices (M4b).")
    parser.add_argument("--runs", type=Path, default=None)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument(
        "--prevalence",
        type=float,
        default=DEPLOYMENT_PREVALENCE,
        help="assumed share of deployed sessions carrying an injection",
    )
    args = parser.parse_args(argv)

    from arena.agent.runner import load_labelled_sessions as load_sessions

    sessions = load_sessions(args.runs)
    if not sessions:
        print("no sessions found. Run `sz collect` first.")
        return 1

    hazard = None
    try:
        from sentinelz.hazard.predict import HazardModel

        hazard = HazardModel.load(k=args.k)
    except Exception as exc:
        print(f"no hazard model ({exc}); estimating with the fallback observation encoder")

    encoder = _fit_encoder(sessions, hazard)
    result = estimate(sessions, hazard=hazard, encoder=encoder, prevalence=args.prevalence)
    save_matrices(result)
    report = write_report(result)

    passive = result["passive"]
    print(f"estimated from {result['n_sessions']} sessions / {result['n_steps']} steps "
          f"({result['n_attacked']} attacked)")
    print(f"  {int(result['transition_counts'].sum())} observed transitions, re-weighted to "
          f"prevalence {args.prevalence:.0%}")
    print("  passive P(-> HARM):  " + "  ".join(
        f"{STATE_NAMES[s]}={passive[s, 3]:.3f}" for s in range(N_STATES) if s not in ABSORBING_STATES
    ))
    print(f"  wrote {MATRICES_PATH}")
    print(f"  wrote {report}")
    return 0


def _fit_encoder(sessions: list[Any], hazard: Any) -> ObservationEncoder:
    """Fit the observation bins on the benign validation split, once, and save."""
    from sentinelz.hazard.train import split_by_session

    _, validation = split_by_session(sessions)
    benign = [s for s in validation if s.injection_task_id is None] or [
        s for s in sessions if s.injection_task_id is None
    ]

    values: list[float] = []
    for session in benign:
        signals_history = [step.signals or {} for step in session.steps]
        if not signals_history:
            continue
        if hazard is None:
            values.extend(float(max(s.values(), default=0.0)) for s in signals_history)
        else:
            from sentinelz.hazard.features import build_session_matrix

            tools = [step.tool_name for step in session.steps]
            values.extend(hazard.p_harm_batch(build_session_matrix(signals_history, tools)).tolist())

    encoder = ObservationEncoder.fit(values)
    encoder.save()
    print(f"  observation bin edges (fitted on {len(benign)} benign validation sessions): "
          f"{[round(e, 4) for e in encoder.edges]}")
    return encoder


if __name__ == "__main__":
    sys.exit(main())
