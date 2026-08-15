"""The decision path.

    signals -> hazard -> obs() -> belief.update() -> policy.act()

One hard rule governs everything in this file: no exception and no timeout
may produce an ALLOW. Every error path returns REVOKE. A defense that opens
when it breaks is not a defense.

Modes:
  pomdp      the real decision maker (default)
  threshold  ablation baseline, a plain hazard threshold
  bootstrap  rule over raw signals, used only to collect transition data
  observe    evaluate and record, never act — used to collect benign features
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from sentinelz.config import DECISION_BUDGET_MS, HAZARD_THRESHOLD
from sentinelz.context import DecisionContext
from sentinelz.policy.decide import Decision, decide, decide_bootstrap, decide_threshold
from sentinelz.pomdp.belief import BeliefFilter
from sentinelz.pomdp.observe import ObservationEncoder
from sentinelz.pomdp.states import ACTION_NAMES, ALLOW, MONITOR, REVOKE, STATE_NAMES, action_name
from sentinelz.signals.bundle import SignalBundle

MODES = ("pomdp", "threshold", "bootstrap", "observe")


class Gateway:
    """Holds every model, loads them once, and decides once per tool call."""

    def __init__(
        self,
        mode: str = "pomdp",
        bundle: SignalBundle | None = None,
        hazard: Any | None = None,
        encoder: ObservationEncoder | None = None,
        belief_filter: BeliefFilter | None = None,
        policy: Any | None = None,
        budget_ms: int = DECISION_BUDGET_MS,
        hazard_threshold: float = HAZARD_THRESHOLD,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; known: {MODES}")
        self.mode = mode
        self.budget_ms = budget_ms
        self.hazard_threshold = hazard_threshold

        # Everything below is constructed here, at startup, and never inside
        # a request. `tests/test_guardrails.py` enforces that.
        self.bundle = bundle or SignalBundle()
        self.hazard = hazard
        self.encoder = encoder or ObservationEncoder.load()
        self.belief_filter = belief_filter
        self.policy = policy

        self.signals_history: list[dict[str, float]] = []
        self.tools_history: list[str] = []
        self.last_action: int = MONITOR
        self.budget_exceeded = 0
        self._last_call_tainted = False
        #: The decision that revoked the token, kept so later refused calls can
        #: keep showing the evidence that justified them.
        self.revoking_decision: Decision | None = None

    # ------------------------------------------------------------ session
    def start_session(self) -> None:
        self.signals_history = []
        self.tools_history = []
        self.last_action = MONITOR
        self._last_call_tainted = False
        self.revoking_decision = None
        self.bundle.reset()
        if self.belief_filter is not None:
            self.belief_filter.reset()

    # ----------------------------------------------------------- evaluate
    def evaluate(self, ctx: DecisionContext) -> Decision:
        """Decide on one pending tool call. Never raises."""
        started = time.perf_counter()
        timings: dict[str, float] = {}
        try:
            t0 = time.perf_counter()
            # Order matters and is easy to get wrong: the injection score for
            # the previous tool result must be known, and taint must have
            # absorbed it, BEFORE this call's taint is evaluated. Otherwise
            # taint is always one step behind the content that caused it.
            precomputed = self._absorb_last_result(ctx)
            signals, explanation = self.bundle.evaluate(ctx, precomputed=precomputed)
            timings["t_signals"] = (time.perf_counter() - t0) * 1000.0

            self.signals_history.append(signals)
            self.tools_history.append(ctx.pending.tool_name)
            self._last_call_tainted = float(signals.get("taint", 0.0)) >= 0.5

            t0 = time.perf_counter()
            p_harm = self._hazard(signals)
            timings["t_hazard"] = (time.perf_counter() - t0) * 1000.0

            t0 = time.perf_counter()
            decision = self._act(signals, explanation, p_harm)
            timings["t_policy"] = (time.perf_counter() - t0) * 1000.0

            elapsed_ms = (time.perf_counter() - started) * 1000.0
            timings["t_total"] = elapsed_ms
            decision.timings_ms = {k: round(v, 3) for k, v in timings.items()}

            if elapsed_ms > self.budget_ms and self.mode not in ("observe",):
                self.budget_exceeded += 1
                return self._remember(self._revoke_decision(
                    f"decision budget exceeded: {elapsed_ms:.1f}ms > {self.budget_ms}ms",
                    signals=signals,
                    hazard=p_harm,
                    timings=timings,
                ))
            return self._remember(decision)

        except Exception as exc:  # noqa: BLE001 - the whole point is to catch everything
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            timings["t_total"] = elapsed_ms
            return self._remember(self._revoke_decision(
                f"exception in decision path: {type(exc).__name__}: {exc}"[:300], timings=timings
            ))

    def _remember(self, decision: Decision) -> Decision:
        """Keep the first REVOKE, so calls refused after it can cite it."""
        if decision.action == "REVOKE" and self.revoking_decision is None:
            self.revoking_decision = decision
        return decision

    # ------------------------------------------------------------ hazard
    def _hazard(self, signals: dict[str, float]) -> float:
        if self.hazard is None:
            # Before the model is trained the hazard column is still useful
            # for the UI, so report the strongest signal rather than 0.0 and
            # pretend nothing is happening.
            return float(max(signals.values(), default=0.0))
        from sentinelz.hazard.features import build

        features = build(self.signals_history, self.tools_history)
        return float(self.hazard.p_harm(features))

    # -------------------------------------------------------------- act
    def _act(self, signals: dict[str, float], explanation: dict[str, Any], p_harm: float) -> Decision:
        tainted = float(signals.get("taint", 0.0)) >= 0.5
        observation = self.encoder.obs(p_harm, tainted)

        if self.mode == "pomdp" and self.belief_filter is not None and self.policy is not None:
            belief = self.belief_filter.update(self.last_action, observation)
            absorption = self._absorption(belief)
            decision = decide(belief, self.policy, p_harm, observation, signals, absorption)
            decision.explanation = explanation
            self.last_action = decision.action_index
            return decision

        # Non-POMDP modes still report a belief when one is available, so the
        # UI and the logs have the same shape in every mode.
        belief = (
            self.belief_filter.update(self.last_action, observation)
            if self.belief_filter is not None
            else np.array([1.0, 0.0, 0.0, 0.0, 0.0])
        )
        absorption = self._absorption(belief)

        if self.mode == "threshold":
            action_index, reason = decide_threshold(p_harm, self.hazard_threshold)
            policy_name = "threshold"
        elif self.mode == "bootstrap":
            action_index, reason = decide_bootstrap(signals)
            policy_name = "bootstrap"
        else:
            action_index, reason = ALLOW, "observe mode: recording only, never acting"
            policy_name = "observe"

        self.last_action = action_index
        return Decision(
            action=action_name(action_index),
            action_index=action_index,
            belief=[float(b) for b in belief],
            argmax_state=STATE_NAMES[int(np.argmax(belief))],
            expected_costs={name: 0.0 for name in ACTION_NAMES},
            absorption=absorption,
            hazard=float(p_harm),
            observation=observation,
            signals=dict(signals),
            reason=reason,
            policy=policy_name,
            explanation=explanation,
        )

    def _absorption(self, belief: np.ndarray) -> dict[str, float]:
        if self.policy is None:
            return {"n1": 0.0, "n3": 0.0, "n5": 0.0}
        try:
            from sentinelz.pomdp.predict import absorption_prob

            return {
                f"n{n}": float(absorption_prob(self.policy.T, belief, n))
                for n in (1, 3, 5)
            }
        except Exception:
            return {"n1": 0.0, "n3": 0.0, "n5": 0.0}

    # ----------------------------------------------------------- revoke
    def standing_revocation(self) -> Decision:
        """The decision for a call made after the token was already destroyed.

        Not a fresh evaluation: the session is contained, the broker is what
        refuses the call, and re-deciding here would spend budget to produce a
        belief of CONTAINED — where the cheapest action is genuinely ALLOW,
        which is the correct answer to the wrong question and reads as the
        defense waving the call through.

        It carries the signals, costs and hazard of the decision that actually
        revoked. Zeroing them would leave the UI's signal bars empty for every
        refused call after the first, so the panel would go blank at exactly
        the moment it is meant to be showing why the session was killed.
        """
        source = self.revoking_decision
        decision = self._revoke_decision(
            "session token already destroyed"
            + (f" (revoked: {source.reason})" if source and source.reason else ""),
            signals=dict(source.signals) if source else None,
            hazard=source.hazard if source else 0.0,
        )
        decision.belief = [0.0, 0.0, 0.0, 0.0, 1.0]
        decision.argmax_state = "CONTAINED"
        decision.absorption = {"n1": 0.0, "n3": 0.0, "n5": 0.0}
        decision.policy = f"{self.mode}:contained"
        if source is not None:
            decision.expected_costs = dict(source.expected_costs)
            decision.observation = source.observation
            decision.explanation = source.explanation
        return decision

    def _revoke_decision(
        self,
        reason: str,
        signals: dict[str, float] | None = None,
        hazard: float = 1.0,
        timings: dict[str, float] | None = None,
    ) -> Decision:
        """The only thing an error path is allowed to return."""
        self.last_action = REVOKE
        decision = Decision(
            action="REVOKE",
            action_index=REVOKE,
            belief=[0.0, 0.0, 0.0, 1.0, 0.0],
            argmax_state="HARM",
            expected_costs={name: 0.0 for name in ACTION_NAMES},
            absorption={"n1": 1.0, "n3": 1.0, "n5": 1.0},
            hazard=float(hazard),
            observation=9,
            signals=dict(signals or {}),
            reason=reason,
            policy=f"{self.mode}:fail-closed",
            timings_ms={k: round(v, 3) for k, v in (timings or {}).items()},
        )
        return decision

    # ------------------------------------------------------------ taint
    def _absorb_last_result(self, ctx: DecisionContext) -> dict[str, tuple[float, dict[str, Any]]]:
        """Score the previous tool result, propagate taint from it, and hand
        the score back so the bundle does not run the classifier twice.

        Returns the precomputed injection entry for `SignalBundle.evaluate`.
        """
        if "injection_likelihood" in self.bundle.disabled or not ctx.history:
            ctx.taint_set = set(self.bundle.tracker.tainted)
            return {}

        t0 = time.perf_counter()
        try:
            score, detail = self.bundle.injection.score(ctx)
        except Exception as exc:
            score, detail = 1.0, {"error": str(exc)[:200]}
        detail = dict(detail)
        detail["_elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)

        last = ctx.history[-1]
        newly_tainted = self.bundle.absorb_result(
            last.tool_name, last.result_text, score, self._last_call_tainted
        )
        if newly_tainted:
            detail["newly_tainted"] = sorted(newly_tainted)[:8]
        ctx.taint_set = set(self.bundle.tracker.tainted)
        return {"injection_likelihood": (score, detail)}


def build_gateway(
    mode: str = "pomdp",
    disabled_signals: frozenset[str] = frozenset(),
    prefer_neural: bool = True,
    hazard_k: int = 3,
) -> Gateway:
    """Load every artifact from `models/` once and return a ready Gateway.

    Missing artifacts degrade the mode rather than crashing: with no trained
    hazard model, `pomdp` falls back to `bootstrap` and says so.
    """
    bundle = SignalBundle(prefer_neural=prefer_neural, disabled=disabled_signals)

    hazard = None
    try:
        from sentinelz.hazard.predict import HazardModel

        hazard = HazardModel.load(k=hazard_k)
    except Exception:
        hazard = None

    encoder = ObservationEncoder.load()

    belief_filter = None
    policy = None
    if mode == "pomdp":
        try:
            from sentinelz.pomdp.estimate import load_matrices
            from sentinelz.pomdp.solve import GridPolicy

            T, O = load_matrices()
            belief_filter = BeliefFilter(T, O)
            policy = GridPolicy.load(T=T, O=O)
        except Exception:
            mode = "bootstrap" if hazard is None else "threshold"

    return Gateway(
        mode=mode,
        bundle=bundle,
        hazard=hazard,
        encoder=encoder,
        belief_filter=belief_filter,
        policy=policy,
    )
