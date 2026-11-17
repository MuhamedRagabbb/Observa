"""Tiny OpenAI-compatible chat client. Standard library only.

Defaults point at Groq. Everything is configured from environment
variables, so nothing secret ever lives in a file:

  GROQ_API_KEY                   the API key (XAI_API_KEY or LLM_API_KEY also work)
  LLM_MODEL                      model id; no default on purpose, model names change often
  LLM_BASE_URL                   default https://api.groq.com/openai/v1
                                 (https://api.x.ai/v1 when only XAI_API_KEY is set)
  LLM_TEMPERATURE                optional; not sent unless set

List the model ids your key can use:   python -m logs_agent.llm
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_BASE_URL = GROQ_BASE_URL


class LLMError(RuntimeError):
    """Any problem talking to the model: config, network, HTTP status, odd reply."""


class LLMClient:
    def __init__(self, api_key: str, model: str | None, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 60.0, temperature: float | None = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature

    @classmethod
    def from_env(cls, require_model: bool = True) -> "LLMClient":
        groq, xai = os.environ.get("GROQ_API_KEY"), os.environ.get("XAI_API_KEY")
        key = groq or xai or os.environ.get("LLM_API_KEY")
        if not key:
            raise LLMError("API key missing: set GROQ_API_KEY (or XAI_API_KEY / LLM_API_KEY) in this terminal")
        default_base = XAI_BASE_URL if (xai and not groq) else GROQ_BASE_URL
        model = os.environ.get("LLM_MODEL")
        if require_model and not model:
            raise LLMError("model missing: set LLM_MODEL (list the ids with: python -m logs_agent.llm)")
        temp = os.environ.get("LLM_TEMPERATURE")
        return cls(key, model, os.environ.get("LLM_BASE_URL", default_base),
                   temperature=float(temp) if temp else None)

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = self.base_url + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "observa-logs-agent/0.1",
        }
        for attempt in (1, 2):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:300]
                if e.code in (429, 500, 502, 503, 504) and attempt == 1:
                    time.sleep(2)
                    continue
                raise LLMError(f"HTTP {e.code} from {path}: {body}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt == 1:
                    time.sleep(2)
                    continue
                raise LLMError(f"network error calling {path}: {e}") from None
            except json.JSONDecodeError:
                raise LLMError(f"{path} did not return JSON") from None
        raise LLMError("unreachable")

    def complete(self, messages: list[dict]) -> str:
        if not self.model:
            raise LLMError("model missing: set LLM_MODEL")
        payload: dict = {"model": self.model, "messages": messages}
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        data = self._request("POST", "/chat/completions", payload)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("unexpected reply shape from /chat/completions") from None
        if not isinstance(content, str) or not content.strip():
            raise LLMError("the model returned an empty answer (a reasoning model can run out of tokens)")
        return content

    def list_models(self) -> list[str]:
        data = self._request("GET", "/models")
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))


class ScriptedLLM:
    """Offline stand-in: returns canned replies in order (or raises an exception). For tests."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict]) -> str:
        self.calls.append(list(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _main() -> None:
    try:
        for model_id in LLMClient.from_env(require_model=False).list_models():
            print(model_id)
    except LLMError as e:
        print(f"error: {e}")
        raise SystemExit(2)


if __name__ == "__main__":
    _main()
