"""Sentinel-Z Live — the chat app and the defense dashboard.

Two pages, one event stream. `/` is the assistant a person actually uses;
`/dashboard` is the same session seen from the defense's side. The point of
the split is that the user is never told anything is wrong — the dashboard is
where the interception is visible, which is exactly the situation the project
is about.

    python sz.py live
"""

from __future__ import annotations

import argparse
import contextlib
import queue
import threading
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from arena import config
from arena.live.agent import LiveAgent
from arena.live.workspace import Workspace
from sentinelz.broker.capability import CapabilityBroker
from sentinelz.evidence.canonical import dumps_str
from sentinelz.gateway import build_gateway

STATIC_DIR = config.REPO_ROOT / "arena" / "live" / "static"
MAX_UPLOAD_BYTES = 512_000


class LiveState:
    """One demo session. Single-user by design — this is a demo, not a service."""

    def __init__(self) -> None:
        self.subscribers: list[queue.Queue[str]] = []
        self.lock = threading.Lock()
        self.workspace = Workspace()
        self.broker = CapabilityBroker()
        self.gateway: Any = None
        self.defense_on = True
        self.busy = False
        self.agent: LiveAgent | None = None
        self.transcript: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []

    # ------------------------------------------------------------ events
    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=1000)
        with self.lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        frame = f"event: {event}\ndata: {dumps_str(payload)}\n\n"
        with self.lock:
            targets = list(self.subscribers)
        for q in targets:
            with contextlib.suppress(queue.Full):
                q.put_nowait(frame)

    # ------------------------------------------------------------- agent
    def ensure_agent(self, backend: str, model: str | None) -> LiveAgent:
        if self.agent is None:
            if self.defense_on and self.gateway is None:
                self.gateway = build_gateway(mode="pomdp")
            self.agent = LiveAgent(
                workspace=self.workspace,
                gateway=self.gateway if self.defense_on else None,
                broker=self.broker,
                backend=backend,
                model=model,
            )
        return self.agent

    def reset(self) -> None:
        self.workspace.reset()
        self.broker.reset()
        self.agent = None
        self.transcript.clear()
        self.decisions.clear()
        self.emit("reset", {})


STATE = LiveState()
BACKEND = "auto"
MODEL: str | None = None


def _run_turn(text: str) -> None:
    """Drive one turn on a worker thread, mirroring every event to the UI."""
    state = STATE
    if state.busy:
        state.emit("log", {"message": "already running"})
        return
    state.busy = True
    try:
        agent = state.ensure_agent(BACKEND, MODEL)
        state.emit(
            "backend",
            {"backend": agent.backend, "model": agent.model, "defense_on": state.defense_on},
        )
        for event, payload in agent.run_turn(text):
            if event == "decision":
                state.decisions.append(payload)
            state.emit(event, payload)
    except Exception as exc:  # a UI must not die on one bad turn
        state.emit("assistant", {"text": f"Something broke: {type(exc).__name__}: {exc}"})
        state.emit("done", {})
    finally:
        state.busy = False


def build_app() -> FastAPI:
    app = FastAPI(title="Sentinel-Z Live", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/dashboard")
    async def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "dashboard.html")

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

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/status")
    async def status() -> JSONResponse:
        agent = STATE.agent
        return JSONResponse(
            {
                "backend": agent.backend if agent else BACKEND,
                "model": agent.model if agent else (MODEL or config.OLLAMA_MODEL),
                "defense_on": STATE.defense_on,
                "busy": STATE.busy,
                "documents": sorted(STATE.workspace.documents),
                "emails": len(STATE.workspace.emails),
                "decisions": STATE.decisions[-50:],
                "attacker_url": config.ATTACKER_URL,
            }
        )

    @app.post("/api/chat")
    async def chat(payload: dict[str, Any]) -> JSONResponse:
        text = str(payload.get("text", "")).strip()
        if not text:
            return JSONResponse({"ok": False, "error": "empty message"}, status_code=400)
        STATE.emit("user", {"text": text})
        threading.Thread(target=_run_turn, args=(text,), daemon=True).start()
        return JSONResponse({"ok": True})

    @app.post("/api/upload")
    async def upload(payload: dict[str, Any]) -> JSONResponse:
        """Uploads arrive as JSON, not multipart.

        The browser already has the file as text, and posting it as JSON
        avoids a `python-multipart` dependency for no loss — every format the
        assistant can read is text anyway.
        """
        text = str(payload.get("content", ""))
        if len(text.encode("utf-8")) > MAX_UPLOAD_BYTES:
            return JSONResponse({"ok": False, "error": "file too large"}, status_code=413)
        name = str(payload.get("name") or "upload.txt")
        kind = str(payload.get("kind") or "document")

        if kind == "email":
            subject, sender, body = _parse_email(text, name)
            STATE.workspace.add_email(sender=sender, subject=subject, body=body)
            STATE.emit("upload", {"name": subject, "kind": "email"})
            return JSONResponse({"ok": True, "kind": "email", "subject": subject})

        STATE.workspace.add_document(name=name, content=text)
        STATE.emit("upload", {"name": name, "kind": "document"})
        return JSONResponse({"ok": True, "kind": "document", "name": name})

    @app.get("/api/exfil")
    async def exfil() -> JSONResponse:
        """Proxy the collection server's records.

        The dashboard cannot read `:8899` directly — different origin — and
        the alternative, showing what the agent *tried* to send, would be a
        weaker claim than showing what actually arrived.
        """
        import httpx

        try:
            response = httpx.get(f"{config.ATTACKER_URL}/records", timeout=2.0)
            response.raise_for_status()
            entries = response.json().get("entries", [])
        except Exception:
            return JSONResponse({"records": [], "reachable": False})
        records = [r for entry in entries for r in entry.get("records", [])]
        return JSONResponse({"records": records, "reachable": True})

    @app.post("/api/stepup")
    async def stepup(payload: dict[str, Any]) -> JSONResponse:
        if STATE.agent is None:
            return JSONResponse({"ok": False}, status_code=409)
        STATE.agent.resolve_step_up(bool(payload.get("approved")))
        return JSONResponse({"ok": True})

    @app.post("/api/defense")
    async def defense(payload: dict[str, Any]) -> JSONResponse:
        STATE.defense_on = bool(payload.get("on", True))
        STATE.agent = None  # a new session, so the change actually takes effect
        STATE.emit("log", {"message": f"defense {'on' if STATE.defense_on else 'OFF'} — new session"})
        return JSONResponse({"defense_on": STATE.defense_on})

    @app.post("/api/reset")
    async def reset() -> JSONResponse:
        STATE.reset()
        return JSONResponse({"ok": True})

    return app


def _parse_email(text: str, fallback_name: str) -> tuple[str, str, str]:
    """Accept a plain RFC-ish `From:/Subject:` header block, or bare text."""
    sender, subject = "unknown@external.example", fallback_name
    lines = text.splitlines()
    body_start = 0
    for index, line in enumerate(lines[:10]):
        lowered = line.lower()
        if lowered.startswith("from:"):
            sender = line.split(":", 1)[1].strip() or sender
            body_start = index + 1
        elif lowered.startswith("subject:"):
            subject = line.split(":", 1)[1].strip() or subject
            body_start = index + 1
    return subject, sender, "\n".join(lines[body_start:]).strip() or text


def main(argv: list[str] | None = None) -> int:
    global BACKEND, MODEL
    parser = argparse.ArgumentParser(description="Sentinel-Z Live.")
    parser.add_argument("--host", default=config.UI_HOST)
    parser.add_argument("--port", type=int, default=config.UI_PORT + 1)
    parser.add_argument("--backend", default="auto", choices=["auto", "llm", "scripted"])
    parser.add_argument("--model", default=None, help="override SENTINELZ_OLLAMA_MODEL")
    args = parser.parse_args(argv)

    BACKEND, MODEL = args.backend, args.model
    print(f"Sentinel-Z Live   chat: http://{args.host}:{args.port}/")
    print(f"                  dash: http://{args.host}:{args.port}/dashboard")
    print(f"              attacker: {config.ATTACKER_URL}")
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
