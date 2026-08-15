"""Choosing what drives the target agent.

Two backends, one interface (AgentDojo's `BasePipelineElement`):

  ollama     the real thing. An 8B-class open-weights model over the local
             HTTP API, address always from OLLAMA_BASE_URL.
  scripted   no model, no network. Deterministic simulated agent — see
             `arena/agent/scripted.py` and configs/scripted_backend.json.

`auto` probes Ollama and falls back to `scripted`. Every Session records
which backend produced it, and no table mixes the two.
"""

from __future__ import annotations

import functools

from arena import config

OLLAMA = "ollama"
SCRIPTED = "scripted"


class BackendUnavailableError(RuntimeError):
    pass


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
def available_models(base_url: str) -> tuple[str, ...]:
    try:
        import httpx

        response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=3.0)
        data = response.json()
        return tuple(sorted(m["name"] for m in data.get("models", [])))
    except Exception:
        return ()


def resolve_backend(requested: str | None = None, base_url: str | None = None) -> str:
    """Return the backend that will actually be used."""
    requested = (requested or config.LLM_BACKEND).strip().lower()
    base_url = base_url or config.OLLAMA_BASE_URL

    if requested == SCRIPTED:
        return SCRIPTED
    if requested == OLLAMA:
        if not probe_ollama(base_url):
            raise BackendUnavailableError(
                f"SENTINELZ_LLM_BACKEND=ollama but no Ollama server answered at {base_url}. "
                f"Start Ollama, fix OLLAMA_BASE_URL in .env, or use SENTINELZ_LLM_BACKEND=auto."
            )
        return OLLAMA
    return OLLAMA if probe_ollama(base_url) else SCRIPTED


def describe() -> dict[str, object]:
    """One-line status for the console and the UI header."""
    backend = resolve_backend()
    return {
        "requested": config.LLM_BACKEND,
        "resolved": backend,
        "ollama_base_url": config.OLLAMA_BASE_URL,
        "ollama_reachable": probe_ollama(config.OLLAMA_BASE_URL),
        "model": config.OLLAMA_MODEL if backend == OLLAMA else "scripted-groundtruth-v1",
        "models_available": list(available_models(config.OLLAMA_BASE_URL)),
    }
