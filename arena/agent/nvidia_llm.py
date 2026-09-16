"""The target agent: NVIDIA NIM API via official OpenAI-compatible endpoint.

NVIDIA NIM exposes an OpenAI-compatible endpoint at `https://integrate.api.nvidia.com/v1`,
allowing AgentDojo's `LocalLLM` to drive it with tool/function calling support.

The API key is read from NVIDIA_API_KEY.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import openai
from agentdojo.agent_pipeline.llms.local_llm import (
    InvalidModelOutputError,
    LocalLLM,
    _make_system_prompt,
    _parse_model_output,
    reformat_message,
)
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionsRuntime
from agentdojo.types import (
    ChatAssistantMessage,
    ChatMessage,
    get_text_content_as_str,
    text_content_block_from_string,
)

from arena import config
from sentinelz.evidence.canonical import dumps_str

MAX_PARSE_RETRIES = 2


class NvidiaLLM(LocalLLM):
    """`LocalLLM` pointed at NVIDIA NIM cloud endpoint without incompatible OpenAI seed parameter."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
        top_p: float = 0.9,
    ) -> None:
        resolved_base = (base_url or config.NVIDIA_BASE_URL).rstrip("/")
        resolved_key = (api_key or config.NVIDIA_API_KEY).strip()
        client = openai.OpenAI(base_url=resolved_base, api_key=resolved_key, max_retries=1)
        super().__init__(
            client=client,
            model=model or config.NVIDIA_MODEL,
            temperature=temperature,
            top_p=top_p,
        )
        self.name = f"nvidia/{self.model}"

    def _chat_completion_no_seed(self, messages: list[dict[str, Any]]) -> str:
        reformatted_messages = []
        for message in messages:
            content = reformat_message(message)  # type: ignore[arg-type]
            reformatted_messages.append({"role": message["role"], "content": content})
        response = (
            self.client.chat.completions.create(
                model=self.model,
                messages=reformatted_messages,  # type: ignore[arg-type]
                temperature=self.temperature,
                top_p=self.top_p,
            )
            .choices[0]
            .message.content
        )
        if response is None:
            raise InvalidModelOutputError("No response from model")
        return response

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        messages_ = []
        for m in messages:
            role, content = m["role"], m["content"]
            if role == "system" and content is not None:
                content = _make_system_prompt(get_text_content_as_str(content), runtime.functions.values())
            if role == "tool":
                role = self.tool_delimiter
                if "error" in m and m["error"] is not None:
                    content = dumps_str({"error": m["error"]})
                else:
                    func_result = m["content"]
                    if func_result == "None":
                        func_result = "Success"
                    content = dumps_str({"result": func_result})
            messages_.append({"role": role, "content": content})

        last_error: Exception | None = None
        for _ in range(MAX_PARSE_RETRIES + 1):
            try:
                completion = self._chat_completion_no_seed(messages_)
                output = _parse_model_output(completion)
                return query, runtime, env, [*messages, output], extra_args
            except (InvalidModelOutputError, ValueError, openai.OpenAIError) as exc:
                last_error = exc

        # Out of retries. Return a plain answer so the run terminates cleanly
        note = f"[model output could not be parsed after {MAX_PARSE_RETRIES} retries: {last_error}]"
        fallback = ChatAssistantMessage(
            role="assistant", content=[text_content_block_from_string(note)], tool_calls=None
        )
        return query, runtime, env, [*messages, fallback], extra_args
