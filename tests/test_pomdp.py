"""POMDP layer: matrices, belief filter, policy, absorbing-DTMC view."""

from __future__ import annotations

import time

import numpy as np
import pytest

from sentinelz.pomdp.belief import BeliefFilter, update_belief
from sentinelz.pomdp.observe import ObservationEncoder
from sentinelz.pomdp.predict import (
    absorption_curve,
    absorption_prob,
    expected_steps_to_absorption,
    fundamental_matrix,
    marginal_chain,
)
from sentinelz.pomdp.solve import simplex_grid, solve
from sentinelz.pomdp.states import (
    ABSORBING_STATES,
    ACTION_NAMES,
    ACTION_STRICTNESS,
    ALLOW,
    BENIGN,
    CONTAINED,
    ESCALATION,
    HARM,
    MONITOR,
    N_ACTIONS,
    N_OBSERVATIONS,
    N_STATES,
    RECON,
    REVOKE,
    STATE_NAMES,
)

# --------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def matrices():
    """A hand-built T and O with the mechanics the real ones should learn.

    Used so the POMDP tests do not depend on whatever the last collection run
    happened to produce. The real matrices are checked in test_integration.
    """
    T = np.zeros((N_STATES, N_ACTIONS, N_STATES))

    for action in range(N_ACTIONS):
        # Passive actions let the session drift towards harm.
        drift = {ALLOW: 1.0, MONITOR: 1.0, 2: 0.55, 3: 0.30, REVOKE: 0.0}[action]
        T[BENIGN, action] = [0.90, 0.08 * drift, 0.02 * drift, 0.0, 0.0]
        T[BENIGN, action, BENIGN] += 1.0 - T[BENIGN, action].sum()

        T[RECON, action] = [0.10, 0.55, 0.30 * drift, 0.05 * drift, 0.0]
        T[RECON, action, RECON] += 1.0 - T[RECON, action].sum()

        T[ESCALATION, action] = [0.02, 0.10, 0.48, 0.40 * drift, 0.0]
        T[ESCALATION, action, ESCALATION] += 1.0 - T[ESCALATION, action].sum()

    # REVOKE contains from any transient state.
    for state in (BENIGN, RECON, ESCALATION):
        T[state, REVOKE] = 0.0
        T[state, REVOKE, CONTAINED] = 1.0

    for state in ABSORBING_STATES:
        T[state, :, :] = 0.0
        T[state, :, state] = 1.0

    O = np.zeros((N_STATES, N_OBSERVATIONS))
    O[BENIGN] = [0.45, 0.01, 0.30, 0.02, 0.12, 0.02, 0.05, 0.01, 0.01, 0.01]
    O[RECON] = [0.10, 0.15, 0.12, 0.20, 0.10, 0.18, 0.05, 0.06, 0.02, 0.02]
    O[ESCALATION] = [0.03, 0.07, 0.05, 0.10, 0.08, 0.17, 0.10, 0.20, 0.05, 0.15]
    O[HARM] = [0.01, 0.02, 0.02, 0.05, 0.03, 0.10, 0.05, 0.22, 0.10, 0.40]
    O[CONTAINED] = [0.30, 0.05, 0.20, 0.05, 0.15, 0.05, 0.10, 0.05, 0.03, 0.02]
    O /= O.sum(axis=1, keepdims=True)
    return T, O


@pytest.fixture(scope="module")
def policy(matrices):
    T, O = matrices
    return solve(T, O)


# ---------------------------------------------------------------- shapes


def test_transition_rows_sum_to_one(matrices):
    T, _ = matrices
    sums = T.sum(axis=2)
    assert np.allclose(sums, 1.0, atol=1e-9), sums


def test_absorbing_rows_are_exact(matrices):
    T, _ = matrices
    for state in ABSORBING_STATES:
        for action in range(N_ACTIONS):
            assert T[state, action, state] == pytest.approx(1.0)
            assert T[state, action].sum() == pytest.approx(1.0)


def test_observation_rows_sum_to_one(matrices):
    _, O = matrices
    assert np.allclose(O.sum(axis=1), 1.0, atol=1e-9)


# ---------------------------------------------------------------- belief


def test_belief_stays_normalised_over_500_random_updates(matrices):
    T, O = matrices
    filt = BeliefFilter(T, O)
    rng = np.random.default_rng(0)
    for _ in range(500):
        action = int(rng.integers(0, N_ACTIONS))
        observation = int(rng.integers(0, N_OBSERVATIONS))
        belief = filt.update(action, observation)
        assert belief.sum() == pytest.approx(1.0, abs=1e-9)
        assert (belief >= -1e-12).all()


def test_belief_starts_at_the_prior(matrices):
    T, O = matrices
    filt = BeliefFilter(T, O)
    assert filt.belief.tolist() == [1.0, 0.0, 0.0, 0.0, 0.0]
    filt.update(MONITOR, 9)
    filt.reset()
    assert filt.belief.tolist() == [1.0, 0.0, 0.0, 0.0, 0.0]


def test_revoke_drives_belief_to_contained(matrices):
    T, O = matrices
    filt = BeliefFilter(T, O)
    filt.update(MONITOR, 7)
    belief = filt.update(REVOKE, 0)
    assert belief[CONTAINED] > 0.9


def test_degenerate_update_keeps_the_prediction(matrices):
    """An impossible observation must not divide by zero, and must be counted."""
    T, O = matrices
    blocked = O.copy()
    blocked[:, 3] = 0.0
    filt = BeliefFilter(T, blocked)
    belief = filt.update(MONITOR, 3)
    assert filt.degenerate_updates == 1
    assert belief.sum() == pytest.approx(1.0, abs=1e-9)


def test_pure_function_matches_the_filter(matrices):
    T, O = matrices
    filt = BeliefFilter(T, O)
    expected = update_belief(T, O, filt.belief, MONITOR, 5)
    assert np.allclose(filt.update(MONITOR, 5), expected)


def test_belief_update_under_50_microseconds(matrices):
    T, O = matrices
    filt = BeliefFilter(T, O)
    for _ in range(200):
        filt.update(MONITOR, 4)

    timings = []
    for i in range(2000):
        started = time.perf_counter()
        filt.update(MONITOR, i % N_OBSERVATIONS)
        timings.append(time.perf_counter() - started)
    median_us = sorted(timings)[len(timings) // 2] * 1e6
    assert median_us < 50.0, f"belief update median {median_us:.1f}us exceeds 50us"


# ------------------------------------------------------------------ grid


def test_simplex_grid_has_the_expected_size():
    grid = simplex_grid(20, 5)
    assert grid.shape == (10626, 5)
    assert np.allclose(grid.sum(axis=1), 1.0)


def test_nearest_grid_point_is_exact_for_grid_points(policy):
    for i in (0, 1, 500, 5000, 10625):
        assert policy.index_of(policy.grid[i]) == i


# ---------------------------------------------------------------- policy


def test_policy_is_permissive_at_pure_benign(policy):
    """A REVOKE at a near-pure BENIGN belief means the costs or the matrix are
    wrong. That must fail here, not after the results are written up."""
    action = policy.act(np.array([1.0, 0.0, 0.0, 0.0, 0.0]))
    assert action in (ALLOW, MONITOR), f"got {ACTION_NAMES[action]} at pure BENIGN"


def test_policy_acts_at_high_harm_belief(policy):
    action = policy.act(np.array([0.0, 0.05, 0.35, 0.60, 0.0]))
    assert ACTION_STRICTNESS[action] >= ACTION_STRICTNESS[2], f"got {ACTION_NAMES[action]}"


@pytest.mark.parametrize("target", [RECON, ESCALATION])
def test_policy_monotonicity_along_the_risk_gradient(policy, target):
    """As belief mass shifts from BENIGN towards a riskier transient state,
    the chosen action must never get looser.

    RECON and ESCALATION are the states that *predict* harm. This is the
    monotonicity property that matters, and it is the one the defense's claim
    rests on.
    """
    previous = -1
    seen = []
    for fraction in np.linspace(0.0, 1.0, 21):
        belief = np.zeros(N_STATES)
        belief[BENIGN] = 1.0 - fraction
        belief[target] = fraction
        action = policy.act(belief)
        seen.append((round(float(fraction), 2), ACTION_NAMES[action]))
        assert ACTION_STRICTNESS[action] >= previous, f"policy loosened along the path: {seen}"
        previous = ACTION_STRICTNESS[action]


def test_policy_monotonicity_in_predicted_future_harm(policy, matrices):
    """Strictness is non-decreasing in P(harm arrives in the next 3 steps).

    Stated over transient beliefs only, and over pairs whose predicted risk
    differs by a real margin, so that grid discretisation in flat regions of
    the value function does not masquerade as a policy inversion.
    """
    T, _ = matrices
    rng = np.random.default_rng(7)
    samples = []
    for _ in range(300):
        transient = rng.dirichlet(np.ones(3))
        belief = np.array([transient[0], transient[1], transient[2], 0.0, 0.0])
        samples.append((absorption_prob(T, belief, 3), ACTION_STRICTNESS[policy.act(belief)]))

    samples.sort()
    for (risk_low, strict_low), (risk_high, strict_high) in zip(samples, samples[1:], strict=False):
        if risk_high - risk_low < 0.02:
            continue
        assert strict_high >= strict_low, (
            f"policy loosened as predicted harm rose: "
            f"risk {risk_low:.4f} -> {risk_high:.4f}, strictness {strict_low} -> {strict_high}"
        )


def test_policy_relaxes_once_harm_is_already_realised(policy, matrices):
    """Documented consequence of the PRD's cost model, not a bug.

    Harm costs 100.0 ON THE TRANSITION into HARM, never per-step inside it.
    HARM is absorbing, so belief mass already there can generate no further
    cost, and the probability of *new* harm strictly falls as mass leaves the
    transient states. A policy that still paid 3.0 to REVOKE a session it
    believes is already over would be spending for nothing.

    The practical reading: the policy is a predictor of imminent harm, so it
    must be judged on what it does *before* the harmful call. That is exactly
    what T3 (advance warning) measures.
    """
    new_harm_risk = []
    for fraction in np.linspace(0.0, 1.0, 11):
        belief = np.zeros(N_STATES)
        belief[BENIGN] = 1.0 - fraction
        belief[HARM] = fraction
        new_harm_risk.append(absorption_prob(matrices[0], belief, 3) - fraction)

    assert all(b <= a + 1e-12 for a, b in zip(new_harm_risk, new_harm_risk[1:], strict=False)), (
        "future-harm risk should fall as mass enters the absorbing state"
    )
    assert policy.act(np.array([0.0, 0.0, 0.0, 1.0, 0.0])) in (ALLOW, MONITOR)


def test_expected_costs_agree_with_the_chosen_action(policy):
    belief = np.array([0.3, 0.3, 0.3, 0.1, 0.0])
    costs = policy.expected_costs(belief)
    assert int(np.argmin(costs)) == policy.act(belief)
    assert len(costs) == N_ACTIONS


def test_every_cost_constant_is_saved(policy):
    for name in ACTION_NAMES:
        assert f"cost_{name}" in policy.constants
    for key in ("resolution", "horizon", "gamma", "harm_cost"):
        assert key in policy.constants


def test_higher_harm_cost_never_gives_a_looser_policy(matrices):
    """The frontier must move in the direction it claims to."""
    T, O = matrices
    cheap = solve(T, O, harm_cost=10.0)
    dear = solve(T, O, harm_cost=1000.0)
    cheap_strictness = np.array([ACTION_STRICTNESS[int(a)] for a in cheap.actions])
    dear_strictness = np.array([ACTION_STRICTNESS[int(a)] for a in dear.actions])
    assert dear_strictness.mean() >= cheap_strictness.mean()


# --------------------------------------------------------- absorbing DTMC


def test_marginal_chain_is_row_stochastic(matrices):
    T, _ = matrices
    P = marginal_chain(T, MONITOR)
    assert np.allclose(P.sum(axis=1), 1.0)


def test_absorption_probability_in_range_and_non_decreasing(matrices):
    T, _ = matrices
    for belief in (
        np.array([1.0, 0.0, 0.0, 0.0, 0.0]),
        np.array([0.2, 0.5, 0.3, 0.0, 0.0]),
        np.array([0.0, 0.1, 0.9, 0.0, 0.0]),
    ):
        previous = -1.0
        for n in (1, 2, 3, 4, 5):
            probability = absorption_prob(T, belief, n)
            assert 0.0 <= probability <= 1.0
            assert probability >= previous - 1e-12, f"decreased at n={n}"
            previous = probability


def test_absorption_curve_matches_pointwise(matrices):
    T, _ = matrices
    belief = np.array([0.1, 0.4, 0.5, 0.0, 0.0])
    curve = absorption_curve(T, belief, 5)
    for n, value in curve.items():
        assert value == pytest.approx(absorption_prob(T, belief, n))


def test_escalation_predicts_harm_sooner_than_benign(matrices):
    """The advance-warning claim, in its simplest form."""
    T, _ = matrices
    benign = absorption_prob(T, np.array([1.0, 0.0, 0.0, 0.0, 0.0]), 3)
    escalation = absorption_prob(T, np.array([0.0, 0.0, 1.0, 0.0, 0.0]), 3)
    assert escalation > benign


def test_fundamental_matrix_is_finite_and_positive(matrices):
    T, _ = matrices
    N = fundamental_matrix(T, MONITOR)
    assert np.isfinite(N).all()
    assert (N >= 0).all()


def test_expected_steps_shrink_as_harm_approaches(matrices):
    T, _ = matrices
    far = expected_steps_to_absorption(T, np.array([1.0, 0.0, 0.0, 0.0, 0.0]))
    near = expected_steps_to_absorption(T, np.array([0.0, 0.0, 1.0, 0.0, 0.0]))
    assert near < far


def test_absorbed_belief_has_no_remaining_steps(matrices):
    T, _ = matrices
    assert expected_steps_to_absorption(T, np.array([0.0, 0.0, 0.0, 1.0, 0.0])) == 0.0


# ------------------------------------------------------------- observe


def test_observation_symbol_range():
    encoder = ObservationEncoder()
    seen = set()
    for p in np.linspace(0.0, 1.0, 51):
        for tainted in (False, True):
            symbol = encoder.obs(float(p), tainted)
            assert 0 <= symbol < N_OBSERVATIONS
            seen.add(symbol)
    assert len(seen) == N_OBSERVATIONS


def test_observation_encodes_taint_in_the_low_bit():
    encoder = ObservationEncoder()
    assert encoder.obs(0.5, False) % 2 == 0
    assert encoder.obs(0.5, True) % 2 == 1
    assert encoder.obs(0.5, True) == encoder.obs(0.5, False) + 1


def test_observation_bins_are_monotone_in_hazard():
    encoder = ObservationEncoder(edges=[0.1, 0.2, 0.3, 0.4])
    bins = [encoder.hazard_bin(p) for p in (0.0, 0.15, 0.25, 0.35, 0.9)]
    assert bins == sorted(bins)
    assert bins == [0, 1, 2, 3, 4]


def test_observation_bins_round_trip(tmp_path):
    encoder = ObservationEncoder(edges=[0.11, 0.22, 0.33, 0.44])
    path = tmp_path / "bins.json"
    encoder.save(path)
    assert ObservationEncoder.load(path).edges == encoder.edges


def test_tied_quantiles_are_separated():
    """Most benign steps score identically, so the quantiles tie. The encoder
    must still produce five usable bins rather than collapsing to one."""
    encoder = ObservationEncoder.fit([0.02] * 200)
    assert encoder.edges == sorted(set(encoder.edges))
    assert len(encoder.edges) == 4


def test_state_and_action_names_are_frozen():
    """AGENTS.md forbids renaming these. Every saved matrix depends on them."""
    assert STATE_NAMES == ("BENIGN", "RECON", "ESCALATION", "HARM", "CONTAINED")
    assert ACTION_NAMES == ("ALLOW", "MONITOR", "SCOPE_DOWN", "STEP_UP", "REVOKE")
    assert ABSORBING_STATES == (HARM, CONTAINED)
