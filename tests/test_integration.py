"""End-to-end tests over the real pipeline.

These run actual AgentDojo tasks through the actual gateway. They are slower
than the unit tests and they are the ones that would catch a wiring mistake
that every isolated component passes.
"""

from __future__ import annotations

import pytest

from arena.agent.runner import TargetAgent
from arena.env.suites import DEMO_INJECTION_TASK_ID, expected_tools, load_suite, task_max_tier
from arena.eval.baselines import build_baseline
from arena.evidence_log import build_chain, session_records
from arena.red.outcome import did_attack_succeed
from arena.red.static import TEMPLATES, StaticAttacker
from sentinelz.evidence.chain import verify
from sentinelz.gateway import Gateway
from sentinelz.pomdp.states import ACTION_NAMES

SUITE = "workspace"
BENIGN_TASK = "user_task_0"


# ------------------------------------------------------------ environment


def test_both_suites_load_with_tasks_and_injections():
    for name, min_tasks in (("workspace", 30), ("banking", 10)):
        suite = load_suite(name)
        assert len(suite.user_tasks) >= min_tasks
        assert len(suite.injection_tasks) >= 5


def test_demo_injection_task_is_registered_and_marked():
    suite = load_suite("workspace")
    assert DEMO_INJECTION_TASK_ID in suite.injection_tasks
    task = suite.injection_tasks[DEMO_INJECTION_TASK_ID]
    assert getattr(task, "DEMO_ONLY", False) is True, "the one task we wrote must be tagged"


def test_expected_tools_come_from_the_ground_truth():
    tools = expected_tools(SUITE, BENIGN_TASK)
    assert tools
    assert all(isinstance(t, str) for t in tools)
    assert task_max_tier(SUITE, BENIGN_TASK) >= 0


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_template_produces_an_injection(template):
    attacker = StaticAttacker(template, SUITE)
    injectable = attacker.injectable_user_tasks()
    assert injectable, f"{template} has no injectable user tasks"
    injections = attacker.injections(injectable[0], "injection_task_0")
    assert injections
    assert all(text.strip() for text in injections.values())


# ------------------------------------------------------------------ runs


def test_benign_run_completes_and_records_steps():
    session = TargetAgent(suite=SUITE, persist=False).run(BENIGN_TASK)
    assert session.steps
    assert session.completed is True
    assert session.injection_task_id is None
    assert did_attack_succeed(session) is False
    assert session.backend in ("scripted", "ollama")


def test_attacked_run_records_the_attack_verdict():
    attacker = StaticAttacker("important_instructions", SUITE)
    task_id = attacker.injectable_user_tasks()[0]
    session = TargetAgent(suite=SUITE, persist=False).run(task_id, "injection_task_0")
    assert session.injection_task_id == "injection_task_0"
    assert isinstance(session.attack_succeeded, bool)
    assert did_attack_succeed(session, "injection_task_0") == session.attack_succeeded


def test_asking_about_the_wrong_injection_raises():
    session = TargetAgent(suite=SUITE, persist=False).run(BENIGN_TASK, "injection_task_0")
    with pytest.raises(ValueError):
        did_attack_succeed(session, "injection_task_3")


def test_determinism_same_seed_same_trace():
    """Requirement N5, end to end."""
    agent_a = TargetAgent(suite=SUITE, seed=7, persist=False)
    agent_b = TargetAgent(suite=SUITE, seed=7, persist=False)
    first = agent_a.run(BENIGN_TASK)
    second = agent_b.run(BENIGN_TASK)

    assert [s.tool_name for s in first.steps] == [s.tool_name for s in second.steps]
    assert [s.tool_args for s in first.steps] == [s.tool_args for s in second.steps]
    assert first.completed == second.completed


def test_decision_log_is_byte_identical_across_identical_runs():
    """The determinism claim as it is actually stated: a fixed seed and a
    fixed trace reproduce a byte-identical log."""
    from sentinelz.evidence.canonical import dumps

    def run_once():
        gateway = build_baseline("sentinelz_full", SUITE, BENIGN_TASK)
        session = TargetAgent(
            suite=SUITE, seed=3, defense="sentinelz_full", gateway=gateway, persist=False
        ).run(BENIGN_TASK)
        records = session_records(session)
        for record in records:
            record.pop("session_id", None)  # a timestamp, by construction
        return dumps(records)

    assert run_once() == run_once()


# -------------------------------------------------------------- defended


def test_defended_run_produces_decisions_on_every_step():
    gateway = build_baseline("sentinelz_full", SUITE, BENIGN_TASK)
    session = TargetAgent(
        suite=SUITE, defense="sentinelz_full", gateway=gateway, persist=False
    ).run(BENIGN_TASK)
    assert session.steps
    for step in session.steps:
        assert step.decision is not None, f"step {step.idx} has no decision"
        assert step.decision["action"] in ACTION_NAMES
        assert step.signals is not None
        assert set(step.signals) == {
            "injection_likelihood",
            "task_alignment",
            "privilege_delta",
            "taint",
            "sequence_novelty",
        }


def test_defense_does_not_destroy_benign_utility():
    """C4: the cost of compliance has to be measured, and it has to be small."""
    undefended = TargetAgent(suite=SUITE, persist=False).run(BENIGN_TASK)
    gateway = build_baseline("sentinelz_full", SUITE, BENIGN_TASK)
    defended = TargetAgent(
        suite=SUITE, defense="sentinelz_full", gateway=gateway, persist=False
    ).run(BENIGN_TASK)
    assert undefended.completed is True
    assert defended.completed is True, "the defense broke a benign task"
    assert defended.revoked_at_step is None, "benign task triggered a false revocation"


def test_decision_latency_within_budget():
    """N1. Measured over a real run, not a synthetic context."""
    gateway = build_baseline("sentinelz_full", SUITE, BENIGN_TASK)
    session = TargetAgent(
        suite=SUITE, defense="sentinelz_full", gateway=gateway, persist=False
    ).run(BENIGN_TASK)
    totals = [
        float((step.decision or {}).get("timings_ms", {}).get("t_total", 0.0))
        for step in session.steps
    ]
    totals = [t for t in totals if t > 0]
    assert totals, "no timings recorded"
    assert max(totals) < 100.0, f"decision path exceeded the 100ms budget: {max(totals):.1f}ms"


# ------------------------------------------------------- fail closed


def test_a_broken_gateway_revokes_rather_than_allowing():
    """N7, through the real object: any exception returns REVOKE."""
    gateway = Gateway(mode="observe")

    def explode(_ctx, precomputed=None):
        raise RuntimeError("signal bundle died")

    gateway.bundle.evaluate = explode  # type: ignore[assignment]
    from conftest import make_ctx

    decision = gateway.evaluate(make_ctx())
    assert decision.action == "REVOKE"
    assert "exception in decision path" in decision.reason


def test_budget_overrun_revokes():
    gateway = Gateway(mode="threshold", budget_ms=0)
    from conftest import make_ctx

    decision = gateway.evaluate(make_ctx())
    assert decision.action == "REVOKE"
    assert "budget exceeded" in decision.reason


# ------------------------------------------------------- evidence log


def test_session_produces_a_verifiable_chain():
    gateway = build_baseline("sentinelz_full", SUITE, BENIGN_TASK)
    session = TargetAgent(
        suite=SUITE, defense="sentinelz_full", gateway=gateway, persist=False
    ).run(BENIGN_TASK)
    chain = build_chain(session)
    assert len(chain) == len(session.steps)
    assert verify(chain.records) == (True, None)


def test_chain_excludes_timings_so_it_can_reproduce():
    gateway = build_baseline("sentinelz_full", SUITE, BENIGN_TASK)
    session = TargetAgent(
        suite=SUITE, defense="sentinelz_full", gateway=gateway, persist=False
    ).run(BENIGN_TASK)
    for record in session_records(session):
        assert "timings_ms" not in record["decision"], "wall-clock in a hashed record breaks N5"


# ------------------------------------------------------------ baselines


@pytest.mark.parametrize(
    "name", ["no_defense", "injection_classifier_only", "per_call_policy", "sentinelz_full"]
)
def test_every_baseline_runs(name):
    gateway = build_baseline(name, SUITE, BENIGN_TASK)
    session = TargetAgent(suite=SUITE, defense=name, gateway=gateway, persist=False).run(BENIGN_TASK)
    assert session.steps or name == "per_call_policy"


def test_unknown_baseline_is_rejected():
    with pytest.raises(ValueError):
        build_baseline("does_not_exist", SUITE, BENIGN_TASK)
