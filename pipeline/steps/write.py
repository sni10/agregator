from pydantic import BaseModel

from .common import ModelStep

WRITE_SYSTEM = """You write an English news story about one event for a technology news site.
Use only the verified facts given. Do not add numbers, dates, names, quotes, background or opinions that are not in
the facts. If the facts leave something open, leave it open.
Keep who did what, and with what result, as the facts say. Do not join two facts with a cause, a purpose, a means
or an order in time that no fact states: put them in separate sentences. Neutral, concrete, no hype, no filler.
- headline: up to 90 characters, factual;
- summary: one sentence;
- body: the length given after the facts, in paragraphs separated by a blank line: what happened; the details;
  the background and reactions; what happens next. Skip a part the facts do not cover. If the facts run out
  before the length, stop: never pad, never repeat a fact;
- image_queries: 2 or 3 searches for a stock photo above the story, 2 to 4 English words each, abstract: objects,
  scenes, concepts ("cybersecurity lock server", "blockchain network abstract"). No names of people, companies,
  products or brands, no logos, no text.
Answer with JSON only."""


class CardAnswer(BaseModel):
    headline: str
    summary: str
    body: str
    image_queries: list[str]


# Body length by how much there is to tell: a one-site note stays short, a story many outlets covered gets room.
# (sites, facts, words, paragraphs): the first row both counts reach; below all of them, SHORT.
LENGTHS = [
    (6, 16, '400 to 600', '5 to 7'),  # few: only stories with plenty to tell
    (3, 7, '250 to 400', '3 to 5'),
]
SHORT = ('120 to 180', '2 or 3')


def body_length(sites: int, facts: int) -> str:
    words, paragraphs = next(((w, p) for min_sites, min_facts, w, p in LENGTHS
                              if sites >= min_sites and facts >= min_facts), SHORT)
    return f'{words} words in {paragraphs} paragraphs'


def write_prompt(facts: list[str], sources: list[str], sites: int | None = None) -> str:
    """sites: how many different sites wrote about it (the sources may hold several articles of one site)."""
    facts_text = '\n'.join(f'- {fact}' for fact in facts)
    sources_text = '\n'.join(f'- {source}' for source in sources)
    length = body_length(len(sources) if sites is None else sites, len(facts))
    return (f'Verified facts:\n{facts_text}\n\nSources (for context only, do not cite their wording):\n{sources_text}'
            f'\n\nBody length: {length}.')


class CardWriter(ModelStep):
    max_tokens = 2048  # 600 words of body are ~800 tokens, plus the JSON around them

    def write(self, facts: list[str], sources: list[str], sites: int | None = None) -> CardAnswer:
        return self.ask(WRITE_SYSTEM, write_prompt(facts, sources, sites), CardAnswer).value
