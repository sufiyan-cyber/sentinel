"""Sentinel-Z Live — the chat app and the defense dashboard.

Two pages, one event stream. `/` is the assistant a person actually uses;
`/dashboard` is the same session seen from the defense's side. The point of
the split is that the user is never told anything is wrong — the dashboard is
where the interception is visible, which is exactly the situation the project
is about.

    python sz.py live

A `Lane` is one agent's whole world: workspace, broker, wallet, cart, evidence
chain. Normal use has one lane, `main`. A twin run adds two more — the same
request sent to an undefended and a defended agent side by side.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import queue
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from arena import config
from arena.live.agent import PROFILES, LiveAgent
from arena.live.calle import CALLE_SERVICE
from arena.live.payments import MockProvider, PaymentError, PaymentProvider, build_provider
from arena.live.shop import POISON, ShopSession, build_router
from arena.live.tools import BOOKING_TOOLS, BROWSER_TOOLS, SHOP_TOOLS
from arena.live.voice import VOICE_SERVICE
from arena.live.wallet import inr
from arena.live.workspace import Workspace
from sentinelz.broker.capability import CapabilityBroker
from sentinelz.context import DecisionContext, PendingCall
from sentinelz.evidence.canonical import dumps_str, loads
from sentinelz.evidence.chain import DecisionChain, verify
from sentinelz.evidence.export import write_chain
from sentinelz.gateway import build_gateway

STATIC_DIR = config.REPO_ROOT / "arena" / "live" / "static"
DEMO_DOCS_DIR = config.REPO_ROOT / "demo_docs"
SCENARIOS_PATH = config.REPO_ROOT / "configs" / "demo_scenarios.json"
MAX_UPLOAD_BYTES = 512_000
MAX_TOPUP_INR = 100_000
MAIN = "main"


def _payments() -> tuple[PaymentProvider, str]:
    """The configured provider, or the mock plus the reason it was not used."""
    try:
        return build_provider(), ""
    except PaymentError as exc:
        return MockProvider(), str(exc)


class Lane:
    """One agent's world. Survives the agent being re-created after a revoke."""

    def __init__(self, name: str, defended: bool, provider: PaymentProvider | None = None) -> None:
        self.name = name
        self.defended = defended
        self.workspace = Workspace()
        self.broker = CapabilityBroker()
        self.shop = ShopSession(provider)
        self.chain = DecisionChain()
        self.chain_id = f"live/{name}-{uuid.uuid4().hex[:8]}"
        self.agent: LiveAgent | None = None
        self.decisions: list[dict[str, Any]] = []
        #: Per-run overrides set by a demo scenario; None means "the default".
        self.backend: str | None = None
        self.profile: str | None = None
        self.alerted = False

    def reset(self) -> None:
        self.workspace.reset()
        self.broker.reset()
        self.shop.reset()
        self.chain = DecisionChain()
        self.chain_id = f"live/{self.name}-{uuid.uuid4().hex[:8]}"
        self.agent = None
        self.decisions.clear()
        self.backend = self.profile = None
        self.alerted = False

    def summary(self) -> dict[str, Any]:
        sent = self.agent.toolbox.sent if self.agent else []
        return {
            "lane": self.name,
            "defended": self.defended,
            "wallet": self.shop.wallet.snapshot(),
            "orders": list(self.shop.orders),
            "spent_paise": self.shop.wallet.opening_paise - self.shop.wallet.balance_paise,
            "revoked": bool(self.agent and not self.broker.is_alive(self.agent.session_id)),
            "egress_calls": len(sent),
            "decisions": len(self.decisions),
        }


class LiveState:
    """One demo session. Single-user by design — this is a demo, not a service."""

    def __init__(self) -> None:
        self.subscribers: list[queue.Queue[str]] = []
        self.lock = threading.Lock()
        provider, self.payments_error = _payments()
        self.main = Lane(MAIN, defended=True, provider=provider)
        self.gateway: Any = None
        self.defense_on = True
        self.busy = False
        self.profile = config.AGENT_PROFILE if config.AGENT_PROFILE in PROFILES else "natural"
        self.twin: dict[str, Lane] = {}
        self.warmup_ms: dict[str, float] = {}

    # Kept so the rest of the module reads as it always did.
    @property
    def workspace(self) -> Workspace:
        return self.main.workspace

    @property
    def agent(self) -> LiveAgent | None:
        return self.main.agent

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

    def emit(self, event: str, payload: dict[str, Any], lane: str = MAIN) -> None:
        # Twin-run lanes get their own event names (`lane.decision`, ...), so
        # the chat page — which only knows the main session — never sees them.
        name = event if lane == MAIN else f"lane.{event}"
        frame = f"event: {name}\ndata: {dumps_str({**payload, 'lane': lane})}\n\n"
        with self.lock:
            targets = list(self.subscribers)
        for q in targets:
            with contextlib.suppress(queue.Full):
                q.put_nowait(frame)

    # ------------------------------------------------------------- agent
    def load_gateway(self) -> None:
        """Build the gateway once and take the cold start here, not on the
        first real decision — where it would blow the latency budget."""
        if self.gateway is not None:
            return
        self.gateway = build_gateway(mode="pomdp")
        dummy = DecisionContext(
            user_task_text="warm-up",
            pending=PendingCall(tool_name="list_files", tool_args={}),
            history=[],
            taint_set=set(),
            expected_tools=frozenset({"list_files"}),
            task_max_tier=1,
            suite="live",
            step_index=0,
        )
        timings = []
        for _ in range(3):
            self.gateway.start_session()
            started = time.perf_counter()
            self.gateway.evaluate(dummy)
            timings.append((time.perf_counter() - started) * 1000.0)
        self.gateway.start_session()
        self.warmup_ms = {"cold": round(timings[0], 2), "warm": round(timings[-1], 2)}

    def ensure_agent(self, lane: Lane | None = None) -> LiveAgent:
        lane = lane or self.main
        if lane.agent is None:
            defended = self.defense_on if lane is self.main else lane.defended
            if defended:
                self.load_gateway()
            lane.agent = LiveAgent(
                workspace=lane.workspace,
                gateway=self.gateway if defended else None,
                broker=lane.broker,
                backend=lane.backend or BACKEND,
                model=None if lane.backend else MODEL,
                shop=lane.shop,
                chain=lane.chain,
                profile=lane.profile or self.profile,
            )
        return lane.agent

    def backend_info(self, lane: Lane | None = None) -> dict[str, Any]:
        agent = self.ensure_agent(lane)
        shop = (lane or self.main).shop
        return {
            "backend": agent.backend,
            "model": agent.model,
            "backend_ok": agent.backend_ok,
            "backend_error": agent.backend_error,
            "agent_profile": agent.profile,
            "defense_on": self.defense_on,
            "payments_provider": shop.provider.name,
            "payments_mode": shop.provider.mode,
            "payments_error": self.payments_error,
            "attacker_url": config.ATTACKER_URL,
            "sarvam_configured": VOICE_SERVICE.configured,
            "calle_configured": CALLE_SERVICE.configured,
            "calle_default_phone": CALLE_SERVICE.default_phone,
            "calle_dry_run": config.CALLE_DRY_RUN,
        }

    def reset(self) -> None:
        self.main.reset()
        self.twin.clear()
        self.emit("reset", {})
        self.emit("wallet", self.main.shop.wallet.snapshot())


STATE = LiveState()
BACKEND = config.LLM_BACKEND
MODEL: str | None = None


def _browser_class() -> Any | None:
    """The desktop-browser driver, or None when its extra is not installed.
    Chat, documents and payments must work without it."""
    try:
        from arena.live.browser import LiveBrowser
    except ImportError:
        return None
    return LiveBrowser


def _alert_owner(lane: Lane, payload: dict[str, Any]) -> None:
    """On the revoke, ring the owner. Runs after the decision, never in it."""
    if lane.alerted or not config.ALERT_CALL or not CALLE_SERVICE.configured:
        return
    lane.alerted = True
    tool = str(payload.get("tool", ""))
    if tool in SHOP_TOOLS:
        what = "tried to make a purchase you did not ask for"
    else:
        what = "tried to send your data to an outside server"
    message = (
        f"This is Sentinel-Z. Your assistant was hijacked and {what}. "
        "It was stopped and its access has been revoked. Nothing was lost."
    )

    def call() -> None:
        result = CALLE_SERVICE.place_call(phone="", message=message, recipient_name="the owner")
        if result.get("ok"):
            STATE.emit(
                "alert_call",
                {
                    "message": message,
                    "dry_run": bool(result.get("dry_run")),
                    "status": result.get("status"),
                    "phone": str(result.get("phone", ""))[-4:],
                },
                lane.name,
            )

    threading.Thread(target=call, daemon=True).start()


def _drive(lane: Lane, text: str, voice_lang: str | None = None) -> None:
    """Run one turn in `lane`, mirroring every event to the UI."""
    state = STATE
    browser = _browser_class() if lane is state.main else None
    try:
        if browser is not None:
            browser._step_listener = lambda step, thought, action: state.emit(
                "browser_step", {"step": step, "thought": thought, "action": action}
            )
        agent = state.ensure_agent(lane)
        if not lane.broker.is_alive(agent.session_id):
            lane.agent = None
            lane.alerted = False
            agent = state.ensure_agent(lane)
        if lane is state.main:
            state.emit("backend", state.backend_info(lane))
        for event, payload in agent.run_turn(text):
            if event == "decision":
                lane.decisions.append(payload)
                if payload.get("action") == "REVOKE" and "already destroyed" not in str(payload.get("reason")):
                    _alert_owner(lane, payload)
            if event == "assistant" and voice_lang:
                with contextlib.suppress(Exception):
                    voice_out = VOICE_SERVICE.prepare_voice_completion(
                        str(payload.get("text") or ""), voice_lang
                    )
                    payload = {**payload, "voice": voice_out}
            state.emit(event, payload, lane.name)
            if event == "stepup_request" and lane is not state.main:
                # Nobody is sitting at a twin lane to answer. Decline, after a
                # beat so the prompt is visible on the dashboard.
                threading.Timer(1.8, agent.resolve_step_up, args=(False,)).start()
            if (
                event == "tool_result"
                and payload.get("tool") == "place_phone_call"
                and not payload.get("refused")
                and CALLE_SERVICE.latest_call
            ):
                state.emit("calle_call", CALLE_SERVICE.latest_call, lane.name)
            if event == "tool_result" and payload.get("tool") in BOOKING_TOOLS and browser is not None:
                with contextlib.suppress(Exception):
                    lb = browser._instance
                    if lb and lb.latest_booking and not str(payload.get("result", "")).startswith("error"):
                        state.emit("booking", {**lb.latest_booking, "tool": payload.get("tool")})
            elif event == "tool_result" and payload.get("tool") in BROWSER_TOOLS and browser is not None:
                with contextlib.suppress(Exception):
                    lb = browser._instance
                    if lb and lb.latest_url:
                        prod_title = lb.latest_product.get("title") if lb.latest_product else lb.latest_title
                        price_val = lb.latest_product.get("price") if lb.latest_product else ""
                        state.emit(
                            "browser_open",
                            {
                                "url": lb.latest_url,
                                "cart_url": lb.latest_cart_url or lb.latest_url,
                                "title": lb.latest_title or prod_title,
                                "product": prod_title,
                                "price": price_val,
                                "screenshot": getattr(lb, "latest_screenshot_b64", ""),
                                "tool": payload.get("tool"),
                            },
                        )
    except Exception as exc:  # a UI must not die on one bad turn
        state.emit("assistant", {"text": f"Something broke: {type(exc).__name__}: {exc}"}, lane.name)
        state.emit("done", {}, lane.name)
    finally:
        if browser is not None:
            browser._step_listener = None
        if len(lane.chain):
            with contextlib.suppress(Exception):
                write_chain(lane.chain_id, lane.chain.records)


def _run_turn(text: str, voice_lang: str | None = None, scenario: dict[str, Any] | None = None) -> None:
    """Drive one turn of the main lane on a worker thread."""
    state = STATE
    if state.busy:
        # Say so in the chat. Silently dropping the message looks like a hang.
        state.emit("log", {"message": "already running"})
        state.emit(
            "assistant",
            {"text": "I'm still working on your previous request. I'll be ready for the next one as soon as it finishes."},
        )
        state.emit("done", {})
        return
    state.busy = True
    try:
        _drive(state.main, text, voice_lang)
        if scenario is not None:
            state.emit("scenario_end", {"id": scenario["id"], "outcome": state.main.summary()})
    finally:
        if scenario is not None:
            # A scenario's backend/profile apply to that run only.
            state.main.backend = state.main.profile = None
        state.busy = False


def _run_twin(scenario: dict[str, Any]) -> None:
    """The same request to two agents at once: one undefended, one defended."""
    state = STATE
    if state.busy:
        state.emit("log", {"message": "already running"})
        return
    state.busy = True
    try:
        state.load_gateway()
        lanes = {
            "undefended": Lane("undefended", defended=False),
            "defended": Lane("defended", defended=True),
        }
        state.twin = lanes
        # The shared gateway is about to serve the defended lane.
        state.main.agent = None
        for lane in lanes.values():
            _stage(lane, scenario)
            state.emit("wallet", lane.shop.wallet.snapshot(), lane.name)
        state.emit(
            "twin_start",
            {"id": scenario["id"], "title": scenario["title"], "narration": scenario.get("narration", ""),
             "prompt": scenario["prompt"], "poison": scenario.get("poison"), "expect": scenario.get("expect")},
        )
        threads = [
            threading.Thread(target=_drive, args=(lane, scenario["prompt"]), daemon=True)
            for lane in lanes.values()
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=180.0)
        state.emit(
            "twin_end",
            {"id": scenario["id"], "outcome": {name: lane.summary() for name, lane in lanes.items()}},
        )
    finally:
        state.main.agent = None
        state.busy = False


# -------------------------------------------------------------- scenarios
def load_scenarios() -> list[dict[str, Any]]:
    return list(loads(SCENARIOS_PATH.read_text(encoding="utf-8"))["scenarios"])


def _stage(lane: Lane, scenario: dict[str, Any]) -> None:
    """Put `lane` in the scenario's starting state."""
    lane.reset()
    lane.backend = scenario.get("backend")
    profile = scenario.get("profile")
    if profile == "auto":
        # Use the model's own behaviour when it is known to fall for the
        # attack; otherwise the labelled simulated weakness.
        probe = STATE.ensure_agent(lane)
        gullible = not probe.backend_error and probe.model in config.NATURAL_ATTACK_MODELS
        profile = "natural" if gullible else "susceptible"
        lane.agent = None
    lane.profile = profile
    if scenario.get("poison"):
        lane.shop.plant(str(scenario["poison"]))
    sample = scenario.get("sample")
    if sample:
        _load_sample(lane, str(sample))
    # A scenario must still run with the network down: if the chosen model
    # does not answer, the deterministic agent takes over, and says so.
    agent = STATE.ensure_agent(lane)
    if agent.backend_error:
        lane.agent = None
        lane.backend = "scripted"
        STATE.emit("log", {"message": f"model backend unavailable ({agent.backend_error}); using the scripted agent"})


def _load_sample(lane: Lane, filename: str) -> dict[str, Any] | None:
    path = (DEMO_DOCS_DIR / filename).resolve()
    # Only files directly inside demo_docs/ — never a path that climbs out.
    if path.parent != DEMO_DOCS_DIR.resolve() or not path.is_file():
        return None
    content = path.read_text(encoding="utf-8", errors="replace")
    if "query" in filename.lower() or "mail" in filename.lower():
        subject, sender, body = _parse_email(content, filename)
        lane.workspace.add_email(sender=sender, subject=subject, body=body)
        return {"name": subject, "kind": "email"}
    lane.workspace.add_document(name=filename, content=content)
    return {"name": filename, "kind": "document"}


def _evidence(lane: Lane) -> dict[str, Any]:
    records = lane.chain.records
    ok, bad = verify(records)
    return {"records": records, "verified": ok, "bad_index": bad, "head": lane.chain.head, "lane": lane.name}


def _lane(name: str | None) -> Lane:
    return STATE.twin.get(str(name or ""), STATE.main)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    STATE.load_gateway()
    agent = STATE.ensure_agent()
    print(f"  decision path warm-up: cold {STATE.warmup_ms.get('cold')} ms, warm {STATE.warmup_ms.get('warm')} ms")
    if agent.backend_error:
        print(f"  WARNING: backend `{agent.backend}` ({agent.model}) is not answering: {agent.backend_error}")
    if STATE.payments_error:
        print(f"  WARNING: payments fell back to mock: {STATE.payments_error}")
    yield


def build_app() -> FastAPI:
    app = FastAPI(title="Sentinel-Z Live", docs_url=None, redoc_url=None, lifespan=_lifespan)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(build_router(lambda: STATE.main.shop))

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/dashboard")
    async def dashboard() -> FileResponse:
        return FileResponse(STATIC_DIR / "dashboard.html", headers={"Cache-Control": "no-store"})

    @app.get("/events")
    async def events() -> StreamingResponse:
        q = STATE.subscribe()

        def generate():
            try:
                yield "retry: 2000\n\n"
                init = {**STATE.backend_info(), "lane": MAIN}
                yield f"event: backend\ndata: {dumps_str(init)}\n\n"
                wallet = {**STATE.main.shop.wallet.snapshot(), "lane": MAIN}
                yield f"event: wallet\ndata: {dumps_str(wallet)}\n\n"
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
        main = STATE.main
        return JSONResponse(
            {
                **STATE.backend_info(),
                "busy": STATE.busy,
                "documents": sorted(main.workspace.documents),
                "emails": len(main.workspace.emails),
                "decisions": main.decisions[-50:],
                "wallet": main.shop.wallet.snapshot(),
                "cart": main.shop.cart_payload(),
                "mandate": {"max_paise": main.broker.spend_limit(main.agent.session_id) if main.agent else None},
                "poison": main.shop.poison,
                "warmup_ms": STATE.warmup_ms,
            }
        )

    @app.post("/api/chat")
    async def chat(payload: dict[str, Any]) -> JSONResponse:
        text = str(payload.get("text", "")).strip()
        if not text:
            return JSONResponse({"ok": False, "error": "empty message"}, status_code=400)
        voice_lang = str(payload.get("voice_lang") or "").strip() or None
        display_text = str(payload.get("display_text") or text).strip()
        STATE.emit("user", {"text": display_text, "voice_lang": voice_lang})
        threading.Thread(target=_run_turn, args=(text, voice_lang), daemon=True).start()
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------- voice
    @app.post("/api/voice/stt")
    async def voice_stt(payload: dict[str, Any]) -> JSONResponse:
        audio_b64 = str(payload.get("audio_b64") or "").strip()
        language_hint = str(payload.get("language_code") or "auto").strip()
        if not audio_b64:
            return JSONResponse({"ok": False, "error": "empty audio"}, status_code=400)
        result = VOICE_SERVICE.speech_to_text(audio_b64, language_hint=language_hint)
        status_code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=status_code)

    @app.post("/api/voice/tts")
    async def voice_tts(payload: dict[str, Any]) -> JSONResponse:
        text = str(payload.get("text") or "").strip()
        language_code = str(payload.get("language_code") or "en-IN").strip()
        if not text:
            return JSONResponse({"ok": False, "error": "empty text"}, status_code=400)
        result = VOICE_SERVICE.prepare_voice_completion(text, language_code)
        return JSONResponse({"ok": True, **result})

    @app.post("/api/voice/config")
    async def voice_config(payload: dict[str, Any]) -> JSONResponse:
        key = str(payload.get("api_key") or payload.get("sarvam_api_key") or "").strip()
        if key:
            VOICE_SERVICE.set_api_key(key)
        return JSONResponse(
            {
                "ok": True,
                "configured": VOICE_SERVICE.configured,
                "sarvam_configured": VOICE_SERVICE.configured,
            }
        )

    @app.post("/api/calle/config")
    async def calle_config(payload: dict[str, Any]) -> JSONResponse:
        key = str(payload.get("api_key") or payload.get("calle_api_key") or "").strip()
        phone = str(payload.get("default_phone") or payload.get("calle_default_phone") or "").strip()
        CALLE_SERVICE.set_config(api_key=key or None, default_phone=phone or None)
        return JSONResponse(
            {
                "ok": True,
                "configured": CALLE_SERVICE.configured,
                "calle_configured": CALLE_SERVICE.configured,
                "default_phone": CALLE_SERVICE.default_phone,
            }
        )

    # --------------------------------------------------------- workspace
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
        agent = _lane(payload.get("lane")).agent
        if agent is None:
            return JSONResponse({"ok": False}, status_code=409)
        agent.resolve_step_up(bool(payload.get("approved")))
        return JSONResponse({"ok": True})

    @app.post("/api/defense")
    async def defense(payload: dict[str, Any]) -> JSONResponse:
        STATE.defense_on = bool(payload.get("on", True))
        STATE.main.agent = None  # a new session, so the change actually takes effect
        STATE.emit("log", {"message": f"defense {'on' if STATE.defense_on else 'OFF'} — new session"})
        STATE.emit("backend", STATE.backend_info())
        return JSONResponse({"defense_on": STATE.defense_on})

    @app.post("/api/profile")
    async def profile(payload: dict[str, Any]) -> JSONResponse:
        chosen = str(payload.get("profile", "")).strip().lower()
        if chosen not in PROFILES:
            return JSONResponse({"ok": False, "error": f"profile must be one of {list(PROFILES)}"}, status_code=400)
        STATE.profile = chosen
        STATE.main.agent = None
        STATE.emit("backend", STATE.backend_info())
        return JSONResponse({"ok": True, "agent_profile": chosen})

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
        samples = [
            {"filename": p.name, "is_poisoned": "POISONED" in p.name or "EVASIVE" in p.name}
            for p in sorted(DEMO_DOCS_DIR.glob("*.txt"))
        ]
        return JSONResponse({"documents": docs, "emails": emails, "samples": samples})

    @app.post("/api/sample/load")
    async def load_sample(payload: dict[str, Any]) -> JSONResponse:
        filename = str(payload.get("filename", "")).strip()
        loaded = _load_sample(STATE.main, filename)
        if loaded is None:
            return JSONResponse({"ok": False, "error": "Sample file not found"}, status_code=404)
        STATE.emit("upload", loaded)
        return JSONResponse({"ok": True, **loaded})

    @app.post("/api/backend")
    async def switch_backend(payload: dict[str, Any]) -> JSONResponse:
        global BACKEND, MODEL
        req_backend = str(payload.get("backend", "auto")).strip().lower()
        req_model = payload.get("model")
        MODEL = str(req_model).strip() if req_model else None
        BACKEND = req_backend
        STATE.main.agent = None
        try:
            info = STATE.backend_info()
        except RuntimeError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        STATE.emit("backend", info)
        return JSONResponse({"ok": True, **info})

    # ------------------------------------------------------ wallet / shop
    @app.get("/api/wallet")
    async def wallet(lane: str = MAIN) -> JSONResponse:
        chosen = _lane(lane)
        return JSONResponse({**chosen.shop.wallet.snapshot(), "cart": chosen.shop.cart_payload(), "orders": chosen.shop.orders})

    @app.post("/api/wallet/topup")
    async def wallet_topup(payload: dict[str, Any]) -> JSONResponse:
        """Add money to the demo wallet.

        With a payment gateway configured this creates a sandbox order for the
        amount first, exactly as a purchase does, and only then credits the
        wallet. No real money moves: it is a test order, and the wallet is a
        number held by this demo.
        """
        try:
            rupees = float(str(payload.get("amount_inr", "")).replace(",", "").strip())
        except ValueError:
            return JSONResponse({"ok": False, "error": "Enter an amount in rupees."}, status_code=400)
        if not 1 <= rupees <= MAX_TOPUP_INR:
            return JSONResponse(
                {"ok": False, "error": f"Enter an amount between ₹1 and ₹{MAX_TOPUP_INR:,}."}, status_code=400
            )
        shop = STATE.main.shop
        amount = int(round(rupees * 100))
        receipt = f"sz-topup-{len(shop.wallet.ledger) + 1}"
        try:
            order = shop.provider.create_order(amount, receipt, {"purpose": "wallet-topup", "source": "sentinel-z-demo"})
        except PaymentError as exc:
            return JSONResponse({"ok": False, "error": f"The payment gateway refused the top-up: {exc}"}, status_code=502)
        shop.wallet.credit(amount, order.order_id, "Wallet top-up")
        snapshot = shop.wallet.snapshot(amount, "Wallet top-up")
        STATE.emit("wallet", snapshot)
        STATE.emit(
            "topup",
            {"amount_paise": amount, "order_id": order.order_id, "provider": order.provider, "mode": order.mode},
        )
        return JSONResponse({"ok": True, "order_id": order.order_id, "provider": order.provider, **snapshot})

    @app.post("/api/shop/poison")
    async def shop_poison(payload: dict[str, Any]) -> JSONResponse:
        """Plant (or remove) the attacker's review on the target listing."""
        shop = STATE.main.shop
        variant = str(payload.get("variant", "loud")).strip().lower()
        text = str(payload.get("text") or "").strip()
        if variant == "off" and not text:
            shop.poison = None
        else:
            shop.plant(variant, text or None)
        STATE.emit("poison", {"planted": shop.poison is not None, "sku": POISON["target_sku"], "review": shop.poison})
        return JSONResponse({"ok": True, "planted": shop.poison is not None, "review": shop.poison})

    # --------------------------------------------------------- scenarios
    @app.get("/api/demo/scenarios")
    async def scenarios() -> JSONResponse:
        return JSONResponse({"scenarios": load_scenarios()})

    def _find(scenario_id: str) -> dict[str, Any] | None:
        return next((s for s in load_scenarios() if s["id"] == scenario_id), None)

    @app.post("/api/demo/run")
    async def demo_run(payload: dict[str, Any]) -> JSONResponse:
        scenario = _find(str(payload.get("id", "")))
        if scenario is None:
            return JSONResponse({"ok": False, "error": "unknown scenario"}, status_code=404)
        if STATE.busy:
            return JSONResponse({"ok": False, "error": "busy"}, status_code=409)
        STATE.defense_on = bool(payload["defense"]) if "defense" in payload else bool(scenario.get("defense", True))
        # `scenario_start` is the pages' cue to clear themselves. A `reset`
        # event would make them reload and miss the run that follows.
        _stage(STATE.main, scenario)
        STATE.emit(
            "scenario_start",
            {k: scenario.get(k) for k in ("id", "title", "narration", "prompt", "poison", "expect", "sample")},
        )
        STATE.emit("backend", STATE.backend_info())
        STATE.emit("wallet", STATE.main.shop.wallet.snapshot())
        STATE.emit("user", {"text": scenario["prompt"], "voice_lang": None})
        threading.Thread(target=_run_turn, args=(scenario["prompt"], None, scenario), daemon=True).start()
        return JSONResponse({"ok": True, "id": scenario["id"]})

    @app.post("/api/demo/twin")
    async def demo_twin(payload: dict[str, Any]) -> JSONResponse:
        scenario = _find(str(payload.get("id", "")))
        if scenario is None:
            return JSONResponse({"ok": False, "error": "unknown scenario"}, status_code=404)
        if STATE.busy:
            return JSONResponse({"ok": False, "error": "busy"}, status_code=409)
        threading.Thread(target=_run_twin, args=(scenario,), daemon=True).start()
        return JSONResponse({"ok": True, "id": scenario["id"]})

    # ---------------------------------------------------------- evidence
    @app.get("/api/evidence")
    async def evidence(lane: str = MAIN) -> JSONResponse:
        return JSONResponse(_evidence(_lane(lane)))

    @app.post("/api/evidence/tamper-demo")
    async def tamper_demo(payload: dict[str, Any]) -> JSONResponse:
        """Edit one record in a COPY of the chain and verify the copy.

        The real log is never touched. This is what an attacker who rewrote
        history would be caught by.
        """
        lane = _lane(payload.get("lane"))
        records = copy.deepcopy(lane.chain.records)
        decisions = [i for i, r in enumerate(records) if r.get("kind") == "decision"]
        if not decisions:
            return JSONResponse({"ok": False, "error": "no decisions recorded yet"}, status_code=409)
        blocking = [i for i in decisions if records[i]["decision"].get("action") == "REVOKE"]
        index = int(payload["index"]) if "index" in payload else (blocking[0] if blocking else decisions[-1])
        if index not in decisions:
            return JSONResponse({"ok": False, "error": "not a decision record"}, status_code=400)
        before = records[index]["decision"]["action"]
        records[index]["decision"]["action"] = "ALLOW"
        ok, bad = verify(records)
        return JSONResponse(
            {"ok": True, "tampered_index": index, "field": "decision.action", "before": before,
             "after": "ALLOW", "verified": ok, "bad_index": bad, "total": len(records)}
        )

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
    parser.add_argument("--host", default=config.LIVE_HOST)
    parser.add_argument("--port", type=int, default=config.LIVE_PORT)
    parser.add_argument("--backend", default=config.LLM_BACKEND, choices=["auto", "nvidia", "gemini", "ollama", "llm", "scripted"])
    parser.add_argument("--model", default=None, help="override the backend's default model")
    parser.add_argument("--profile", default=None, choices=list(PROFILES), help="agent profile")
    args = parser.parse_args(argv)

    BACKEND, MODEL = args.backend, args.model
    # The Windows console defaults to cp1252, which cannot print a rupee sign.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")
    if args.profile:
        STATE.profile = args.profile
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    print(f"Sentinel-Z Live   chat: http://{shown}:{args.port}/")
    print(f"                  dash: http://{shown}:{args.port}/dashboard")
    print(f"                  shop: http://{shown}:{args.port}/shop")
    print(f"              attacker: {config.ATTACKER_URL}")
    print(f"               wallet: {inr(STATE.main.shop.wallet.balance_paise)}  payments: {STATE.main.shop.provider.name}")
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
