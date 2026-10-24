from types import SimpleNamespace

from django.test import SimpleTestCase
from pydantic import BaseModel

from llm.client import LLMError, LMStudioClient


class Answer(BaseModel):
    category: str


class FakeCompletions:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.contents.pop(0))
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def client_answering(*contents, max_retries=1):
    client = LMStudioClient('http://test/v1', 'key', timeout=10, max_retries=max_retries)
    completions = FakeCompletions(contents)
    client.api = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def ask(client):
    return client.complete_json(model='m', system='s', user='u', schema=Answer)


class CompleteJsonTests(SimpleTestCase):
    def test_validates_answer_and_sends_schema(self):
        client, completions = client_answering('{"category": "ai"}')

        result = ask(client)

        self.assertEqual((result.value, result.attempts, result.usage.completion_tokens), (Answer(category='ai'), 1, 5))
        self.assertEqual(completions.calls[0]['response_format']['json_schema']['name'], 'Answer')

    def test_turns_thinking_off_unless_asked(self):
        client, completions = client_answering('{"category": "ai"}', '{"category": "ai"}')

        ask(client)
        client.complete_json(model='m', system='s', user='u', schema=Answer, reasoning_effort='low')

        self.assertEqual([call['reasoning_effort'] for call in completions.calls], ['none', 'low'])

    def test_accepts_json_in_code_fences(self):
        client, _ = client_answering('```json\n{"category": "it"}\n```')

        self.assertEqual(ask(client).value.category, 'it')

    def test_retries_invalid_json_and_sums_usage(self):
        client, _ = client_answering('not json', '{"category": "crypto"}')

        result = ask(client)

        self.assertEqual((result.value.category, result.attempts, result.usage.prompt_tokens), ('crypto', 2, 20))

    def test_raises_after_last_retry(self):
        client, completions = client_answering('{}', '{"wrong": 1}')

        with self.assertRaisesMessage(LLMError, 'no valid Answer after 2 attempts'):
            ask(client)
        self.assertEqual(len(completions.calls), 2)
