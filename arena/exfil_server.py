"""The attacker's collection server (role = attacker, laptop B).

THIS TERMINAL IS THE DEMO. Records must arrive visibly, one at a time, with a
typing effect. A counter that says "12 records leaked" is a dashboard and
nobody cares; payroll rows arriving one by one while people watch is the
moment the demo works.

Runs on 0.0.0.0:8899 with no ML dependencies at all, so a `git clone` on a
weak second laptop installs in seconds:

    pip install -e ".[attacker]"
    python -m arena.exfil_server
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from arena import config
from sentinelz.evidence.canonical import dumps_str, loads

LOG_PATH = config.RUNS_DIR / "exfil_log.jsonl"
TYPING_DELAY_S = 0.012
"""Per-character delay. About 80 characters a second — fast enough not to
drag, slow enough that the audience sees each row land."""

_queue: Queue[dict[str, Any]] = Queue()
_received: list[dict[str, Any]] = []
_lock = threading.Lock()


# ------------------------------------------------------------------ app


def create_app() -> Any:
    # Imported into module globals, not just this function's locals. This file
    # uses `from __future__ import annotations`, so every annotation is a
    # string that FastAPI resolves with `get_type_hints` against the *module*
    # namespace. A function-local `Request` is invisible there, so FastAPI
    # cannot tell the parameter is the request object, decides it must be a
    # query parameter, and answers every POST /collect with 422 — the whole
    # exfiltration path silently dead, on the one endpoint the demo is about.
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    globals().setdefault("Request", Request)
    globals().setdefault("JSONResponse", JSONResponse)

    app = FastAPI(title="collection endpoint", docs_url=None, redoc_url=None)

    @app.post("/collect")
    async def collect(request: Request) -> JSONResponse:
        try:
            payload = loads(await request.body())
        except Exception:
            payload = {"source": "unknown", "records": []}
        records = [str(r) for r in payload.get("records", []) if str(r).strip()]
        source = str(payload.get("source", "unknown"))
        entry = {
            "received_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "source": source,
            "records": records,
        }
        with _lock:
            _received.append(entry)
        _append_log(entry)
        for record in records:
            _queue.put({"source": source, "record": record})
        return JSONResponse({"ok": True, "received": len(records)})

    @app.get("/status")
    async def status() -> JSONResponse:
        with _lock:
            total = sum(len(e["records"]) for e in _received)
            return JSONResponse({"payloads": len(_received), "records": total})

    @app.post("/reset")
    async def reset() -> JSONResponse:
        with _lock:
            _received.clear()
        while not _queue.empty():
            try:
                _queue.get_nowait()
            except Empty:
                break
        if LOG_PATH.exists():
            LOG_PATH.unlink()
        return JSONResponse({"ok": True})

    @app.get("/records")
    async def records() -> JSONResponse:
        with _lock:
            return JSONResponse({"entries": list(_received)})

    return app


def _append_log(entry: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(dumps_str(entry) + "\n")


# -------------------------------------------------------------- terminal


def _terminal_loop(typing_delay: float = TYPING_DELAY_S) -> None:
    """Print each arriving record one character at a time.

    Never batch-print. Never just show a counter.
    """
    def plain_write(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def plain_newline() -> None:
        plain_write("\n")

    writer: Callable[[str], None] = plain_write
    newline: Callable[[], None] = plain_newline

    try:
        from rich.console import Console

        console = Console()

        def rich_write(text: str) -> None:
            console.print(text, style="bold green", end="")

        def rich_newline() -> None:
            console.print("")

        writer, newline = rich_write, rich_newline
    except Exception:
        pass  # rich is optional; the plain writer looks the same, minus colour

    _banner()
    count = 0
    while True:
        try:
            item = _queue.get(timeout=0.25)
        except Empty:
            continue
        count += 1
        prefix = f"[{count:04d}] "
        writer(prefix)
        for character in str(item["record"]):
            writer(character)
            time.sleep(typing_delay)
        newline()


def _banner() -> None:
    print()
    print("=" * 78)
    print(f"  COLLECTION ENDPOINT  --  listening on 0.0.0.0:{config.ATTACKER_PORT}")
    print("  every record that reaches this terminal left the victim's boundary")
    print("=" * 78)
    print()


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attacker collection server (role=attacker).")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=config.ATTACKER_PORT)
    parser.add_argument("--typing-delay", type=float, default=TYPING_DELAY_S)
    parser.add_argument("--quiet", action="store_true", help="no terminal view, just the API")
    args = parser.parse_args(argv)

    import uvicorn

    if not args.quiet:
        thread = threading.Thread(target=_terminal_loop, args=(args.typing_delay,), daemon=True)
        thread.start()
    else:
        _banner()

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="error")
    return 0


def replay_log(path: Path | None = None, typing_delay: float = TYPING_DELAY_S) -> int:
    """Re-type a recorded exfiltration log. Used by the offline demo."""
    path = path or LOG_PATH
    if not path.exists():
        print(f"no exfiltration log at {path}")
        return 1
    _banner()
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = loads(line)
        for record in entry.get("records", []):
            count += 1
            sys.stdout.write(f"[{count:04d}] ")
            for character in str(record):
                sys.stdout.write(character)
                sys.stdout.flush()
                time.sleep(typing_delay)
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
