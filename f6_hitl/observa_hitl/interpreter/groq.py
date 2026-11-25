"""Groq through instructor (JSON mode): the model's output must validate as InterpretedFeedback
against the reviewed version's targets.

instructor re-asks with the validation error up to `max_retries` times (INTERPRETER_MAX_RETRIES = 2,
so at most 3 calls). When every attempt is invalid the result is `inconclusive` with the last raw
output kept: F6 never guesses a revision plan. Provider failures (network, 429, 5xx) raise
ProviderUnavailable instead, so the outbox retries the whole interpretation later with backoff;
the OpenAI client itself is built with max_retries=0 so nothing retries in hiding.
"""
import threading

import httpx
import instructor
import openai
from instructor.core.exceptions import InstructorRetryException
from openai import OpenAI

from observa_reports.content import ReportContent

from . import prompt
from .schema import InterpretationResult, InterpretedFeedback, inconclusive

INTERPRETER_MAX_RETRIES = 2


class ProviderUnavailable(RuntimeError):
    """Groq could not be reached or refused the request; the outbox retries the event."""


def _content(completion) -> str | None:
    try:
        return completion.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        return None if completion is None else str(completion)


class GroqInterpreter:
    name = "groq"

    def __init__(self, client: instructor.Instructor, model: str, *, max_retries: int = INTERPRETER_MAX_RETRIES):
        self._client, self._model, self._max_retries = client, model, max_retries
        self._calls = threading.local()
        client.on("completion:kwargs", self._count)

    @classmethod
    def from_settings(cls, *, api_key: str, base_url: str, model: str, max_retries: int = INTERPRETER_MAX_RETRIES,
                      timeout: float = 30.0, http_client: httpx.Client | None = None) -> "GroqInterpreter":
        if not api_key:
            raise ValueError("GROQ_API_KEY is required for INTERPRETER=groq")
        raw = OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout, http_client=http_client)
        return cls(instructor.from_openai(raw, mode=instructor.Mode.JSON), model, max_retries=max_retries)

    def _count(self, *_args, **_kwargs) -> None:
        self._calls.n = getattr(self._calls, "n", 0) + 1

    def interpret(self, feedback: str, report: ReportContent, allowed_targets: set[str]) -> InterpretationResult:
        if not (feedback or "").strip():
            return InterpretationResult(output=inconclusive(allowed_targets), attempts=0, interpreter=self.name)
        self._calls.n = 0
        try:
            output = self._client.chat.completions.create(
                model=self._model, response_model=InterpretedFeedback, max_retries=self._max_retries,
                context={"allowed_targets": allowed_targets}, temperature=0,
                messages=prompt.build(feedback, report, allowed_targets))
        except InstructorRetryException as exc:
            if isinstance(exc.__cause__, (openai.BadRequestError, openai.UnprocessableEntityError)):
                # Groq rejected this request itself (e.g. json_validate_failed, context too long): retrying
                # the same prompt cannot help, so ask the reviewer to clarify instead of retrying for minutes.
                return InterpretationResult(output=inconclusive(allowed_targets), attempts=max(1, self._calls.n),
                                            raw=f"provider rejected the request: {exc.__cause__}"[:2000],
                                            interpreter=self.name)
            if isinstance(exc.__cause__, openai.APIError) or not exc.failed_attempts:
                raise ProviderUnavailable(f"Groq request failed: {exc.__cause__ or exc}") from exc
            return InterpretationResult(output=inconclusive(allowed_targets), attempts=exc.n_attempts,
                                        raw=_content(exc.last_completion), interpreter=self.name)
        except openai.APIError as exc:  # raised directly by some instructor paths
            raise ProviderUnavailable(f"Groq request failed: {exc}") from exc
        return InterpretationResult(output=output, attempts=max(1, self._calls.n), interpreter=self.name)
