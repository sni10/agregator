from pydantic import BaseModel

from verification.card_check import Card

from .common import ModelStep

WRITE_SYSTEM = """You write a short English news card about one event for a technology news site.
Use only the verified facts given. Do not add numbers, dates, names, quotes, background or opinions that are not in
the facts. If the facts leave something open, leave it open.
Keep who did what, and with what result, as the facts say. Do not join two facts with a cause, a purpose, a means
or an order in time that no fact states: put them in separate sentences. Neutral, concrete, no hype, no filler.
- headline: up to 90 characters, factual;
- summary: one sentence;
- body: 3 to 5 sentences.
Answer with JSON only."""


class CardAnswer(BaseModel):
    headline: str
    summary: str
    body: str


def write_prompt(facts: list[str], sources: list[str]) -> str:
    facts_text = '\n'.join(f'- {fact}' for fact in facts)
    sources_text = '\n'.join(f'- {source}' for source in sources)
    return f'Verified facts:\n{facts_text}\n\nSources (for context only, do not cite their wording):\n{sources_text}'


class CardWriter(ModelStep):
    max_tokens = 1024

    def write(self, facts: list[str], sources: list[str]) -> Card:
        answer = self.ask(WRITE_SYSTEM, write_prompt(facts, sources), CardAnswer).value
        return Card(answer.headline, answer.summary, answer.body)
