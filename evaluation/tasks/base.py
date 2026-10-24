"""A stage 0 task: builds cases from the labeled test set, asks the model, scores each answer."""
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import BaseModel

from llm.client import Completion, LLMClient, ReasoningEffort
from pipeline.steps.common import UNTRUSTED_NOTE, article_block  # noqa: F401 (tasks import it here)
from sources.models import Article

from ..models import EvalEvent, EvalResult

@dataclass
class Case:
    key: str  # shown in the progress output
    expected: Any
    event: EvalEvent | None = None
    article: Article | None = None
    data: dict = field(default_factory=dict)  # task-specific input, stored with the result


@dataclass(frozen=True)
class Score:
    correct: bool | None  # None = needs a human (the admin's manual score)
    details: dict = field(default_factory=dict)


class Task(ABC):
    name: ClassVar[str]
    schema: ClassVar[type[BaseModel]]  # the model's answer; also restores stored answers for rescoring
    role: ClassVar[str] = 'worker'  # which configured model runs it by default: worker | writer
    max_tokens: ClassVar[int] = 1024

    def __init__(self, client: LLMClient, model: str, reasoning_effort: ReasoningEffort | None = None):
        self.client = client
        self.model = model
        self.reasoning_effort = reasoning_effort

    @abstractmethod
    def cases(self) -> Iterator[Case]: ...

    @abstractmethod
    def ask(self, case: Case) -> Completion: ...

    @abstractmethod
    def score(self, case: Case, completion: Completion) -> Score: ...

    def summarize(self, results: list[EvalResult]) -> dict:
        """Task-specific metrics on top of the common ones (accuracy, speed, errors)."""
        return {}

    def complete_json(self, system: str, user: str, schema):
        return self.client.complete_json(
            model=self.model, system=f'{system}\n{UNTRUSTED_NOTE}', user=user, schema=schema,
            max_tokens=self.max_tokens, reasoning_effort=self.reasoning_effort,
        )


def events_with_articles():
    return EvalEvent.objects.prefetch_related('articles__source', 'facts').order_by('id')
