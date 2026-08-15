"""Collect labelled sessions.

Milestone 4b cannot be estimated without these. The PRD asks for 300+; below
about 150 the transition cells are too sparse to mean anything, and the count
is printed so the limitation goes in the paper rather than being found by a
reviewer.

Three collection modes, deliberately mixed:

  observe    defense evaluates and records but never acts. Gives clean benign
             and clean attacked trajectories.
  bootstrap  a rule over raw signals (no learned model) that exercises all
             five actions, so the SCOPE_DOWN / STEP_UP / REVOKE rows of the
             transition matrix are backed by observations instead of by
             smoothing alone.

The split is 60/40. The bootstrap rule is never reported as a baseline.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from arena import config
from arena.agent.backends import describe
from arena.agent.runner import TargetAgent
from arena.env.suites import DEMO_INJECTION_TASK_ID, load_suite
from arena.red.static import TEMPLATES, StaticAttacker, _task_sort_key
from sentinelz.gateway import build_gateway

BOOTSTRAP_FRACTION = 0.4
BENIGN_FRACTION = 0.35


def collect(
    n: int = 300,
    seeds: tuple[int, ...] = (0, 1, 2, 3, 4),
    quiet: bool = False,
    fresh: bool = False,
) -> list[Any]:
    """Run `n` sessions across both suites and return them.

    Two passes, because `sequence_novelty` scores a step against a table of
    benign trigrams that does not exist until some benign sessions have been
    run. The first pass exists only to build that table and its sessions are
    **discarded**; the second pass re-runs everything, benign included, against
    the finished table.

    Keeping the first pass would be the subtle version of the same bug: those
    benign sessions were scored with no table loaded, so every one of them gets
    `sequence_novelty = 1.0` while the attacked sessions get a real score. The
    signal then reads "benign runs are the novel ones", the hazard model fits a
    negative coefficient on it, and the fifth signal is worse than absent.
    """
    info = describe()
    if not quiet:
        print(f"collecting {n} sessions | backend={info['resolved']} model={info['model']}")

    if fresh:
        removed = _clear_runs()
        if not quiet:
            print(f"  --fresh: removed {removed} session files from {config.RUNS_DIR}")

    plan = _build_plan(n, seeds)
    benign_plan = [item for item in plan if item["injection_task"] is None]
    started = time.monotonic()

    if not quiet:
        print(f"pass 1/2: {len(benign_plan)} benign sessions to build the novelty baseline "
              f"(not kept)")
    baseline = _run_plan(benign_plan, started, quiet, offset=0, total=len(plan), persist=False)
    build_table(baseline)

    if not quiet:
        print(f"pass 2/2: all {len(plan)} sessions against the finished baseline")
    sessions = _run_plan(plan, started, quiet, offset=0, total=len(plan))

    _summarise(sessions, started, quiet)
    return sessions


def _clear_runs() -> int:
    """Delete previously recorded sessions so a run is not mixed with the last.

    Only the session files directly in `runs/`; campaigns, demo recordings and
    exported decision logs live in subdirectories and are left alone.
    """
    if not config.RUNS_DIR.exists():
        return 0
    removed = 0
    for path in config.RUNS_DIR.glob("*.json"):
        path.unlink()
        removed += 1
    return removed


def _run_plan(
    plan: list[dict[str, Any]],
    started: float,
    quiet: bool,
    offset: int,
    total: int,
    persist: bool = True,
) -> list[Any]:
    gateways = {
        "observe": build_gateway(mode="observe"),
        "bootstrap": build_gateway(mode="bootstrap"),
    }
    agents: dict[tuple[str, str, int], TargetAgent] = {}
    sessions: list[Any] = []

    for j, item in enumerate(plan, start=1):
        i = offset + j
        key = (item["suite"], item["mode"], item["seed"])
        if key not in agents:
            agents[key] = TargetAgent(
                suite=item["suite"],
                seed=item["seed"],
                defense=item["mode"],
                gateway=gateways[item["mode"]],
                persist=persist,
            )
        agent = agents[key]
        try:
            session = agent.run(
                item["user_task"],
                item["injection_task"],
                template=item["template"],
            )
        except Exception as exc:  # one bad pair must not lose the batch
            if not quiet:
                print(f"  [{i}/{total}] FAILED {item['user_task']}/{item['injection_task']}: {exc}")
            continue
        sessions.append(session)
        if not quiet and (i % 25 == 0 or j == len(plan)):
            rate = i / max(time.monotonic() - started, 1e-6)
            print(f"  [{i}/{total}] {rate:.1f} sessions/s")
    return sessions


def build_table(sessions: list[Any]) -> None:
    """Build the benign tool-trigram table from the sessions collected so far."""
    from sentinelz.signals.novelty import build_table as write_trigrams

    payload = write_trigrams(sessions)
    print(f"  novelty baseline: {len(payload['counts'])} trigrams "
          f"from {payload['n_benign_sessions']} benign sessions")


def _summarise(sessions: list[Any], started: float, quiet: bool) -> None:
    if quiet:
        return
    attacked = sum(1 for s in sessions if s.injection_task_id)
    succeeded = sum(1 for s in sessions if s.attack_succeeded)
    revoked = sum(1 for s in sessions if s.revoked_at_step is not None)
    print(
        f"\ncollected {len(sessions)} sessions in {time.monotonic() - started:.1f}s: "
        f"{len(sessions) - attacked} benign, {attacked} attacked "
        f"({succeeded} succeeded), {revoked} with a revocation"
    )
    if len(sessions) < 150:
        print("WARNING: fewer than 150 sessions. Transition cells will be sparse; "
              "state the limitation rather than hiding it.")


def _build_plan(n: int, seeds: tuple[int, ...]) -> list[dict[str, Any]]:
    """Deterministic round-robin over suites, tasks, injections and templates."""
    plan: list[dict[str, Any]] = []
    catalogue: dict[str, dict[str, Any]] = {}
    for suite_name in config.SUITES:
        suite = load_suite(suite_name)
        injections = [t for t in sorted(suite.injection_tasks, key=_task_sort_key) if t != DEMO_INJECTION_TASK_ID]
        per_template = {}
        for template in TEMPLATES:
            try:
                per_template[template] = StaticAttacker(template, suite_name).injectable_user_tasks()
            except Exception:
                per_template[template] = []
        catalogue[suite_name] = {
            "user_tasks": sorted(suite.user_tasks, key=_task_sort_key),
            "injections": injections,
            "injectable": per_template,
        }

    suites = list(config.SUITES)
    for i in range(n):
        suite_name = suites[i % len(suites)]
        entry = catalogue[suite_name]
        seed = seeds[i % len(seeds)]
        mode = "bootstrap" if (i % 10) < int(BOOTSTRAP_FRACTION * 10) else "observe"
        benign = (i % 20) < int(BENIGN_FRACTION * 20)

        if benign:
            user_task = entry["user_tasks"][i % len(entry["user_tasks"])]
            plan.append(
                {"suite": suite_name, "user_task": user_task, "injection_task": None,
                 "template": "important_instructions", "seed": seed, "mode": mode}
            )
            continue

        template = TEMPLATES[i % len(TEMPLATES)]
        injectable = entry["injectable"].get(template) or entry["user_tasks"]
        user_task = injectable[i % len(injectable)]
        injection_task = entry["injections"][i % len(entry["injections"])]
        plan.append(
            {"suite": suite_name, "user_task": user_task, "injection_task": injection_task,
             "template": template, "seed": seed, "mode": mode}
        )
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect labelled sessions for M4/M4b.")
    parser.add_argument("-n", "--n", type=int, default=300)
    parser.add_argument("--seeds", type=str, default="0,1,2,3,4")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="delete previously recorded sessions first, so the corpus is exactly this run",
    )
    args = parser.parse_args(argv)

    seeds = tuple(int(s) for s in args.seeds.split(",") if s.strip())
    sessions = collect(args.n, seeds, fresh=args.fresh)
    print(f"sessions written to {config.RUNS_DIR}")
    return 0 if sessions else 1


if __name__ == "__main__":
    sys.exit(main())
