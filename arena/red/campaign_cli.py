"""`sz adapt` — run an adaptive campaign and print the arms race."""

from __future__ import annotations

import argparse
import sys

from arena import config
from arena.agent.backends import describe
from arena.env.suites import DEMO_INJECTION_TASK_ID
from arena.eval.baselines import build_baseline
from arena.eval.report import markdown_table
from arena.red.adaptive import AdaptiveAttacker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an adaptive attack campaign (M7).")
    parser.add_argument("--suite", default="workspace", choices=list(config.SUITES))
    parser.add_argument("--task", default="user_task_10")
    parser.add_argument("--injection", default=DEMO_INJECTION_TASK_ID)
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--no-defense", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--show-diffs", action="store_true")
    parser.add_argument(
        "--all-rounds",
        action="store_true",
        help="keep going after the attacker first gets through, for the full ASR-vs-round curve",
    )
    args = parser.parse_args(argv)

    info = describe()
    attacker = AdaptiveAttacker(
        max_rounds=args.rounds, suite=args.suite, stop_on_success=not args.all_rounds
    )
    print(f"backend {info['resolved']} | rewriter {attacker.rewriter}")
    if attacker.rewriter == "ladder":
        print("NOTE: no Ollama reachable - using the deterministic mutation ladder.")
        print("      Same feedback channel (which signal fired highest), no LLM.\n")

    gateway = None if args.no_defense else build_baseline("sentinelz_full", args.suite, args.task)
    campaign = attacker.attack(
        args.task,
        args.injection,
        defense=gateway,
        defense_name="none" if args.no_defense else "sentinelz",
        use_cache=not args.no_cache,
    )

    if not campaign.rounds:
        print("no rounds ran: that (user_task, injection_task) pair is not injectable.")
        return 1

    rows = [
        {
            "round": r.round_index,
            "outcome": "GOT THROUGH" if r.succeeded else r.outcome,
            "defense": r.defense_action,
            "top signal": r.top_signal,
            "value": round(r.top_signal_value, 2),
            "strategy": r.strategy,
            "leaked": r.leaked_records,
            "cum ASR": round(campaign.asr_by_round[i], 3),
        }
        for i, r in enumerate(campaign.rounds)
    ]
    print(markdown_table(rows))

    first = campaign.first_success_round
    print(f"\nrounds: {len(campaign.rounds)} | first success: {first if first else 'never'}")
    print(f"cached to {config.CAMPAIGNS_DIR}")

    if args.show_diffs:
        for r in campaign.rounds:
            if r.diff:
                print(f"\n--- round {r.round_index} diff ---")
                print(r.diff)
    return 0


if __name__ == "__main__":
    sys.exit(main())
