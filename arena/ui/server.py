"""The Arena UI backend.

FastAPI plus server-sent events. One page, precise layout, dark, projector
legible. Not Streamlit — the layout matters too much.

The two columns are the same task run twice, undefended and defended. Both
runs are computed first and then streamed interleaved at the chosen speed, so
what the audience sees is identical whether the model is live or the run came
off disk. That is deliberate: local inference stalling on stage is the single
most likely thing to go wrong, and `--replay` is visually indistinguishable.
"""

from __future__ import annotations

import argparse
import contextlib
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

from arena import config
from arena.agent.backends import describe
from arena.agent.runner import TargetAgent, load_session
from arena.env import egress
from arena.env.suites import DEMO_INJECTION_TASK_ID, load_suite
from arena.red.adaptive import AdaptiveAttacker, AttackCampaign
from arena.red.static import _task_sort_key
from sentinelz.evidence.canonical import dumps_str
from sentinelz.gateway import build_gateway

STATIC_DIR = Path(__file__).parent / "static"

DEFAULT_DEMO_TASK = "user_task_10"
DEFAULT_DEMO_INJECTION = DEMO_INJECTION_TASK_ID
DEFAULT_TEMPLATE = "important_instructions"


class ArenaState:
    """Everything the page shows, and the thread that produces it."""

    def __init__(self) -> None:
        self.subscribers: list[queue.Queue[str]] = []
        self.lock = threading.Lock()
        self.speed = 1.0
        self.defense_on = True
        self.threshold = 0.5
        self.busy = False
        self.scoreboard: dict[str, float] = {
            "attempted": 0,
            "succeeded": 0,
            "blocked": 0,
            "benign_total": 0,
            "benign_completed": 0,
            "leaked_undefended": 0,
            "round": 0,
        }
        self.last_pair: dict[str, Any] = {}
        self.gateway: Any | None = None

    # ------------------------------------------------------------ events
    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=2000)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        message = f"event: {event}\ndata: {dumps_str(payload)}\n\n"
        with self.lock:
            targets = list(self.subscribers)
        for q in targets:
            # A subscriber that has stopped reading must not block the run.
            with contextlib.suppress(queue.Full):
                q.put_nowait(message)

    def pause(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds / max(self.speed, 0.01)))

    # -------------------------------------------------------- scoreboard
    def push_scoreboard(self) -> None:
        board: dict[str, float] = dict(self.scoreboard)
        total = board["benign_total"]
        board["utility_retained"] = (board["benign_completed"] / total * 100.0) if total else 100.0
        self.emit("scoreboard", board)

    def reset(self) -> None:
        self.scoreboard = {
            "attempted": 0,
            "succeeded": 0,
            "blocked": 0,
            "benign_total": 0,
            "benign_completed": 0,
            "leaked_undefended": 0,
            "round": 0,
        }
        self.last_pair = {}
        self.emit("reset", {})
        self.push_scoreboard()

    def get_gateway(self) -> Any:
        if self.gateway is None:
            self.gateway = build_gateway(mode="pomdp")
        return self.gateway


STATE = ArenaState()


# ------------------------------------------------------------- streaming


def _session_payload(session: Any, side: str) -> dict[str, Any]:
    return {
        "side": side,
        "task_id": session.task_id,
        "suite": session.suite,
        "backend": session.backend,
        "defense": session.defense,
        "injection": session.injection_task_id,
        "template": session.attack_template,
        "completed": session.completed,
        "attack_succeeded": session.attack_succeeded,
        "revoked_at_step": session.revoked_at_step,
        "n_steps": len(session.steps),
    }


def _step_payload(step: Any, side: str, session: Any) -> dict[str, Any]:
    decision = step.decision or {}
    exfiltrated: list[str] = []
    if not step.blocked and egress.is_external(step.tool_name, step.tool_args):
        exfiltrated = egress.extract_records(step.tool_name, step.tool_args)
    return {
        "side": side,
        "idx": step.idx,
        "tool": step.tool_name,
        "args": _truncate_args(step.tool_args),
        "result": (step.tool_result_text or "")[:400],
        "blocked": step.blocked,
        "note": step.note,
        "signals": step.signals or {},
        "decision": decision.get("action", ""),
        "reason": decision.get("reason", ""),
        "belief": decision.get("belief", []),
        "argmax_state": decision.get("argmax_state", ""),
        "expected_costs": decision.get("expected_costs", {}),
        "absorption": decision.get("absorption", {}),
        "hazard": decision.get("hazard", 0.0),
        "timings_ms": decision.get("timings_ms", {}),
        "exfiltrated": exfiltrated,
        "task_id": session.task_id,
    }


def _truncate_args(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in args.items():
        text = str(value)
        out[key] = text[:220] + ("..." if len(text) > 220 else "")
    return out


def stream_pair(undefended: Any, defended: Any, state: ArenaState) -> None:
    """Interleave two completed sessions step by step."""
    state.emit("pair_start", {
        "left": _session_payload(undefended, "left"),
        "right": _session_payload(defended, "right"),
    })

    length = max(len(undefended.steps), len(defended.steps))
    for i in range(length):
        if i < len(undefended.steps):
            state.emit("step", _step_payload(undefended.steps[i], "left", undefended))
            state.pause(0.35)
        if i < len(defended.steps):
            state.emit("step", _step_payload(defended.steps[i], "right", defended))
            state.pause(0.35)

    # The scoreboard scores ONE system: Sentinel-Z. The undefended column is the
    # control and does not feed it. Counting the left column's successes and the
    # right column's blocks into the same three numbers made them incoherent —
    # a single attack pair added one to `attempted`, one to `succeeded` (because
    # it got through undefended) and one to `blocked` (because it did not get
    # through defended), so the board read "5 attempted, 3 succeeded, 4 blocked".
    if undefended.injection_task_id:
        state.scoreboard["attempted"] += 1
        if defended.attack_succeeded:
            state.scoreboard["succeeded"] += 1
        elif defended.revoked_at_step is not None:
            # Blocked means the defense acted and the attack then failed.
            # Crediting every non-success would claim wins against attacks that
            # never landed in the first place.
            state.scoreboard["blocked"] += 1
        state.scoreboard["leaked_undefended"] += len(undefended.exfiltrated_records)
    else:
        state.scoreboard["benign_total"] += 1
        state.scoreboard["benign_completed"] += 1 if defended.completed else 0
    state.push_scoreboard()

    state.last_pair = {
        "left": undefended.model_dump(),
        "right": defended.model_dump(),
    }
    state.emit("pair_end", {
        "left": _session_payload(undefended, "left"),
        "right": _session_payload(defended, "right"),
        "timeline": _timeline(defended),
    })


def _timeline(session: Any) -> list[dict[str, Any]]:
    """One tick per step, for the flight-recorder scrubber."""
    out = []
    for step in session.steps:
        decision = step.decision or {}
        out.append({
            "idx": step.idx,
            "tool": step.tool_name,
            "signals": step.signals or {},
            "hazard": decision.get("hazard", 0.0),
            "decision": decision.get("action", ""),
            "belief": decision.get("belief", []),
            "absorption": decision.get("absorption", {}),
            "expected_costs": decision.get("expected_costs", {}),
            "blocked": step.blocked,
        })
    return out


# ----------------------------------------------------------------- work


def _run_pair(task_id: str, injection_task_id: str | None, template: str, suite: str) -> None:
    state = STATE
    if state.busy:
        state.emit("log", {"message": "already running"})
        return
    state.busy = True
    try:
        state.emit("log", {"message": f"running {task_id} / {injection_task_id or 'benign'} on {suite}"})

        undefended = TargetAgent(suite=suite, defense="none", persist=False).run(
            task_id, injection_task_id, template=template
        )
        gateway = state.get_gateway() if state.defense_on else None
        defended = TargetAgent(
            suite=suite,
            defense="sentinelz" if gateway else "none",
            gateway=gateway,
            persist=False,
        ).run(task_id, injection_task_id, template=template)

        stream_pair(undefended, defended, state)
    except Exception as exc:  # a UI must not die on one bad task
        state.emit("log", {"message": f"error: {type(exc).__name__}: {exc}"})
    finally:
        state.busy = False


def _run_campaign(task_id: str, injection_task_id: str, suite: str, max_rounds: int) -> None:
    state = STATE
    if state.busy:
        return
    state.busy = True
    try:
        attacker = AdaptiveAttacker(max_rounds=max_rounds, suite=suite)
        gateway = state.get_gateway() if state.defense_on else None
        state.emit("campaign_start", {"max_rounds": max_rounds, "rewriter": attacker.rewriter})

        def on_round(round_: Any) -> None:
            state.scoreboard["round"] = round_.round_index
            state.scoreboard["attempted"] += 1
            if round_.succeeded:
                state.scoreboard["succeeded"] += 1
            elif round_.defense_intervened:
                state.scoreboard["blocked"] += 1
            state.push_scoreboard()
            state.emit("round", round_.model_dump())
            state.pause(0.6)

        campaign = attacker.attack(
            task_id, injection_task_id, defense=gateway,
            defense_name="sentinelz" if gateway else "none",
            use_cache=False, on_round=on_round,
        )
        state.emit("campaign_end", {
            "asr_by_round": campaign.asr_by_round,
            "first_success_round": campaign.first_success_round,
            "rounds": len(campaign.rounds),
        })
    except Exception as exc:
        state.emit("log", {"message": f"campaign error: {type(exc).__name__}: {exc}"})
    finally:
        state.busy = False


def _replay(path: Path) -> None:
    state = STATE
    if state.busy:
        return
    state.busy = True
    try:
        if path.is_dir():
            _replay_demo_dir(path)
            return
        session = load_session(path)
        stream_pair(session, session, state)
    except Exception as exc:
        state.emit("log", {"message": f"replay error: {type(exc).__name__}: {exc}"})
    finally:
        state.busy = False


def _replay_demo_dir(directory: Path) -> None:
    """Replay a recorded demo: pairs first, then the campaign."""
    state = STATE
    pairs = sorted(directory.glob("pair_*_left.json"))
    for left_path in pairs:
        right_path = left_path.with_name(left_path.name.replace("_left.json", "_right.json"))
        if not right_path.exists():
            continue
        stream_pair(load_session(left_path), load_session(right_path), state)
        state.pause(0.8)

    for campaign_path in sorted(directory.glob("campaign_*.json")):
        campaign = AttackCampaign.load(campaign_path)
        state.emit("campaign_start", {"max_rounds": len(campaign.rounds), "rewriter": campaign.rewriter})
        for round_ in campaign.rounds:
            state.scoreboard["round"] = round_.round_index
            state.scoreboard["attempted"] += 1
            if round_.succeeded:
                state.scoreboard["succeeded"] += 1
            elif round_.defense_intervened:
                state.scoreboard["blocked"] += 1
            state.push_scoreboard()
            state.emit("round", round_.model_dump())
            state.pause(0.6)
        state.emit("campaign_end", {
            "asr_by_round": campaign.asr_by_round,
            "first_success_round": campaign.first_success_round,
            "rounds": len(campaign.rounds),
        })


# ------------------------------------------------------------------ app


def create_app() -> Any:
    from fastapi import FastAPI
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="Sentinel-Z Arena", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/events")
    async def events() -> StreamingResponse:
        q = STATE.subscribe()

        def generate():
            try:
                yield "retry: 2000\n\n"
                while True:
                    try:
                        yield q.get(timeout=15.0)
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                STATE.unsubscribe(q)

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/status")
    async def status() -> JSONResponse:
        info = describe()
        return JSONResponse({
            "backend": info,
            "speed": STATE.speed,
            "defense_on": STATE.defense_on,
            "threshold": STATE.threshold,
            "busy": STATE.busy,
            "scoreboard": STATE.scoreboard,
            "suites": list(config.SUITES),
        })

    @app.get("/api/tasks")
    async def tasks(suite: str = "workspace") -> JSONResponse:
        task_suite = load_suite(suite)
        return JSONResponse({
            "user_tasks": sorted(task_suite.user_tasks, key=_task_sort_key),
            "injection_tasks": sorted(task_suite.injection_tasks, key=_task_sort_key),
        })

    @app.get("/api/recordings")
    async def recordings() -> JSONResponse:
        files = sorted(p.name for p in config.RUNS_DIR.glob("*.json"))
        demo = config.DEMO_DIR.exists()
        return JSONResponse({"files": files[-200:], "demo_available": demo})

    @app.post("/api/run-benign")
    async def run_benign(suite: str = "workspace", task_id: str = DEFAULT_DEMO_TASK) -> JSONResponse:
        threading.Thread(target=_run_pair, args=(task_id, None, DEFAULT_TEMPLATE, suite), daemon=True).start()
        return JSONResponse({"ok": True})

    @app.post("/api/run-attack")
    async def run_attack(
        suite: str = "workspace",
        task_id: str = DEFAULT_DEMO_TASK,
        injection_task_id: str = DEFAULT_DEMO_INJECTION,
        template: str = DEFAULT_TEMPLATE,
    ) -> JSONResponse:
        threading.Thread(target=_run_pair, args=(task_id, injection_task_id, template, suite), daemon=True).start()
        return JSONResponse({"ok": True})

    @app.post("/api/campaign")
    async def campaign(
        suite: str = "workspace",
        task_id: str = DEFAULT_DEMO_TASK,
        injection_task_id: str = DEFAULT_DEMO_INJECTION,
        max_rounds: int = 12,
    ) -> JSONResponse:
        threading.Thread(target=_run_campaign, args=(task_id, injection_task_id, suite, max_rounds), daemon=True).start()
        return JSONResponse({"ok": True})

    @app.post("/api/replay")
    async def replay(name: str = "") -> JSONResponse:
        path = config.DEMO_DIR if not name or name == "demo" else config.RUNS_DIR / name
        threading.Thread(target=_replay, args=(path,), daemon=True).start()
        return JSONResponse({"ok": True, "path": str(path)})

    @app.post("/api/reset")
    async def reset() -> JSONResponse:
        STATE.reset()
        return JSONResponse({"ok": True})

    @app.post("/api/speed")
    async def speed(value: float = 1.0) -> JSONResponse:
        STATE.speed = max(0.1, min(float(value), 32.0))
        return JSONResponse({"speed": STATE.speed})

    @app.post("/api/defense")
    async def defense(on: bool = True) -> JSONResponse:
        STATE.defense_on = bool(on)
        return JSONResponse({"defense_on": STATE.defense_on})

    @app.post("/api/threshold")
    async def threshold(value: float = 0.5) -> JSONResponse:
        STATE.threshold = max(0.0, min(float(value), 1.0))
        STATE.emit("threshold", {"value": STATE.threshold})
        return JSONResponse({"threshold": STATE.threshold})

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sentinel-Z Arena UI.")
    parser.add_argument("--host", default=config.UI_HOST)
    parser.add_argument("--port", type=int, default=config.UI_PORT)
    parser.add_argument("--replay", type=str, default="", help="replay a recording on startup")
    parser.add_argument("--panic", action="store_true", help="skip straight to the known-good recorded campaign")
    args = parser.parse_args(argv)

    import uvicorn

    info = describe()
    print(f"Arena UI on http://{args.host}:{args.port}")
    print(f"backend: {info['resolved']}  model: {info['model']}")
    if args.panic or args.replay:
        target = config.DEMO_DIR if args.panic or args.replay == "demo" else config.RUNS_DIR / args.replay

        def kick() -> None:
            time.sleep(2.0)
            _replay(target)

        threading.Thread(target=kick, daemon=True).start()
        print(f"will replay: {target}")

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="error")
    return 0


if __name__ == "__main__":
    sys.exit(main())
