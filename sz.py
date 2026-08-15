"""Sentinel-Z task runner.

Every `make` target, runnable without `make` — which Windows does not ship.
`make` and `sz` stay in step; the Makefile just calls into this file.

    python sz.py <target> [options]
    sz <target>                     (via sz.cmd on Windows, ./sz on POSIX)

Run `python sz.py help` for the list.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = REPO_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
PYTHON = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))

# The Windows console defaults to cp1252, which turns every dash and "±" in a
# results table into a replacement character. Fix it once, here, for this
# process and for every subprocess this file launches.
CHILD_ENV = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

TARGETS: dict[str, tuple[str, list[str]]] = {
    # ---- setup
    "install": ("install dependencies into .venv", []),
    "check": ("ruff + mypy + pytest", []),
    "test": ("pytest only", [PYTHON, "-m", "pytest", "-q"]),
    "lint": ("ruff only", [PYTHON, "-m", "ruff", "check", "."]),
    "typecheck": ("mypy only", [PYTHON, "-m", "mypy"]),
    "fetch-models": ("download the optional neural injection classifier",
                     [PYTHON, "-m", "sentinelz.signals.injection", "--fetch"]),

    # ---- gates
    "smoke": ("M1 gate: 5 benign tasks complete", [PYTHON, "-m", "arena.eval.gates", "smoke"]),
    "run-benign": ("run benign tasks and print the table", [PYTHON, "-m", "arena.eval.gates", "smoke", "--n", "8"]),
    "run-attack": ("M2 gate: undefended ASR per template", [PYTHON, "-m", "arena.eval.gates", "attack"]),

    # ---- data and models
    "collect": ("collect labelled sessions (N=300)", [PYTHON, "-m", "arena.eval.collect", "-n", "300"]),
    "collect-sessions": ("alias of collect", [PYTHON, "-m", "arena.eval.collect", "-n", "300"]),
    "train-hazard": ("M4: fit the hazard model, write the coefficient table",
                     [PYTHON, "-m", "sentinelz.hazard.train"]),
    "estimate-pomdp": ("M4b: estimate T and O, write pomdp_report.md",
                       [PYTHON, "-m", "sentinelz.pomdp.estimate"]),
    "solve-pomdp": ("M4b: solve the grid policy and the F3 frontier",
                    [PYTHON, "-m", "sentinelz.pomdp.solve", "--frontier"]),
    "build-novelty": ("rebuild the benign tool-trigram table",
                      [PYTHON, "-m", "sentinelz.signals.novelty", "--build"]),
    "train": ("collect -> train-hazard -> estimate-pomdp -> solve-pomdp", []),

    # ---- running
    "run-arena": ("one attack, defended and undefended, printed",
                  [PYTHON, "-m", "arena.eval.arena_once"]),
    "adapt": ("M7: run an adaptive campaign", [PYTHON, "-m", "arena.red.campaign_cli"]),
    "eval": ("M9: regenerate every table and figure", [PYTHON, "-m", "arena.eval.tables"]),
    "eval-quick": ("eval without the ablation sweep",
                   [PYTHON, "-m", "arena.eval.tables", "--quick", "--seeds", "0,1", "--pairs", "4"]),

    # ---- ui and demo
    "ui": ("start the Arena UI", [PYTHON, "-m", "arena.ui.server"]),
    "replay": ("start the UI and replay the recorded demo",
               [PYTHON, "-m", "arena.ui.server", "--replay", "demo"]),
    "demo": ("replay the recorded demo, no model, no network",
             [PYTHON, "-m", "arena.ui.server", "--panic"]),
    "record-demo": ("record the full demo sequence into runs/demo/",
                    [PYTHON, "-m", "arena.eval.record_demo"]),
    "verify-log": ("verify the most recent decision log",
                   [PYTHON, "-m", "arena.eval.verify_latest"]),

    # ---- roles
    "attacker": ("role=attacker: the exfiltration server (laptop B)",
                 [PYTHON, "-m", "arena.exfil_server"]),
    "victim": ("role=victim: the agent, gateway and UI (laptop A)",
               [PYTHON, "-m", "arena.ui.server"]),
    "solo": ("all roles on one machine", []),
}


def run(command: list[str], env: dict[str, str] | None = None) -> int:
    print("+ " + " ".join(command))
    merged = {**os.environ, **CHILD_ENV, **(env or {})}
    return subprocess.call(command, cwd=str(REPO_ROOT), env=merged)


def target_install() -> int:
    if not VENV_PYTHON.exists():
        code = run([sys.executable, "-m", "venv", ".venv"])
        if code:
            return code
    code = run([PYTHON, "-m", "pip", "install", "--upgrade", "pip"])
    if code:
        return code
    role = os.environ.get("SENTINELZ_ROLE", "victim").strip().lower()
    extra = "attacker" if role == "attacker" else "victim"
    print(f"\ninstalling extras for role={extra}")
    return run([PYTHON, "-m", "pip", "install", "-e", f".[{extra},dev]"])


def target_check() -> int:
    for command in (
        [PYTHON, "-m", "ruff", "check", "."],
        [PYTHON, "-m", "mypy"],
        [PYTHON, "-m", "pytest", "-q"],
    ):
        code = run(command)
        if code:
            return code
    return 0


def target_train(argv: list[str]) -> int:
    n = "300"
    if "-n" in argv:
        n = argv[argv.index("-n") + 1]
    steps = [
        # --fresh so the corpus is exactly this run. Without it, runs/ still
        # holds every session from every previous collection and the matrices
        # are estimated over a mix of code versions.
        [PYTHON, "-m", "arena.eval.collect", "-n", n, "--fresh"],
        [PYTHON, "-m", "sentinelz.hazard.train"],
        [PYTHON, "-m", "sentinelz.pomdp.estimate"],
        [PYTHON, "-m", "sentinelz.pomdp.solve", "--frontier"],
        # Re-estimate now that the hazard model exists, so the observation
        # symbols come from the real sensor rather than the stand-in.
        [PYTHON, "-m", "sentinelz.pomdp.estimate"],
        [PYTHON, "-m", "sentinelz.pomdp.solve", "--frontier"],
    ]
    for command in steps:
        code = run(command)
        if code:
            return code
    return 0


def target_solo() -> int:
    """All roles on one machine. The fallback when the network fails on the
    day — and the network is the most likely thing to fail."""
    env = {"SENTINELZ_ATTACKER_HOST": "127.0.0.1"}
    print("starting the exfiltration server in the background on 127.0.0.1")
    server = subprocess.Popen(
        [PYTHON, "-m", "arena.exfil_server"],
        cwd=str(REPO_ROOT),
        env={**os.environ, **env},
    )
    try:
        return run([PYTHON, "-m", "arena.ui.server"], env=env)
    finally:
        server.terminate()


def usage() -> int:
    print(__doc__)
    print("targets:\n")
    width = max(len(name) for name in TARGETS)
    for name, (description, _) in TARGETS.items():
        print(f"  {name.ljust(width)}  {description}")
    print("\nextra arguments are passed through, e.g.:")
    print("  python sz.py collect -n 500")
    print("  python sz.py eval --quick")
    print("  python sz.py run-attack --suite banking")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"help", "-h", "--help"}:
        return usage()

    target, rest = argv[0], argv[1:]
    if target not in TARGETS:
        print(f"unknown target {target!r}\n")
        return usage()

    if target == "install":
        return target_install()
    if target == "check":
        return target_check()
    if target == "train":
        return target_train(rest)
    if target == "solo":
        return target_solo()

    _, command = TARGETS[target]
    if not command:
        print(f"target {target!r} has no command")
        return 1
    return run([*command, *rest])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
