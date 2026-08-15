"""`sz verify-log` — verify decision logs, and prove tampering is caught."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from arena import config
from sentinelz.evidence.export import read_chain, verify_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify hash-chained decision logs.")
    parser.add_argument("--all", action="store_true", help="verify every log under runs/")
    parser.add_argument("--demo-tamper", action="store_true",
                        help="flip one byte in a copy and show the verifier naming the record")
    args = parser.parse_args(argv)

    logs = sorted(config.RUNS_DIR.glob("*/decisions.jsonl"))
    if not logs:
        print(f"no decision logs under {config.RUNS_DIR}. Run `sz run-arena` first.")
        return 1

    targets = logs if args.all else [logs[-1]]
    failures = 0
    for path in targets:
        ok, bad = verify_file(path)
        count = len(read_chain(path))
        status = "OK  " if ok else "FAIL"
        detail = "chain intact" if ok else f"first bad record at index {bad}"
        print(f"{status} {count:>4} records  {detail}  {path}")
        failures += 0 if ok else 1

    if args.demo_tamper:
        print()
        _demo_tamper(targets[-1])
    return 0 if failures == 0 else 1


def _demo_tamper(path: Path) -> None:
    """Act 5 of the demo: change one byte, watch the verifier name it."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        print("log too short to demonstrate tampering")
        return
    index = len(lines) // 2

    copy = path.with_name("decisions_tampered.jsonl")
    tampered = list(lines)
    tampered[index] = tampered[index].replace('"ALLOW"', '"REVOKE"', 1)
    if tampered[index] == lines[index]:
        tampered[index] = tampered[index].replace('"REVOKE"', '"ALLOW"', 1)
    copy.write_text("\n".join(tampered) + "\n", encoding="utf-8")

    ok, bad = verify_file(copy)
    print(f"changed one field in record {index} of a copy:")
    print(f"  verify -> {'OK (unexpected!)' if ok else f'FAIL at record {bad}'}")
    print(f"  copy at {copy}")


if __name__ == "__main__":
    sys.exit(main())
