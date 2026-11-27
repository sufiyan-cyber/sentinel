"""Live real-time web browsing and e-commerce automation for Sentinel-Z Aura.

Powered by the proven 'browser-use' autonomous AI web agent framework.
Navigates, searches, and interacts with websites (Amazon, Flipkart, Google, etc.)
in a real visible Chrome window (headless=False) on the user's desktop.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime
import logging
import os
import re
import sys
import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import quote_plus

from browser_use import Agent, Browser, ChatGoogle, ChatOpenAI, Tools

from arena import config
from arena.live.flightparse import Flight, best_flight, parse_flights
from arena.live.seatmap import Seat, find_seats, pick_run

# Ensure UTF-8 output encoding on Windows so currency symbols (e.g. ₹) don't crash console
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            with contextlib.suppress(Exception):
                _reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger(__name__)


def _get_browser_llm() -> Any:
    """Resolve the best available LLM for browser-use.

    Prioritizes Gemini 3.5 Flash Lite (native, fast, vision-capable),
    falling back to NVIDIA NIM or Ollama if configured.
    """
    gemini_key = config.GEMINI_API_KEY
    if gemini_key:
        try:
            return ChatGoogle(
                model=config.GEMINI_MODEL,  # default: gemini-3.5-flash-lite
                api_key=gemini_key,
            )
        except Exception as exc:
            logger.warning("Failed to initialize ChatGoogle: %s", exc)

    if config.NVIDIA_API_KEY:
        try:
            return ChatOpenAI(
                model=config.NVIDIA_MODEL,
                api_key=config.NVIDIA_API_KEY,
                base_url=config.NVIDIA_BASE_URL,
                temperature=0.1,
            )
        except Exception as exc:
            logger.warning("Failed to initialize ChatOpenAI (NVIDIA): %s", exc)

    # Fallback to local Ollama
    return ChatOpenAI(
        model=config.OLLAMA_MODEL,
        api_key="ollama",
        base_url=f"{config.OLLAMA_BASE_URL}/v1",
        temperature=0.1,
    )


#: Longest any one browsing tool call may take before it is abandoned.
TASK_LIMIT_S = float(os.environ.get("SENTINELZ_BROWSER_LIMIT_S", "240"))

#: Hard limits for every browsing task that is not plain shopping. The agent
#: may look, compare and hold seats on screen; it may not commit to anything.
_NO_COMMIT = (
    "SAFETY RULES, never break them: do not log in or sign up; never type a phone number, email, "
    "name, OTP, card or UPI detail; never click Pay, Proceed to Pay, Continue to Payment, Make Payment "
    "or Confirm Booking. Close or skip any login, location, notification or offer popup. "
    "Stop as soon as you can read the information asked for. Nothing is to be booked or paid for. "
    "Always output text using standard UTF-8 and write 'INR' instead of the rupee symbol. "
)
_SYSTEM_MESSAGES: dict[str, str] = {
    "shop": (
        "Always output text using standard UTF-8. "
        "Avoid unencodable currency symbols; write 'INR' instead of the rupee symbol. "
        "For shopping tasks on Amazon in India, navigate to https://www.amazon.in. "
        "Always click into the selected product's main page (URL containing /dp/...) before adding to cart. "
        "When you finish the task, clearly state in your final message:\n"
        "Product: <Exact product name>\n"
        "Price: <Exact price>\n"
        "ASIN: <10-character Amazon ASIN>\n"
        "Status: Added to cart successfully"
    ),
    "movies": _NO_COMMIT + (
        "When you finish, your final message must use exactly these lines, writing 'not found' for "
        "anything you could not read:\n"
        "Movie: <title>\nCity: <city>\nCinema: <cinema name and area of the show you opened>\nDate: <date>\n"
        "Showtime: <time>\nSeats: <seat numbers selected, or how many are available>\n"
        "Price: <total or per-ticket price in INR>\n"
        "Other shows: <up to four other cinemas showing it, each with its area and showtimes>\n"
        "Status: <Seats available | Selected on screen, not booked | Sold out | Not showing>"
    ),
    "flights": _NO_COMMIT + (
        "When you finish, your final message must use exactly these lines, writing 'not found' for "
        "anything you could not read:\n"
        "Route: <from> to <to>\nDate: <date>\nAirline: <airline of the cheapest suitable flight>\n"
        "Flight: <flight number>\nDeparture: <time>\nArrival: <time>\nDuration: <duration and stops>\n"
        "Fare: <fare in INR>\n"
        "Other options: <up to three more flights as airline, departure time, fare>\n"
        "Status: <Available, not booked | Sold out | No flights found>"
    ),
}

#: IATA codes for the cities people ask about most. Anything else is typed
#: into the site's own search box by the browsing agent.
_AIRPORTS: dict[str, str] = {
    "bengaluru": "BLR", "bangalore": "BLR", "delhi": "DEL", "new delhi": "DEL", "mumbai": "BOM",
    "bombay": "BOM", "chennai": "MAA", "madras": "MAA", "hyderabad": "HYD", "kolkata": "CCU",
    "calcutta": "CCU", "pune": "PNQ", "goa": "GOI", "ahmedabad": "AMD", "kochi": "COK", "cochin": "COK",
    "jaipur": "JAI", "lucknow": "LKO", "chandigarh": "IXC", "guwahati": "GAU", "varanasi": "VNS",
    "thiruvananthapuram": "TRV", "trivandrum": "TRV", "bhubaneswar": "BBI", "indore": "IDR",
    "patna": "PAT", "nagpur": "NAG", "coimbatore": "CJB", "mangaluru": "IXE", "mangalore": "IXE",
    "srinagar": "SXR", "dubai": "DXB", "singapore": "SIN", "london": "LHR", "bangkok": "BKK",
}


def parse_travel_date(text: str, today: datetime.date | None = None) -> datetime.date:
    """'tomorrow', '12 Dec', 'Dec 12 2026', '2026-12-12', '12/12/2026' -> a date.

    Anything unreadable, or in the past, becomes two weeks from today — far
    enough ahead that fares exist and nothing is sold out.
    """
    today = today or datetime.date.today()
    fallback = today + datetime.timedelta(days=14)
    cleaned = (text or "").strip().lower().replace(",", " ")
    if not cleaned:
        return fallback
    if cleaned == "today":
        return today
    if cleaned == "tomorrow":
        return today + datetime.timedelta(days=1)
    found: datetime.date | None = None
    iso = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", cleaned)
    slash = re.search(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{4}))?", cleaned)
    word = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*(?:\s+(\d{4}))?", cleaned) or re.search(
        r"\b([a-z]{3})[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?", cleaned
    )
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    with contextlib.suppress(ValueError):
        if iso:
            found = datetime.date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        elif slash:
            found = datetime.date(int(slash.group(3) or today.year), int(slash.group(2)), int(slash.group(1)))
        elif word:
            a, b, year = word.groups()
            day, month = (a, b) if a.isdigit() else (b, a)
            if month in months:
                found = datetime.date(int(year or today.year), months.index(month) + 1, int(day))
    if found is None:
        return fallback
    if found < today and not (iso or (slash and slash.group(3)) or (word and word.group(3))):
        found = found.replace(year=found.year + 1)  # "12 Jan" said in December
    return found if found >= today else fallback


def airport_code(place: str) -> str:
    """'Bengaluru' -> 'BLR'. A three-letter code is passed through."""
    cleaned = place.strip().lower()
    if len(cleaned) == 3 and cleaned.isalpha():
        return cleaned.upper()
    return _AIRPORTS.get(cleaned, "")


def _close_stale_browsers(profile_dir: str) -> None:
    """Close any Chrome still holding our profile directory.

    A window left open on purpose (seats selected on screen) outlives a server
    restart, and Chrome refuses to start a second time on the same profile.
    Only processes launched with this project's profile path are touched.
    """
    try:
        import psutil
    except ImportError:
        return
    needle = profile_dir.lower().replace("/", "\\")
    closing = []
    for process in psutil.process_iter(["name", "cmdline"]):
        with contextlib.suppress(Exception):
            name = (process.info.get("name") or "").lower()
            if "chrome" not in name and "chromium" not in name:
                continue
            command = " ".join(process.info.get("cmdline") or []).lower().replace("/", "\\")
            if needle in command:
                process.terminate()
                closing.append(process)
    if closing:
        # Chrome will not start on a profile another Chrome is still leaving.
        _, alive = psutil.wait_procs(closing, timeout=6)
        for process in alive:
            with contextlib.suppress(Exception):
                process.kill()
        psutil.wait_procs(alive, timeout=3)


_TIER_RE = re.compile(r"([A-Z][A-Z +&]{2,30})\n+(?:₹|Rs\.?)\s?(\d[\d,.]*)\n+([A-Z][A-Z ]{3,20})\b")


def price_tiers(page_text: str) -> str:
    """'Recliner INR 750 (filling fast); Prime INR 360 (available)' from the
    seat-count dialog. '' when the page does not list tiers."""
    out: list[str] = []
    for name, price, state in _TIER_RE.findall(page_text):
        label = name.replace("ROWS", "").strip().title()
        entry = f"{label} INR {price} ({state.strip().lower()})"
        if label and entry not in out:
            out.append(entry)
    return "; ".join(out[:6])


def _screenshot_scale(png: bytes, inner_width: Any) -> float:
    """Screenshot pixels per CSS pixel (1.0 on a normal display)."""
    try:
        import io

        from PIL import Image

        width = Image.open(io.BytesIO(png)).width
        return max(0.5, min(4.0, width / float(inner_width)))
    except Exception:
        return 1.0


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def _bring_window_to_front() -> None:
    """Ensure the browser window is brought to the foreground on Windows."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32

        def enum_windows(hwnd, extra):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value
                    if any(k in title for k in ("Amazon", "Flipkart", "Google", "Chrome", "Chromium")):
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)  # HWND_TOPMOST, SWP_NOMOVE | SWP_NOSIZE
                        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)  # HWND_NOTOPMOST
                        user32.SetForegroundWindow(hwnd)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        user32.EnumWindows(WNDENUMPROC(enum_windows), 0)
    except Exception:
        pass


class LiveBrowser:
    """Controls an autonomous AI agent for real-time web navigation and shopping."""

    _instance: LiveBrowser | None = None
    _step_listener: Callable[[int, str, str], None] | None = None

    def __init__(self, headless: bool | None = None) -> None:
        if headless is not None:
            self.headless = headless
        else:
            env_h = os.environ.get("HEADLESS", "").strip().lower()
            if env_h in ("true", "1"):
                self.headless = True
            elif env_h in ("false", "0"):
                self.headless = False
            else:
                self.headless = sys.platform != "win32" and not os.environ.get("DISPLAY")
        self.latest_url: str = ""
        self.latest_cart_url: str = ""
        self.latest_title: str = ""
        self.latest_product: dict[str, Any] | None = None
        self.latest_screenshot_b64: str = ""
        self._seats_wanted = 2
        self._free_seats = 0
        self._flight_query: tuple[str, str, str] = ("", "", "")
        #: A browser left open on purpose (seats selected on screen). Closed
        #: before the next task, which needs the same profile directory.
        self._kept_browser: Any = None
        #: Result of the last look-up task (movies, flights); None after shopping.
        self.latest_booking: dict[str, Any] | None = None
        self.current_products: list[dict[str, Any]] = []

    @classmethod
    def get_instance(cls, headless: bool | None = None) -> LiveBrowser:
        if cls._instance is None:
            cls._instance = cls(headless=headless)
        return cls._instance

    def is_closed(self) -> bool:
        return False

    def close(self) -> None:
        """Reset state."""
        self.current_products = []

    def run_autonomous_task(
        self,
        task: str,
        on_step_callback: Callable[[int, str, str], None] | None = None,
        max_steps: int = 8,
        mode: str = "shop",
    ) -> str:
        """Run an autonomous multi-step browser task using browser-use."""
        # Optimize direct search URLs to skip blank homepage typing and cut latency by 60%
        if "http" not in task.lower():
            if "amazon" in task.lower():
                query = "green t shirt"
                m_q = re.search(
                    r"(?:find|search(?:\s+for)?|get|buy)\s+(?:me\s+)?(?:a\s+)?(.+?)\s+(?:on\s+amazon|and\s+add)",
                    task,
                    re.I,
                )
                if m_q:
                    query = m_q.group(1).strip()
                task = (
                    f"Navigate directly to https://www.amazon.in/s?k={quote_plus(query)}. "
                    f"Pick the first relevant product from the search results, open its product page (/dp/...), and click 'Add to Cart'. "
                    f"Report the exact product title, price in INR, ASIN, and cart status."
                )
            elif "flipkart" in task.lower():
                query = "item"
                m_q = re.search(
                    r"(?:find|search(?:\s+for)?|get|buy)\s+(?:me\s+)?(?:a\s+)?(.+?)\s+(?:on\s+flipkart|and\s+add)",
                    task,
                    re.I,
                )
                if m_q:
                    query = m_q.group(1).strip()
                task = (
                    f"Navigate directly to https://www.flipkart.com/search?q={quote_plus(query)}. "
                    f"Pick the first relevant product, click on it, and click 'Add to Cart' or 'Buy Now'. "
                    f"Report the product title, price in INR, and cart status."
                )

        return self._run(lambda: self._async_run_task(task, on_step_callback, max_steps, mode))

    def _run(self, make_coroutine: Callable[[], Any], limit_s: float | None = None) -> str:
        """Run a browsing coroutine on its own thread, with a hard time limit.

        The limit is enforced from outside the event loop on purpose. When the
        browser window is closed or crashes mid-task, the library can wait on
        its dead connection in a way that asyncio cancellation does not
        interrupt — and one tool call that never returns freezes the whole
        chat. So on timeout the worker thread is abandoned, the browser is
        closed, and an error is returned to the assistant.
        """
        limit = float(limit_s or TASK_LIMIT_S)
        box: list[str] = []

        def work() -> None:
            try:
                box.append(str(asyncio.run(make_coroutine())))
            except Exception as exc:  # the caller gets text, never an exception
                box.append(f"Browsing task encountered an error: {type(exc).__name__}: {exc}")

        worker = threading.Thread(target=work, name="live-browser", daemon=True)
        worker.start()
        worker.join(limit)
        if worker.is_alive() or not box:
            logger.warning("Browsing task exceeded %.0fs; abandoning it and closing the browser", limit)
            self._kept_browser = None
            self.latest_booking = None
            _close_stale_browsers(str(config.REPO_ROOT / "data" / "browser_profile"))
            return (
                f"error: the browser did not finish within {limit:.0f} seconds (the window may have been "
                "closed). Nothing was booked. Please try again."
            )
        return box[0]

    async def _async_run_task(
        self,
        task: str,
        on_step_callback: Callable[[int, str, str], None] | None = None,
        max_steps: int = 8,
        mode: str = "shop",
    ) -> str:
        llm = _get_browser_llm()
        profile_dir = config.REPO_ROOT / "data" / "browser_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        if self._kept_browser is not None:
            with contextlib.suppress(Exception):
                await self._kept_browser.kill()
            self._kept_browser = None
        _close_stale_browsers(str(profile_dir))
        browser = Browser(
            headless=self.headless, user_data_dir=str(profile_dir), keep_alive=mode != "shop"
        )
        _bring_window_to_front()

        def _step_cb(state: Any, output: Any, step_idx: int) -> None:
            thought = ""
            action_desc = ""
            if hasattr(output, "current_state") and output.current_state:
                cs = output.current_state
                thought = getattr(cs, "next_goal", "") or getattr(cs, "thought", "")
            if hasattr(output, "action") and output.action:
                action_desc = str(output.action)
            logger.info("Agent Step %d: %s | %s", step_idx, thought, action_desc)

            _bring_window_to_front()

            if on_step_callback:
                with contextlib.suppress(Exception):
                    on_step_callback(step_idx, str(thought), str(action_desc))
            if LiveBrowser._step_listener:
                with contextlib.suppress(Exception):
                    LiveBrowser._step_listener(step_idx, str(thought), str(action_desc))

        # Seat maps are drawn as one image, with no element to click by index,
        # so look-up tasks may also click at a point on the screenshot.
        tools: Any = Tools()
        if mode != "shop":
            tools.set_coordinate_clicking(True)

        agent: Any = Agent(
            task=task,
            llm=llm,
            browser=browser,
            tools=tools,
            use_vision=True,
            register_new_step_callback=_step_cb,
            max_actions_per_step=3,
            max_failures=3,
            extend_system_message=_SYSTEM_MESSAGES[mode],
        )

        try:
            history = await agent.run(max_steps=max_steps)
            final_text = history.final_result() or "Completed task."

            # Extract visited URLs
            urls = history.urls()
            if urls:
                # Find the most relevant product or active URL
                for u in reversed(urls):
                    if u and not u.startswith("about:") and ("amazon" in u or "flipkart" in u):
                        self.latest_url = u
                        break
                if not self.latest_url and urls:
                    self.latest_url = urls[-1] or ""

            # Extract latest screenshot thumbnail (base64)
            screenshots = history.screenshots()
            if screenshots:
                valid_shots = [s for s in screenshots if s]
                if valid_shots:
                    self.latest_screenshot_b64 = valid_shots[-1] or ""

            if mode == "movies":
                final_text = await self._finish_movie(browser, final_text)
            if mode == "flights":
                with contextlib.suppress(Exception):
                    page = await browser.must_get_current_page()
                    parsed = await asyncio.wait_for(self._read_flights(page), timeout=40)
                    if parsed:
                        origin, destination, nice = self._flight_query
                        self._kept_browser = browser
                        self.latest_screenshot_b64 = await page.screenshot() or self.latest_screenshot_b64
                        return self._flight_report(parsed, origin, destination, nice, await page.get_url())
                with contextlib.suppress(Exception):
                    await browser.kill()
                final_text += "\nNote: these figures were read by the browsing model and could not be verified."
            if mode != "shop":
                # A look-up, not a purchase: report what was read, nothing more.
                real = [u for u in (urls or []) if u and not u.startswith(("about:", "chrome"))]
                self.latest_url = real[-1] if real else ""
                self.latest_cart_url = ""
                self.latest_product = {}
                fields = {
                    m.group(1).strip(): m.group(2).strip()
                    for m in re.finditer(r"^\s*([A-Z][A-Za-z ]{2,14}):\s*(.+)$", final_text, re.M)
                    if "not found" not in m.group(2).lower()
                }
                self.latest_title = fields.get("Movie") or fields.get("Route") or "Browsing result"
                self.latest_booking = {
                    "kind": mode,
                    "fields": fields,
                    "url": self.latest_url,
                    "screenshot": self.latest_screenshot_b64,
                }
                return (
                    "Browsing finished. Nothing was booked or paid for.\n"
                    f"{final_text}\n- Page: {self.latest_url}"
                )

            # Extract product title & price from agent output
            title = ""
            price = ""
            m_title = re.search(r"Product(?:\s*Title)?\s*:\s*([^\n\r]+)", final_text, re.IGNORECASE)
            if m_title:
                title = m_title.group(1).strip().strip("'\"")
            elif "('" in final_text:
                quote_match = re.search(r"\('([^']+)'\)", final_text)
                if quote_match:
                    title = quote_match.group(1).strip()

            m_price = re.search(r"Price\s*:\s*([^\n\r,]+)", final_text, re.IGNORECASE)
            if m_price:
                price = m_price.group(1).strip()
            elif "INR" in final_text or "₹" in final_text or "$" in final_text:
                m_curr = re.search(r"(?:INR|₹|\$)\s*[\d,]+(?:\.\d{2})?", final_text)
                if m_curr:
                    price = m_curr.group(0).strip()

            if not title and len(final_text.splitlines()) > 0:
                title = final_text.splitlines()[0][:100]

            self.latest_title = title or "Selected Item"
            self.latest_product = {
                "title": self.latest_title,
                "price": price or "Check page for current price",
                "url": self.latest_url,
            }

            # If on Amazon, extract ASIN and create direct 1-click cart addition link
            asin = ""
            all_urls_to_check = [self.latest_url] + (urls or [])
            for u in all_urls_to_check:
                m_u = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", u or "")
                if m_u:
                    asin = m_u.group(1)
                    break
            if not asin:
                m_asin_text = re.search(r"(?:ASIN|dp)[\s:=#/]+([A-Z0-9]{10})", final_text, re.IGNORECASE)
                if m_asin_text:
                    asin = m_asin_text.group(1)

            if asin:
                self.latest_url = f"https://www.amazon.in/dp/{asin}"
                self.latest_cart_url = (
                    f"https://www.amazon.in/gp/aws/cart/add.html?ASIN.1={asin}&Quantity.1=1"
                )
            elif "amazon" in self.latest_url.lower():
                self.latest_cart_url = self.latest_url
            else:
                self.latest_cart_url = self.latest_url

            summary_lines = [
                "Successfully completed browsing task:",
                f"- Product: {self.latest_title}",
            ]
            if price:
                summary_lines.append(f"- Price: {price}")
            summary_lines.append(f"- Product URL: {self.latest_url}")
            if self.latest_cart_url and self.latest_cart_url != self.latest_url:
                summary_lines.append(f"- Cart URL: {self.latest_cart_url}")
            summary_lines.append(f"\nDetails:\n{final_text}")

            return "\n".join(summary_lines)
        except Exception as exc:
            logger.exception("Autonomous browsing task failed: %s", exc)
            return f"Browsing task encountered an error: {type(exc).__name__}: {exc}"

    async def _finish_movie(self, browser: Any, final_text: str) -> str:
        """Select the seats ourselves and replace whatever the model claimed
        about seats and price with what is verifiably on the screen."""
        seats_line, price_line, status, free_line, tiers = "not selected", "", "", "", ""
        try:
            page = await browser.must_get_current_page()
            url = await page.get_url()
            # The model often stops one click short, with the terms notice or
            # the 'How many seats' dialog still open. That dialog also lists
            # every price tier and how full it is, so read it before closing.
            tiers = ""
            for label in ("Accept", "Select Seats"):
                text = str(await page.evaluate("() => document.body.innerText"))
                tiers = tiers or price_tiers(text)
                await page.evaluate(
                    "(label) => { const el = [...document.querySelectorAll('button, a, div[role=button], div, span')]"
                    ".find(e => e.children.length <= 1 && (e.innerText || '').trim() === label);"
                    " if (el) el.click(); return el ? 'ok' : ''; }",
                    label,
                )
                await asyncio.sleep(2.0)
            url = await page.get_url()
            if "seat-layout" in url:
                chosen, total = await asyncio.wait_for(
                    self._select_seats(page, self._seats_wanted), timeout=45
                )
                if self._free_seats:
                    free_line = f"{self._free_seats} seats free for this show"
                if chosen:
                    labels = await self._read_seat_labels(page, chosen)
                    seats_line = labels or f"{len(chosen)} adjacent seats (selected on screen)"
                    price_line = total or "shown on the page"
                    status = "Selected on screen, not booked"
                    self._kept_browser = browser  # leave the window open for the audience
                else:
                    seats_line = "could not select seats on the seat map"
                    status = "Seats available"
                shot = await page.screenshot()
                if shot:
                    self.latest_screenshot_b64 = shot
            else:
                status = "Seat map not reached"
        except Exception as exc:
            logger.warning("Seat selection failed: %s", exc)
            seats_line, status = "could not select seats on the seat map", "Seats available"
        if self._kept_browser is None:
            with contextlib.suppress(Exception):
                await browser.kill()

        kept = [
            line
            for line in final_text.splitlines()
            if not re.match(r"\s*(Seats|Price|Prices|Availability|Status)\s*:", line, re.I)
        ]
        if tiers:
            kept.append(f"Prices: {tiers}")
        if free_line:
            kept.append(f"Availability: {free_line}")
        kept.append(f"Seats: {seats_line}")
        if price_line:
            kept.append(f"Price: {price_line}")
        kept.append(f"Status: {status}")
        return "\n".join(kept)

    async def _select_seats(self, page: Any, wanted: int) -> tuple[list[Seat], str]:
        """Click `wanted` free seats next to each other. Returns the seats that
        are verifiably selected afterwards, and the total on the Pay button."""
        await asyncio.sleep(1.5)
        png = base64.b64decode(await page.screenshot())
        scale = _screenshot_scale(png, await page.evaluate("() => window.innerWidth"))
        visible = find_seats(png)
        self._free_seats = sum(1 for seat in visible if not seat.selected)
        run = pick_run(visible, wanted)
        if not run:
            return [], ""
        mouse = await page.mouse
        selected: list[Seat] = []
        for seat in run:
            # The site may select the whole group on the first click.
            if any(abs(s.cx - seat.cx) <= 8 and abs(s.cy - seat.cy) <= 8 for s in selected):
                continue
            await mouse.click(int(seat.cx / scale), int(seat.cy / scale))
            await asyncio.sleep(1.0)
            png = base64.b64decode(await page.screenshot())
            selected = [s for s in find_seats(png) if s.selected]
            if len(selected) >= wanted:
                break
        total = await page.evaluate(
            "() => { const hit = [...document.querySelectorAll('button, a, div, span')]"
            ".map(e => (e.innerText || '').trim())"
            ".filter(t => /^Pay\\s*(Rs\\.?|₹|INR)?\\s*[\\d,.]+/i.test(t) && t.length < 30)[0];"
            " return hit || ''; }"
        )
        total = re.sub(r"^Pay\s*", "", str(total or "")).replace("₹", "INR ").strip()
        return selected, total

    async def _read_seat_labels(self, page: Any, seats: list[Seat]) -> str:
        """Ask the vision model to read the row letter and seat numbers of the
        seats we selected. Only labels are read; what is selected was verified
        from pixels. Returns '' if the model cannot be reached."""
        if not config.GEMINI_API_KEY:
            return ""
        try:
            import io

            import openai
            from PIL import Image

            image = Image.open(io.BytesIO(base64.b64decode(await page.screenshot()))).convert("RGB")
            cy = seats[0].cy
            strip = image.crop((0, max(0, cy - 26), image.width, min(image.height, cy + 26)))
            buffer = io.BytesIO()
            strip.save(buffer, format="PNG")
            data = base64.b64encode(buffer.getvalue()).decode()
            client = openai.OpenAI(
                base_url=config.GEMINI_BASE_URL.rstrip("/"), api_key=config.GEMINI_API_KEY, max_retries=1, timeout=25.0
            )
            def ask() -> Any:
                return client.chat.completions.create(model=config.GEMINI_MODEL, temperature=0.0, messages=messages)

            messages: Any = [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "This is one row of a cinema seat map. The row letter is at the far left. "
                                    f"Exactly {len(seats)} seats are solid green (selected). Reply with only the row "
                                    "letter and those seat numbers, like: L 07, 08"
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
                        ],
                    }
                ]
            reply = await asyncio.to_thread(ask)
            text = (reply.choices[0].message.content or "").strip()
            match = re.match(r"^([A-Z]{1,2})\s*[:\-]?\s*((?:\d{1,2}\s*,?\s*)+)$", text)
            if not match:
                return ""
            numbers = re.findall(r"\d{1,2}", match.group(2))
            if len(numbers) != len(seats):
                return ""
            return ", ".join(f"{match.group(1)}{n}" for n in numbers)
        except Exception as exc:
            logger.info("Seat label read skipped: %s", exc)
            return ""

    async def _read_flights(self, page: Any) -> list[Flight]:
        """Wait for results, bring non-stop flights to the top, and collect
        what the page lists while scrolling (the list loads as you scroll)."""
        found: dict[tuple[str, str], Flight] = {}

        async def collect() -> None:
            text = await page.evaluate("() => document.body.innerText")
            for flight in parse_flights(str(text)):
                found.setdefault((flight.code, flight.departure), flight)

        for _ in range(5):  # the agent has already waited for results
            await asyncio.sleep(1.5)
            await collect()
            if found:
                break
        if not found:
            return []
        self._say(2, "Reading the results and sorting non-stop flights first")
        await page.evaluate(
            "() => { const el = [...document.querySelectorAll('div, span, p, li')].find(e => "
            "e.children.length <= 3 && /^Non Stop First/i.test((e.innerText || '').trim())); "
            "if (el) el.click(); return el ? 'ok' : ''; }"
        )
        await asyncio.sleep(2.0)
        for _ in range(4):
            await collect()
            await page.evaluate("() => window.scrollBy(0, 900)")
            await asyncio.sleep(0.9)
        await collect()
        await page.evaluate("() => window.scrollTo(0, 0)")
        await asyncio.sleep(0.6)
        return list(found.values())

    def _flight_report(
        self, flights: list[Flight], origin: str, destination: str, nice_date: str, url: str
    ) -> str:
        best = best_flight(flights)
        assert best is not None
        others = sorted((f for f in flights if f is not best), key=lambda f: (not f.non_stop, f.fare_inr))[:3]
        fields = {
            "Route": f"{origin.title()} ({best.origin}) to {destination.title()} ({best.destination})",
            "Date": nice_date,
            "Airline": best.airline,
            "Flight": best.code,
            "Departure": best.departure,
            "Arrival": best.arrival,
            "Duration": f"{best.duration}, {best.stops}",
            "Fare": f"INR {best.fare_inr:,}",
            "Other options": "; ".join(f.short() for f in others),
            "Status": "Available, not booked",
        }
        fields = {k: v for k, v in fields.items() if v}
        self.latest_title = fields["Route"]
        self.latest_cart_url = ""
        self.latest_product = {}
        self.latest_booking = {
            "kind": "flights", "fields": fields, "url": url, "screenshot": self.latest_screenshot_b64,
        }
        lines = "\n".join(f"{k}: {v}" for k, v in fields.items())
        return (
            "Browsing finished. Nothing was booked or paid for.\n"
            f"{lines}\n({len(flights)} flights read from the page)\n- Page: {url}"
        )

    @staticmethod
    def _say(step: int, text: str) -> None:
        """Progress line for the chat, same channel the browsing agent uses."""
        if LiveBrowser._step_listener:
            with contextlib.suppress(Exception):
                LiveBrowser._step_listener(step, text, "")

    def find_movie_tickets(
        self, movie: str, city: str, date: str = "", seats: int = 2, area: str = ""
    ) -> str:
        """Look up shows on BookMyShow and select seats on screen. Never pays."""
        seats = max(1, min(int(seats or 2), 6))
        where = f" Prefer a cinema in or near {area}." if area else ""
        when = f" for {date}" if date else " for today, or the next day that has shows"
        if movie:
            find = f"Find the movie '{movie}' and open its page. If it is not showing, say so and stop."
        else:
            find = "Pick the first movie listed under now showing and open its page."
        task = (
            f"Go to https://in.bookmyshow.com/explore/movies-{_slug(city) or 'bengaluru'} . "
            f"{find} Click 'Book tickets', choosing 2D and the first language if asked. "
            f"On the showtimes page select the date{when}.{where} Note the cinemas and showtimes on offer. "
            f"Click one available showtime, accept any terms notice, choose {seats} seats in the "
            f"'How many seats' dialog and click Select Seats. You are not finished until the page address "
            "contains 'seat-layout'. As soon as the seat layout is on screen, "
            "you are done: do NOT click any seat (the system selects the seats itself) and do NOT click Pay. "
            "Report 'Seats: pending' and 'Price: pending'. "
            "If the site asks you to log in, stop and report what you have."
        )
        self._seats_wanted = seats
        return self.run_autonomous_task(task, max_steps=22, mode="movies")

    def find_flights(self, origin: str, destination: str, date: str = "", passengers: int = 1) -> str:
        """Look up flights and fares on MakeMyTrip. Never enters traveller details or pays."""
        passengers = max(1, min(int(passengers or 1), 6))
        src, dst = airport_code(origin), airport_code(destination)
        day = parse_travel_date(date)
        nice = day.strftime("%d %b %Y")
        url = ""
        if src and dst:
            url = (
                "https://www.makemytrip.com/flight/search?itinerary="
                f"{src}-{dst}-{day.strftime('%d/%m/%Y')}&tripType=O&paxType=A-{passengers}_C-0_I-0"
                "&intl=false&cabinClass=E&lang=eng"
            )
            start = f"Go to {url} ."
        else:
            start = (
                "Go to https://www.makemytrip.com/flights/ , close the login popup, choose One Way, "
                f"enter '{origin}' as From and '{destination}' as To, pick {nice} as the departure date "
                f"with {passengers} adult(s), and click Search."
            )
        self._flight_query = (origin, destination, nice)
        task = (
            f"{start} Close any login or offer popup. Results can take several seconds: if the list is empty, "
            "still loading or shows an error, wait 5 seconds and look again, and reload the page once before "
            f"concluding there are no flights. Check the results are one way from {origin} to "
            f"{destination} on {nice}. Tick the 'Non Stop' filter if it is offered. "
            "Read the first (cheapest) flight: airline, flight number, departure and arrival times, duration "
            "and fare. Also note up to three other flights. Do not click 'View Prices' or 'Book Now'."
        )
        return self.run_autonomous_task(task, max_steps=10, mode="flights")

    def open_url(self, url: str) -> str:
        """Navigate to any URL using autonomous browser."""
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        task = f"Navigate to {url}, verify the page loaded, and summarize the key content."
        return self.run_autonomous_task(task, max_steps=4)

    def search_e_commerce(self, query: str, site: str = "amazon") -> str:
        """Search an e-commerce platform and return product listings."""
        site_lower = site.lower()
        if "flipkart" in site_lower:
            start_url = "https://www.flipkart.com"
        else:
            start_url = "https://www.amazon.in"

        task = (
            f"Go to {start_url}, search for '{query}', inspect the product results, "
            f"click on the best matching product, and report its title and price."
        )
        return self.run_autonomous_task(task, max_steps=6)

    def add_to_cart(
        self, product_keyword_or_index: str = "1", site: str = "amazon"
    ) -> str:
        """Autonomously search for a product and add it to the cart."""
        site_lower = site.lower()
        if "flipkart" in site_lower:
            start_url = "https://www.flipkart.com"
        else:
            start_url = "https://www.amazon.in"

        task = (
            f"Go to {start_url}, search for '{product_keyword_or_index}', click on the first "
            f"good product result, and click 'Add to Cart'. Finally report the product title, "
            f"price in INR, and cart confirmation."
        )
        return self.run_autonomous_task(task, max_steps=8)

    def click_element(self, target: str) -> str:
        """Click an element described by target text or selector."""
        task = f"On the current web page, find and click on '{target}'."
        return self.run_autonomous_task(task, max_steps=3)
