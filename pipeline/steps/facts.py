"""Event -> verified facts (spec §8, steps 1-4).

1. The worker model extracts facts, each with the article it comes from and a verbatim quote.
2. Code: the quote is in that article, the statement's numbers are in it.
3. The worker model checks each statement against the paragraph of its quote, and code checks the model's answer
   (its quote, numbers and digit-bearing names must be in that text: alone the model accepted 13% of traps).
"""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from sources.models import Article
from verification.text_match import claim_problems, normalize, quote_in_text, unsupported_numbers

from .common import ModelStep, article_block

EXTRACT_SYSTEM = """You extract verifiable facts about one news event from the articles given.
For every fact give:
- statement: one short self-contained sentence in English;
- article_id: the id of the article it comes from;
- quote: the exact sentence or phrase from that article that states it, copied verbatim;
- kind: number | date | version | price | quote | general.
Extract only what the articles state. Never add numbers, dates, names or conclusions that are not in the quote.
Prefer concrete facts: who, what, when, how much, which version.
Keep in each statement who did it, who says it, and how it ended (tried, failed, planned, alleged), as the quote does.
At most 12 facts. Answer with JSON only."""

VERIFY_SYSTEM = """You check one statement against news articles.
- supports: the articles state it (same numbers, dates, names and meaning);
- contradicts: the articles state something incompatible (for example a different number or date);
- unclear: the articles do not say it.
Be strict: a statement with any detail the articles do not give is not supported.
Answer with JSON only: verdict, and the exact quote from the articles your verdict rests on (empty if unclear)."""

TEXT_BUDGET = 8000  # characters for all articles together (~2k tokens): fits a 9B model's context with room
CONTEXT_PARAGRAPHS = 1  # around the quote's paragraph, for the check


class ExtractedFact(BaseModel):
    statement: str
    article_id: int
    quote: str
    kind: Literal['number', 'date', 'version', 'price', 'quote', 'general']


class ExtractAnswer(BaseModel):
    facts: list[ExtractedFact]


class VerifyAnswer(BaseModel):
    verdict: Literal['supports', 'contradicts', 'unclear']
    quote: str


@dataclass(frozen=True)
class CheckedFact:
    fact: ExtractedFact
    article: Article | None
    problems: list[str]  # empty = verified

    @property
    def verified(self) -> bool:
        return not self.problems


def extraction_problems(fact: ExtractedFact, articles: dict[int, Article]) -> list[str]:
    article = articles.get(fact.article_id)
    if article is None:
        return [f'unknown article_id {fact.article_id}']
    text = f'{article.title}\n{article.body}'
    problems = []
    if not quote_in_text(fact.quote, text):
        problems.append('quote not in article')
    if missing := unsupported_numbers(fact.statement, text):
        problems.append(f'numbers not in article: {sorted(missing)}')
    return problems


def quote_context(quote: str, article: Article) -> str:
    """The paragraph holding the quote and its neighbours: enough for the check, short enough to be quick."""
    paragraphs = [p for p in f'{article.title}\n{article.body}'.split('\n') if p.strip()]
    for index, paragraph in enumerate(paragraphs):
        if quote_in_text(quote, paragraph) or normalize(quote)[:60] in normalize(paragraph):
            start, end = max(0, index - CONTEXT_PARAGRAPHS), index + CONTEXT_PARAGRAPHS + 1
            return '\n'.join(paragraphs[start:end])
    return '\n'.join(paragraphs)[:3000]


class FactExtractor(ModelStep):
    max_tokens = 2048

    def extract(self, title: str, articles: list[Article]) -> list[ExtractedFact]:
        per_article = TEXT_BUDGET // len(articles)
        user = '\n\n'.join(article_block(article, per_article) for article in articles)
        return self.ask(f'{EXTRACT_SYSTEM}\nEvent: {title}', user, ExtractAnswer).value.facts


class FactVerifier(ModelStep):
    max_tokens = 400

    def verify(self, statement: str, article: Article, context: str) -> list[str]:
        """Problems with the statement; empty = the model supports it and the code agrees."""
        block = f'<article id="{article.pk}" source="{article.source.name}">\n{context}\n</article>'
        answer = self.ask(VERIFY_SYSTEM, f'Statement: {statement}\n\n{block}', VerifyAnswer).value
        if answer.verdict != 'supports':
            return [f'model: {answer.verdict}']
        return claim_problems(statement, answer.quote, context)


def check_facts(extractor: FactExtractor, verifier: FactVerifier, title: str,
                articles: list[Article]) -> list[CheckedFact]:
    by_id = {article.pk: article for article in articles}
    checked = []
    for fact in extractor.extract(title, articles):
        article = by_id.get(fact.article_id)
        problems = extraction_problems(fact, by_id)
        if not problems:
            # only facts that passed the code checks cost a model call
            problems = verifier.verify(fact.statement, article, quote_context(fact.quote, article))
        checked.append(CheckedFact(fact, article, problems))
    return checked
