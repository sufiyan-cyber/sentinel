"""Arena-side configuration: role, LLM backend, network addresses."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

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
NVIDIA_MODEL: str = _env("SENTINELZ_NVIDIA_MODEL", "meta/llama-3.2-11b-vision-instruct")

#: Google Gemini API configuration (via official OpenAI-compatible endpoint)
GEMINI_API_KEY: str = _env("GEMINI_API_KEY", "") or _env("GOOGLE_API_KEY", "")
GEMINI_BASE_URL: str = _env("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
GEMINI_MODEL: str = _env("SENTINELZ_GEMINI_MODEL", "gemini-3.5-flash-lite")

ATTACKER_HOST: str = _env("SENTINELZ_ATTACKER_HOST", "127.0.0.1")
ATTACKER_PORT: int = _int_env("SENTINELZ_ATTACKER_PORT", 8899)
ATTACKER_URL: str = f"http://{ATTACKER_HOST}:{ATTACKER_PORT}"

UI_HOST: str = _env("SENTINELZ_UI_HOST", "127.0.0.1")
UI_PORT: int = _int_env("SENTINELZ_UI_PORT", 8800)

SEED: int = _int_env("SENTINELZ_SEED", 0)

#: The two suites the project commits to. Four would be padding.
SUITES: tuple[str, ...] = ("workspace", "banking")
SUITE_VERSION: str = "v1"

for _d in (RUNS_DIR, CAMPAIGNS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
