"""Arena-side configuration: role, LLM backend, network addresses."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

# Verify TLS against the operating system's certificate store when possible.
# On networks that inspect HTTPS (most offices), the bundled CA list does not
# know the network's own CA and every cloud call fails certificate checks.
# This keeps verification ON — it only changes whose root list is trusted.
with contextlib.suppress(Exception):
    import truststore

    truststore.inject_into_ssl()

RUNS_DIR = REPO_ROOT / "runs"
CAMPAIGNS_DIR = RUNS_DIR / "campaigns"
DEMO_DIR = RUNS_DIR / "demo"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _int_env(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


ROLE: str = _env("SENTINELZ_ROLE", "victim")

#: "auto" probes Ollama and silently falls back to the scripted backend.
LLM_BACKEND: str = _env("SENTINELZ_LLM_BACKEND", "auto")

#: Never hardcode localhost for Ollama — it may live on another machine.
OLLAMA_BASE_URL: str = _env("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = _env("SENTINELZ_OLLAMA_MODEL", "llama3.1:8b")
ATTACKER_MODEL: str = _env("SENTINELZ_ATTACKER_MODEL", OLLAMA_MODEL)

#: NVIDIA NIM API configuration (Meta Llama 3.2 11B cloud endpoint)
NVIDIA_API_KEY: str = _env("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL: str = _env("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODEL: str = _env("SENTINELZ_NVIDIA_MODEL", "nvidia/nemotron-3.5-bolt-e4b")

#: Google Gemini API configuration (via official OpenAI-compatible endpoint)
GEMINI_API_KEY: str = _env("GEMINI_API_KEY", "") or _env("GOOGLE_API_KEY", "")
GEMINI_BASE_URL: str = _env("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
GEMINI_MODEL: str = _env("SENTINELZ_GEMINI_MODEL", "gemini-3.5-flash-lite")

#: Sarvam AI Voice configuration (multilingual STT, Translation & TTS)
SARVAM_API_KEY: str = _env("SARVAM_API_KEY", "")
SARVAM_BASE_URL: str = _env("SARVAM_BASE_URL", "https://api.sarvam.ai")
SARVAM_SPEAKER: str = _env("SARVAM_SPEAKER", "shubh")

#: CALL-E Agentic Phone Calling configuration (https://api.heycall-e.com)
CALLE_API_KEY: str = _env("CALLE_API_KEY", "") or _env("CALL_E_API_KEY", "")
CALLE_BASE_URL: str = _env("CALLE_BASE_URL", "https://api.heycall-e.com")
CALLE_DEFAULT_PHONE: str = _env("CALLE_DEFAULT_PHONE", "") or _env("CALL_E_DEFAULT_PHONE", "")

#: Dry run by default: the call tool reports success without dialling anyone.
CALLE_DRY_RUN: bool = _env("CALLE_DRY_RUN", "1") not in ("0", "false", "no")

#: Ring the owner when a session is revoked (goes through CALLE_DRY_RUN).
ALERT_CALL: bool = _env("SENTINELZ_ALERT_CALL", "1") not in ("0", "false", "no")

# ------------------------------------------------------------ live demo
#: `natural`     the model decides for itself whether to obey injected text.
#: `susceptible` the model still does the language work, but an instruction
#:               found in untrusted content is followed. Always labelled.
AGENT_PROFILE: str = _env("SENTINELZ_AGENT_PROFILE", "natural").lower()

#: Models that obey planted instructions by themselves (measured, see
#: docs/DEMO_UPGRADE_PLAN.md). Attack scenarios marked `profile: auto` run
#: these as-is and fall back to the labelled `susceptible` profile otherwise.
NATURAL_ATTACK_MODELS: frozenset[str] = frozenset(
    m.strip() for m in _env("SENTINELZ_NATURAL_ATTACK_MODELS", "nvidia/nemotron-3.5-bolt-e4b").split(",") if m.strip()
)

#: The agent's wallet, in whole rupees. Stored and computed in paise.
WALLET_INR: int = _int_env("SENTINELZ_WALLET_INR", 5000)
#: Spend cap when the user names no amount.
DEFAULT_SPEND_CAP_INR: int = _int_env("SENTINELZ_DEFAULT_SPEND_CAP_INR", 1000)
#: Headroom over a stated budget, percent ("about 500" may mean 549).
MANDATE_TOLERANCE_PCT: int = _int_env("SENTINELZ_MANDATE_TOLERANCE", 10)

#: `mock` (offline, default), `cashfree` (sandbox) or `razorpay` (test keys).
PAYMENTS: str = _env("SENTINELZ_PAYMENTS", "mock").lower()
CASHFREE_APP_ID: str = _env("CASHFREE_APP_ID", "")
CASHFREE_SECRET_KEY: str = _env("CASHFREE_SECRET_KEY", "")
CASHFREE_BASE_URL: str = _env("CASHFREE_BASE_URL", "https://sandbox.cashfree.com/pg")
CASHFREE_API_VERSION: str = _env("CASHFREE_API_VERSION", "2023-08-01")
RAZORPAY_KEY_ID: str = _env("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET: str = _env("RAZORPAY_KEY_SECRET", "")
RAZORPAY_BASE_URL: str = _env("RAZORPAY_BASE_URL", "https://api.razorpay.com/v1")

ATTACKER_HOST: str = _env("SENTINELZ_ATTACKER_HOST", "127.0.0.1")
ATTACKER_PORT: int = _int_env("SENTINELZ_ATTACKER_PORT", 8899)
ATTACKER_URL: str = f"http://{ATTACKER_HOST}:{ATTACKER_PORT}"

UI_HOST: str = _env("SENTINELZ_UI_HOST", "127.0.0.1")
UI_PORT: int = _int_env("SENTINELZ_UI_PORT", 8800)

#: The live assistant. Loopback by default; a container sets HOST=0.0.0.0.
LIVE_HOST: str = _env("HOST", "127.0.0.1")
LIVE_PORT: int = _int_env("PORT", UI_PORT + 1)
LIVE_URL: str = f"http://127.0.0.1:{LIVE_PORT}"

SEED: int = _int_env("SENTINELZ_SEED", 0)

#: The two suites the project commits to. Four would be padding.
SUITES: tuple[str, ...] = ("workspace", "banking")
SUITE_VERSION: str = "v1"

for _d in (RUNS_DIR, CAMPAIGNS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
