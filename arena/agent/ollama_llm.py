"""The real target agent: an open-weights model served by Ollama.

Ollama exposes an OpenAI-compatible endpoint at `<base>/v1`, so AgentDojo's
`LocalLLM` drives it unchanged. `LocalLLM` already does what the PRD asks for
— a strict output format with a parse-failure retry loop — so there is nothing
to write here beyond wiring.

The address always comes from OLLAMA_BASE_URL. Ollama may be on another
machine on the LAN; nothing here may assume localhost.
"""

from __future__ import annotations

from collections.abc import Sequence

import openai
from agentdojo.agent_pipeline.llms.local_llm import InvalidModelOutputError, LocalLLM
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.types import ChatAssistantMessage, ChatMessage, text_content_block_from_string

from arena import config

MAX_PARSE_RETRIES = 2


class OllamaLLM(LocalLLM):
    """`LocalLLM` pointed at Ollama, with the retry loop the PRD specifies."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
        top_p: float = 0.9,
    ) -> None:
        resolved_base = (base_url or config.OLLAMA_BASE_URL).rstrip("/")
        client = openai.OpenAI(base_url=f"{resolved_base}/v1", api_key="ollama", max_retries=1)
        super().__init__(client=client, model=model or config.OLLAMA_MODEL, temperature=temperature, top_p=top_p)
        self.name = f"ollama/{self.model}"

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        last_error: Exception | None = None
        for _ in range(MAX_PARSE_RETRIES + 1):
            try:
                return super().query(query, runtime, env, messages, extra_args)
            except (InvalidModelOutputError, ValueError) as exc:
                last_error = exc

        # Out of retries. Return a plain answer so the run terminates cleanly
        # instead of raising into the benchmark — a model that cannot emit a
        # parseable call is a model that took no action.
        note = f"[model output could not be parsed after {MAX_PARSE_RETRIES} retries: {last_error}]"
        fallback = ChatAssistantMessage(
            role="assistant", content=[text_content_block_from_string(note)], tool_calls=None
        )
        return query, runtime, env, [*messages, fallback], extra_args
