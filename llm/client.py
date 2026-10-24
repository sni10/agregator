"""LLM access through LM Studio's OpenAI-compatible API.

Every call returns timings and token counts with the answer: the evaluation compares models on them.
JSON answers are validated against a pydantic model; an invalid answer is retried, then raised as LLMError.
"""
import time
from dataclasses import dataclass
from typing import Literal, Protocol

import openai
from pydantic import BaseModel, ValidationError

type ReasoningEffort = Literal['none', 'low', 'medium', 'high']


class LLMError(Exception):
    """The model gave no usable answer (transport error or JSON that never passed validation)."""


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0  # part of completion_tokens spent on thinking
    seconds: float = 0.0

    def __add__(self, other: 'Usage') -> 'Usage':
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.reasoning_tokens + other.reasoning_tokens,
            self.seconds + other.seconds,
        )


@dataclass(frozen=True)
class Completion[T]:
    value: T
    raw: str
    model: str
    usage: Usage
    attempts: int = 1

    @property
    def tokens_per_second(self) -> float:
        return self.usage.completion_tokens / self.usage.seconds if self.usage.seconds else 0.0


class LLMClient(Protocol):
    def complete_text(self, *, model: str, system: str, user: str, max_tokens: int = 1024,
                      temperature: float = 0.2, reasoning_effort: ReasoningEffort | None = None) -> Completion[str]: ...

    def complete_json[T: BaseModel](self, *, model: str, system: str, user: str, schema: type[T],
                                    max_tokens: int = 2048, temperature: float = 0.1,
                                    reasoning_effort: ReasoningEffort | None = None) -> Completion[T]: ...


class LMStudioClient:
    def __init__(self, base_url: str, api_key: str, timeout: float, max_retries: int,
                 reasoning_effort: ReasoningEffort = 'none'):
        # The SDK's own retries only cover transport errors; validation retries are ours (max_retries)
        self.api = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=1)
        self.max_retries = max_retries
        self.reasoning_effort = reasoning_effort

    @classmethod
    def from_settings(cls, config) -> 'LMStudioClient':
        return cls(config.base_url, config.api_key.get_secret_value(), config.timeout, config.max_retries,
                   config.reasoning_effort)

    def list_models(self) -> list[str]:
        try:
            return [model.id for model in self.api.models.list()]
        except openai.APIError as exc:
            raise LLMError(f'LM Studio is not reachable: {exc}') from exc

    def embed(self, texts: list[str], model: str, batch: int = 32) -> list[list[float]]:
        vectors = []
        for start in range(0, len(texts), batch):
            try:
                response = self.api.embeddings.create(model=model, input=texts[start:start + batch])
            except openai.APIError as exc:
                raise LLMError(f'{model}: {exc}') from exc
            vectors += [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        return vectors

    def complete_text(self, *, model, system, user, max_tokens=1024, temperature=0.2, reasoning_effort=None):
        raw, usage = self._chat(model, system, user, max_tokens, temperature, reasoning_effort, response_format=None)
        return Completion(raw, raw, model, usage)

    def complete_json(self, *, model, system, user, schema, max_tokens=2048, temperature=0.1, reasoning_effort=None):
        response_format = {
            'type': 'json_schema',
            'json_schema': {'name': schema.__name__, 'strict': True, 'schema': schema.model_json_schema()},
        }
        usage, error = Usage(), None
        for attempt in range(1, self.max_retries + 2):
            raw, spent = self._chat(model, system, user, max_tokens, temperature, reasoning_effort, response_format)
            usage += spent
            try:
                value = schema.model_validate_json(_strip_fences(raw))
            except ValidationError as exc:
                error = exc
                continue
            return Completion(value, raw, model, usage, attempt)
        raise LLMError(f'{model}: no valid {schema.__name__} after {self.max_retries + 1} attempts: {error}')

    def _chat(self, model, system, user, max_tokens, temperature, reasoning_effort, response_format):
        started = time.monotonic()
        try:
            response = self.api.chat.completions.create(
                model=model,
                messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning_effort or self.reasoning_effort,
                **({'response_format': response_format} if response_format else {}),
            )
        except openai.APIError as exc:
            raise LLMError(f'{model}: {exc}') from exc
        return response.choices[0].message.content or '', _usage(response.usage, time.monotonic() - started)


def _usage(usage, seconds: float) -> Usage:
    if not usage:
        return Usage(seconds=seconds)
    details = getattr(usage, 'completion_tokens_details', None)
    reasoning = getattr(details, 'reasoning_tokens', None) or 0
    return Usage(usage.prompt_tokens, usage.completion_tokens, reasoning, seconds)


def _strip_fences(raw: str) -> str:
    """Some models wrap JSON in ```json fences even with a response schema."""
    text = raw.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else ''
        text = text.rsplit('```', 1)[0]
    return text.strip()
