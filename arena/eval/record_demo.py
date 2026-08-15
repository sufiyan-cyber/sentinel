"""`sz record-demo` — record the whole demo so it can replay offline.

The demo must run in airplane mode. Recording it the night before is not a
fallback, it is the plan: local model inference stalling on stage is the most
likely thing to go wrong, and a replay is visually identical to a live run.
"""

from __future__ import annotations

import argparse
import shutil
import sys

from arena import config
from arena.agent.backends import describe
from arena.agent.runner import TargetAgent, save_session
from arena.env.suites import DEMO_INJECTION_TASK_ID
from arena.eval.baselines import build_baseline
from arena.evidence_log import log_session
from arena.red.adaptive import AdaptiveAttacker

DEMO_SEQUENCE = [
    # (label, user_task, injection_task or None)
    ("act1_benign", "user_task_10", None),
    ("act2_theft", "user_task_10", DEMO_INJECTION_TASK_ID),
    ("act3_credentials", "user_task_14", "injection_task_4"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record the demo into runs/demo/.")
    parser.add_argument("--suite", default="workspace")
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--keep", action="store_true", help="do not clear runs/demo first")
    args = parser.parse_args(argv)

    info = describe()
    print(f"recording demo | backend {info['resolved']} | model {info['model']}")

    if config.DEMO_DIR.exists() and not args.keep:
        shutil.rmtree(config.DEMO_DIR)
    config.DEMO_DIR.mkdir(parents=True, exist_ok=True)

    for label, task_id, injection_task_id in DEMO_SEQUENCE:
        print(f"  {label}: {task_id} / {injection_task_id or 'benign'}")

        undefended = TargetAgent(suite=args.suite, seed=args.seed, defense="none", persist=False).run(
            task_id, injection_task_id
        )
        gateway = build_baseline("sentinelz_full", args.suite, task_id)
        defended = TargetAgent(
            suite=args.suite, seed=args.seed, defense="sentinelz_full", gateway=gateway, persist=False
        ).run(task_id, injection_task_id)

        undefended.session_id = f"pair_{label}_left"
        defended.session_id = f"pair_{label}_right"
        save_session(undefended, config.DEMO_DIR)
        save_session(defended, config.DEMO_DIR)
        log_session(defended)

        print(f"     undefended: attack={undefended.attack_succeeded} leaked={len(undefended.exfiltrated_records)}")
        print(f"     defended:   attack={defended.attack_succeeded} leaked={len(defended.exfiltrated_records)} "
              f"revoked_at={defended.revoked_at_step}")

    print(f"  campaign: {args.rounds} rounds")
    attacker = AdaptiveAttacker(max_rounds=args.rounds, suite=args.suite, seed=args.seed)
    gateway = build_baseline("sentinelz_full", args.suite, "user_task_10")
    campaign = attacker.attack("user_task_10", DEMO_INJECTION_TASK_ID, defense=gateway, use_cache=False)
    campaign.save(config.DEMO_DIR)
    print(f"     rounds={len(campaign.rounds)} first_success={campaign.first_success_round}")

    print(f"\nrecorded to {config.DEMO_DIR}")
    print("replay with:  python sz.py demo        (no model, no network)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
