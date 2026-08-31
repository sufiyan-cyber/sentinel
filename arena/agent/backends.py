"""Choosing what drives the target agent.

Four backends, one interface (AgentDojo's `BasePipelineElement`):

  nvidia     NVIDIA NIM API (Meta Llama 3.2 11B cloud endpoint).
  gemini     Google Gemini API (via official OpenAI-compatible endpoint).
  ollama     the real thing. An 8B-class open-weights model over the local
             HTTP API, address always from OLLAMA_BASE_URL.
  scripted   no model, no network. Deterministic simulated agent — see
             `arena/agent/scripted.py` and configs/scripted_backend.json.

`auto` probes NVIDIA, then Ollama, then Gemini, and falls back to `scripted`. Every Session records
which backend produced it, and no table mixes them.
"""

from __future__ import annotations

import functools

from arena import config

NVIDIA = "nvidia"
GEMINI = "gemini"
OLLAMA = "ollama"
SCRIPTED = "scripted"


class BackendUnavailableError(RuntimeError):
    pass


@functools.lru_cache(maxsize=4)
def probe_nvidia(api_key: str | None = None) -> bool:
    """True when an NVIDIA API key is configured."""
    key = (api_key or config.NVIDIA_API_KEY).strip()
    return bool(key)


@functools.lru_cache(maxsize=4)
def probe_ollama(base_url: str, timeout_s: float = 2.0) -> bool:
    """True when an Ollama server answers at `base_url`."""
    try:
        import httpx

        response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout_s)
        return response.status_code == 200
    except Exception:
        return False


@functools.lru_cache(maxsize=4)
def probe_gemini(api_key: str | None = None) -> bool:
    """True when a Gemini API key is configured."""
    key = (api_key or config.GEMINI_API_KEY).strip()
    return bool(key)


@functools.lru_cache(maxsize=4)
def available_models(base_url: str) -> tuple[str, ...]:
    try:
        import httpx

        response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=3.0)
        data = response.json()
        return tuple(sorted(m["name"] for m in data.get("models", [])))
    except Exception:
        return ()


def resolve_backend(
    requested: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> str:
    """Return the backend that will actually be used."""
    requested = (requested or config.LLM_BACKEND).strip().lower()
    base_url = base_url or config.OLLAMA_BASE_URL

    if requested == SCRIPTED:
        return SCRIPTED
    if requested == NVIDIA:
        if not probe_nvidia(api_key or config.NVIDIA_API_KEY):
            raise BackendUnavailableError(
                "SENTINELZ_LLM_BACKEND=nvidia but no NVIDIA_API_KEY was found. "
                "Set NVIDIA_API_KEY in .env or switch to SENTINELZ_LLM_BACKEND=auto."
            )
        return NVIDIA
    if requested == GEMINI:
        if not probe_gemini(api_key or config.GEMINI_API_KEY):
            raise BackendUnavailableError(
                "SENTINELZ_LLM_BACKEND=gemini but no GEMINI_API_KEY (or GOOGLE_API_KEY) was found. "
                "Set GEMINI_API_KEY in .env or switch to SENTINELZ_LLM_BACKEND=auto."
            )
        return GEMINI
    if requested == OLLAMA:
        if not probe_ollama(base_url):
            raise BackendUnavailableError(
                f"SENTINELZ_LLM_BACKEND=ollama but no Ollama server answered at {base_url}. "
                f"Start Ollama, fix OLLAMA_BASE_URL in .env, or use SENTINELZ_LLM_BACKEND=auto."
            )
        return OLLAMA

    # auto mode: NVIDIA -> Ollama -> Gemini -> Scripted
    if probe_nvidia(api_key or config.NVIDIA_API_KEY):
        return NVIDIA
    if probe_ollama(base_url):
        return OLLAMA
    if probe_gemini(api_key or config.GEMINI_API_KEY):
        return GEMINI
    return SCRIPTED


def describe() -> dict[str, object]:
    """One-line status for the console and the UI header."""
    backend = resolve_backend()
    if backend == NVIDIA:
        model_name = config.NVIDIA_MODEL
    elif backend == OLLAMA:
        model_name = config.OLLAMA_MODEL
    elif backend == GEMINI:
        model_name = config.GEMINI_MODEL
    else:
        model_name = "scripted-groundtruth-v1"

    return {
        "requested": config.LLM_BACKEND,
        "resolved": backend,
        "nvidia_configured": probe_nvidia(config.NVIDIA_API_KEY),
        "ollama_base_url": config.OLLAMA_BASE_URL,
        "ollama_reachable": probe_ollama(config.OLLAMA_BASE_URL),
        "gemini_configured": probe_gemini(config.GEMINI_API_KEY),
        "model": model_name,
        "models_available": list(available_models(config.OLLAMA_BASE_URL)),
    }
