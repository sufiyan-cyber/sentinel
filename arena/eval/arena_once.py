"""One attack, run twice — undefended and defended — printed side by side.

The console version of what the Arena UI shows. Useful when checking a change
without starting a server, and it is what `sz run-arena` calls.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from arena import config
from arena.agent.backends import describe
from arena.agent.runner import TargetAgent
from arena.env.suites import DEMO_INJECTION_TASK_ID
from arena.eval.baselines import build_baseline
from arena.eval.report import markdown_table
from arena.evidence_log import log_session
from sentinelz.evidence.export import verify_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one attack defended and undefended.")
    parser.add_argument("--suite", default="workspace", choices=list(config.SUITES))
    parser.add_argument("--task", default="user_task_10")
    parser.add_argument("--injection", default=DEMO_INJECTION_TASK_ID)
    parser.add_argument("--template", default="important_instructions")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    info = describe()
    print(f"backend {info['resolved']} | model {info['model']}\n")

    undefended = TargetAgent(suite=args.suite, seed=args.seed, defense="none", persist=False).run(
        args.task, args.injection, template=args.template
    )
    gateway = build_baseline("sentinelz_full", args.suite, args.task)
    defended = TargetAgent(
        suite=args.suite, seed=args.seed, defense="sentinelz_full", gateway=gateway, persist=False
    ).run(args.task, args.injection, template=args.template)

    for title, session in (("UNDEFENDED", undefended), ("SENTINEL-Z", defended)):
        print(f"=== {title} " + "=" * (58 - len(title)))
        print(markdown_table([_row(step) for step in session.steps] or [{"(no steps)": ""}]))
        print(
            f"  completed={session.completed}  attack_succeeded={session.attack_succeeded}  "
            f"records_leaked={len(session.exfiltrated_records)}  revoked_at={session.revoked_at_step}"
        )
        print()

    path = log_session(defended)
    ok, bad = verify_file(path)
    print(f"decision log: {path}")
    print(f"  chain {'verifies' if ok else f'FAILS at record {bad}'}")

    print("\nsummary")
    print(markdown_table([undefended.summary_row(), defended.summary_row()]))
    return 0


def _row(step: Any) -> dict[str, Any]:
    decision = step.decision or {}
    signals = step.signals or {}
    return {
        "#": step.idx,
        "tool": step.tool_name,
        "inj": round(float(signals.get("injection_likelihood", 0.0)), 2),
        "align": round(float(signals.get("task_alignment", 0.0)), 2),
        "priv": round(float(signals.get("privilege_delta", 0.0)), 2),
        "taint": round(float(signals.get("taint", 0.0)), 2),
        "novel": round(float(signals.get("sequence_novelty", 0.0)), 2),
        "hazard": round(float(decision.get("hazard", 0.0)), 3),
        "state": decision.get("argmax_state", "-"),
        "action": decision.get("action", "-"),
        "blocked": step.blocked,
    }


if __name__ == "__main__":
    sys.exit(main())
