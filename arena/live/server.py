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
import os
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
BACKEND = config.LLM_BACKEND
MODEL: str | None = None


def _run_turn(text: str) -> None:
    """Drive one turn on a worker thread, mirroring every event to the UI."""
    state = STATE
    if state.busy:
        state.emit("log", {"message": "already running"})
        return
    state.busy = True
    try:
        from arena.live.browser import LiveBrowser
        LiveBrowser._step_listener = lambda step, thought, action: state.emit(
            "browser_step", {"step": step, "thought": thought, "action": action}
        )
        agent = state.ensure_agent(BACKEND, MODEL)
        state.emit(
            "backend",
            {"backend": agent.backend, "model": agent.model, "defense_on": state.defense_on},
        )
        for event, payload in agent.run_turn(text):
            if event == "decision":
                state.decisions.append(payload)
            state.emit(event, payload)
            if event == "tool_result" and payload.get("tool") in (
                "browser_add_to_cart", "browser_search", "browse_website", "autonomous_browse"
            ):
                try:
                    from arena.live.browser import LiveBrowser
                    lb = LiveBrowser._instance
                    if lb and lb.latest_url:
                        prod_title = lb.latest_product.get("title") if lb.latest_product else lb.latest_title
                        price_val = lb.latest_product.get("price") if lb.latest_product else ""
                        state.emit("browser_open", {
                            "url": lb.latest_url,
                            "cart_url": lb.latest_cart_url or lb.latest_url,
                            "title": lb.latest_title or prod_title,
                            "product": prod_title,
                            "price": price_val,
                            "screenshot": getattr(lb, "latest_screenshot_b64", ""),
                            "tool": payload.get("tool"),
                        })
                except Exception:
                    pass
    except Exception as exc:  # a UI must not die on one bad turn
        state.emit("assistant", {"text": f"Something broke: {type(exc).__name__}: {exc}"})
        state.emit("done", {})
    finally:
        with contextlib.suppress(Exception):
            from arena.live.browser import LiveBrowser
            LiveBrowser._step_listener = None
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
        agent = STATE.ensure_agent(BACKEND, MODEL)
        return JSONResponse(
            {
                "backend": agent.backend,
                "model": agent.model,
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

    @app.get("/api/workspace/items")
    async def workspace_items() -> JSONResponse:
        docs = [
            {"name": doc.name, "preview": doc.preview, "size": len(doc.content)}
            for doc in STATE.workspace.documents.values()
        ]
        emails = [
            {
                "id": email.email_id,
                "sender": email.sender,
                "subject": email.subject,
                "preview": email.body[:120],
                "unread": email.unread,
            }
            for email in STATE.workspace.emails.values()
        ]
        demo_dir = config.REPO_ROOT / "demo_docs"
        samples = []
        if demo_dir.exists():
            for p in sorted(demo_dir.glob("*.txt")):
                samples.append({
                    "filename": p.name,
                    "is_poisoned": "POISONED" in p.name or "EVASIVE" in p.name,
                })
        return JSONResponse({"documents": docs, "emails": emails, "samples": samples})

    @app.post("/api/sample/load")
    async def load_sample(payload: dict[str, Any]) -> JSONResponse:
        filename = str(payload.get("filename", "")).strip()
        demo_path = config.REPO_ROOT / "demo_docs" / filename
        if not demo_path.exists() or not demo_path.is_file():
            return JSONResponse({"ok": False, "error": "Sample file not found"}, status_code=404)
        content = demo_path.read_text(encoding="utf-8", errors="replace")
        if "query" in filename.lower() or "mail" in filename.lower():
            subj, sender, body = _parse_email(content, filename)
            STATE.workspace.add_email(sender=sender, subject=subj, body=body)
            STATE.emit("upload", {"name": subj, "kind": "email"})
        else:
            STATE.workspace.add_document(name=filename, content=content)
            STATE.emit("upload", {"name": filename, "kind": "document"})
        return JSONResponse({"ok": True, "name": filename})

    @app.post("/api/backend")
    async def switch_backend(payload: dict[str, Any]) -> JSONResponse:
        global BACKEND, MODEL
        req_backend = str(payload.get("backend", "auto")).strip().lower()
        req_model = payload.get("model")
        if req_model:
            MODEL = str(req_model).strip()
        BACKEND = req_backend
        STATE.agent = None
        agent = STATE.ensure_agent(BACKEND, MODEL)
        STATE.emit(
            "backend",
            {"backend": agent.backend, "model": agent.model, "defense_on": STATE.defense_on},
        )
        return JSONResponse({"ok": True, "backend": agent.backend, "model": agent.model})

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
    port_default = int(os.environ.get("PORT", str(config.UI_PORT + 1)))
    host_default = os.environ.get("HOST", "0.0.0.0")
    parser.add_argument("--host", default=host_default)
    parser.add_argument("--port", type=int, default=port_default)
    parser.add_argument("--backend", default=config.LLM_BACKEND, choices=["auto", "nvidia", "gemini", "ollama", "llm", "scripted"])
    parser.add_argument("--model", default=None, help="override SENTINELZ_OLLAMA_MODEL")
    args = parser.parse_args(argv)

    BACKEND, MODEL = args.backend, args.model
    print(f"Sentinel-Z Live   chat: http://{args.host}:{args.port}/")
    print(f"                  dash: http://{args.host}:{args.port}/dashboard")
    print(f"              attacker: {config.ATTACKER_URL}")
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
