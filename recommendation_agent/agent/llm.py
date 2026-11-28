"""LangChain-backed OpenAI-compatible chat client for F4 option generation."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"


class LLMProviderError(RuntimeError):
    """Provider error preserving retryability metadata for F4 callers."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.is_file():
        return dotenv_values(env_path, encoding="utf-8-sig", interpolate=False).get(name) or None
    return None


class LLMClient:
    """Small compatibility wrapper around LangChain's ChatOpenAI interface."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = GROQ_BASE_URL,
        timeout: float = 45.0,
        temperature: float | None = 0.0,
    ):
        try:
            from langchain_openai import ChatOpenAI

            self._chat = ChatOpenAI(
                model=model,
                api_key=api_key,
                base_url=base_url.rstrip("/"),
                temperature=temperature,
                timeout=timeout,
                max_retries=1,
                use_responses_api=False,
            )
        except Exception as exc:
            raise LLMProviderError(f"Could not configure LangChain chat client: {exc}", retryable=False) from None

    @classmethod
    def from_env(cls, *, optional: bool = False) -> "LLMClient | None":
        groq = _env_value("GROQ_API_KEY")
        xai = _env_value("XAI_API_KEY")
        key = groq or xai or _env_value("LLM_API_KEY")
        if not key:
            if optional:
                return None
            raise LLMProviderError(
                "API key missing: set GROQ_API_KEY (or LLM_API_KEY / XAI_API_KEY) in environment or recommendation_agent/.env",
                retryable=False,
            )
        base_url = _env_value("LLM_BASE_URL") or (XAI_BASE_URL if xai and not groq else GROQ_BASE_URL)
        model = _env_value("LLM_MODEL") or DEFAULT_MODEL
        temp_raw = _env_value("LLM_TEMPERATURE")
        try:
            temperature = float(temp_raw) if temp_raw is not None else 0.0
        except ValueError:
            raise LLMProviderError("LLM_TEMPERATURE must be numeric", retryable=False) from None
        return cls(api_key=key, model=model, base_url=base_url, temperature=temperature)

    def complete(self, messages: list[dict[str, str]]) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        lc_messages = []
        for message in messages:
            cls = SystemMessage if message.get("role") == "system" else HumanMessage
            lc_messages.append(cls(content=message.get("content", "")))
        try:
            response = self._chat.invoke(lc_messages)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            retryable = status is None or status in (408, 409, 425, 429) or status >= 500
            raise LLMProviderError(
                f"LangChain chat request failed{f' (HTTP {status})' if status else ''}: {exc}",
                status_code=status,
                retryable=retryable,
            ) from None
        content = response.content
        if not isinstance(content, str) or not content.strip():
            raise LLMProviderError("Model returned an empty recommendation response", retryable=True)
        return content


class ScriptedLLM:
    """Offline deterministic stand-in for tests and local pipeline runs."""

    def __init__(self, replies: list[str | Exception]):
        self.replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
