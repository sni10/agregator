from pydantic import BaseModel

from .common import ModelStep

WRITE_SYSTEM = """You write an English news story about one event for a technology news site.
Use only the verified facts given. Do not add numbers, dates, names, quotes, background or opinions that are not in
the facts. If the facts leave something open, leave it open.
Keep who did what, and with what result, as the facts say. Do not join two facts with a cause, a purpose, a means
or an order in time that no fact states: put them in separate sentences. Neutral, concrete, no hype, no filler.
- headline: up to 90 characters, factual;
- summary: one sentence;
- body: 150 to 250 words in 3 or 4 paragraphs separated by a blank line: what happened; the details; the
  background and reactions; what happens next. Skip a part the facts do not cover. With few facts write less:
  never pad, never repeat a fact;
- image_queries: 2 or 3 searches for a stock photo above the story, 2 to 4 English words each, abstract: objects,
  scenes, concepts ("cybersecurity lock server", "blockchain network abstract"). No names of people, companies,
  products or brands, no logos, no text.
Answer with JSON only."""


class CardAnswer(BaseModel):
    headline: str
    summary: str
    body: str
    image_queries: list[str]


def write_prompt(facts: list[str], sources: list[str]) -> str:
    facts_text = '\n'.join(f'- {fact}' for fact in facts)
    sources_text = '\n'.join(f'- {source}' for source in sources)
    return f'Verified facts:\n{facts_text}\n\nSources (for context only, do not cite their wording):\n{sources_text}'


class CardWriter(ModelStep):
    max_tokens = 1024

    def write(self, facts: list[str], sources: list[str]) -> CardAnswer:
        return self.ask(WRITE_SYSTEM, write_prompt(facts, sources), CardAnswer).value
