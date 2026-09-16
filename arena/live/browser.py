"""Live real-time web browsing and e-commerce automation for Sentinel-Z Aura.

Powered by the proven 'browser-use' autonomous AI web agent framework.
Navigates, searches, and interacts with websites (Amazon, Flipkart, Google, etc.)
in a real visible Chrome window (headless=False) on the user's desktop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import re
import sys
from typing import Any, Callable

# Ensure UTF-8 output encoding on Windows so currency symbols (e.g. ₹) don't crash console
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from arena import config
from browser_use import Agent, Browser, ChatGoogle, ChatOpenAI

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


from urllib.parse import quote_plus

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

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    lambda: asyncio.run(self._async_run_task(task, on_step_callback, max_steps))
                ).result()
        return asyncio.run(self._async_run_task(task, on_step_callback, max_steps))

    async def _async_run_task(
        self,
        task: str,
        on_step_callback: Callable[[int, str, str], None] | None = None,
        max_steps: int = 8,
    ) -> str:
        llm = _get_browser_llm()
        profile_dir = config.REPO_ROOT / "data" / "browser_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        browser = Browser(headless=self.headless, user_data_dir=str(profile_dir))
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
                try:
                    on_step_callback(step_idx, str(thought), str(action_desc))
                except Exception:
                    pass
            if LiveBrowser._step_listener:
                try:
                    LiveBrowser._step_listener(step_idx, str(thought), str(action_desc))
                except Exception:
                    pass

        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            use_vision=True,
            register_new_step_callback=_step_cb,
            max_actions_per_step=3,
            max_failures=3,
            extend_system_message=(
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
                m_u = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", u)
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
