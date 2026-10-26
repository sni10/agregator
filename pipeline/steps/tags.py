"""Tags for a written story: the subjects that run through many stories (companies, products, coins, topics), so a
reader can open every story on one of them. Reusing the tags already in use matters more than the perfect word:
"AI coding agents" once, not "AI coding", "Coding agents" and "AI-powered coding" on three stories."""
import re

from pydantic import BaseModel

from .common import ModelStep

TAG_SYSTEM = """You tag a story of a technology news site. A tag gathers every story on one subject, so a reader can
follow it.
Give 2 to 5 tags, the most important first:
- the main companies, products, projects, coins or people the story is about (not ones only mentioned in passing);
- one or two topics: what kind of news it is, e.g. "AI coding agents", "Data breaches", "Bitcoin ETFs",
  "Chip export controls".
Reuse a tag from the list of tags in use whenever it fits, spelled exactly as listed. Make a new one only for a
subject the list lacks.
A tag is 1 to 4 words: names in their official spelling (OpenAI, iPhone, CoinDCX), topics with a capital first letter
only. No tags that fit almost any story of the site: no "AI", "IT", "Crypto", "Technology", "News", "Security",
"Cryptocurrency", "Artificial intelligence"; no dates, numbers or opinions.
Answer with JSON only."""

MAX_TAGS = 5
MAX_LENGTH = 60
MAX_WORDS = 5
TOO_WIDE = {'ai', 'it', 'crypto', 'technology', 'tech', 'news', 'security', 'cybersecurity', 'cryptocurrency',
            'cryptocurrencies', 'artificial intelligence', 'blockchain', 'software', 'internet'}


class TagAnswer(BaseModel):
    tags: list[str]


def key(tag: str) -> str:
    """One key for every spelling of a tag: "AI-coding agents" = "ai coding agents" (the site's slug does the same)."""
    return ' '.join(re.findall(r'[a-z0-9]+', tag.lower()))


def clean(tags: list[str], known: list[str]) -> list[str]:
    """The model's tags in the spelling already in use, without duplicates, too wide or malformed ones."""
    spelling = {key(tag): tag for tag in known}
    result = {}
    for tag in tags:
        tag = ' '.join(tag.strip(' #"\'.').split())
        k = key(tag)
        if not k or k in TOO_WIDE or len(tag) > MAX_LENGTH or len(tag.split()) > MAX_WORDS:
            continue
        result.setdefault(k, spelling.get(k, tag))
    return list(result.values())[:MAX_TAGS]


def tag_prompt(headline: str, summary: str, body: str, known: list[str]) -> str:
    in_use = ', '.join(known) if known else '(none yet)'
    return f'Tags in use: {in_use}\n\nStory:\n{headline}\n{summary}\n\n{body}'


class Tagger(ModelStep):
    max_tokens = 256

    def tag(self, headline: str, summary: str, body: str, known: list[str]) -> list[str]:
        answer = self.ask(TAG_SYSTEM, tag_prompt(headline, summary, body, known), TagAnswer).value
        return clean(answer.tags, known)
