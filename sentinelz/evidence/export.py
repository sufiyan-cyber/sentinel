"""Write and verify decision logs on disk."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from sentinelz.config import RUNS_DIR
from sentinelz.evidence.canonical import dumps_str, loads
from sentinelz.evidence.chain import verify


def decisions_path(run_id: str) -> Path:
    return RUNS_DIR / run_id / "decisions.jsonl"


def write_chain(run_id: str, records: list[dict[str, Any]]) -> Path:
    """Write an already-linked chain to `runs/<run_id>/decisions.jsonl`."""
    path = decisions_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(dumps_str(record) + "\n")
    return path


def read_chain(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return [loads(line) for line in fh if line.strip()]


def verify_file(path: Path) -> tuple[bool, int | None]:
    return verify(read_chain(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a Sentinel-Z decision log.")
    parser.add_argument("path", type=Path, help="path to a decisions.jsonl file")
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"no such file: {args.path}")
        return 2

    ok, bad = verify_file(args.path)
    count = len(read_chain(args.path))
    if ok:
        print(f"OK   {count} records, chain intact: {args.path}")
        return 0
    print(f"FAIL {count} records, first bad record at index {bad}: {args.path}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
