from typing import Literal

from pydantic import BaseModel

from sources.models import Article

from .common import ModelStep, article_block

CATEGORIES = """Categories:
- it: software development, infrastructure, cybersecurity (hacks, breaches, vulnerabilities, malware, certificates,
  encryption), open source, hardware, big tech business
- ai: machine learning, LLMs, generative AI, AI agents, AI research, AI chips and AI policy
- crypto: cryptocurrencies and blockchains only: Bitcoin, Ethereum, stablecoins, tokens, DeFi, exchanges,
  crypto ETFs, and hacks or regulation of those
- other: anything else (not technology news)
"crypto" means cryptocurrency, not cryptography: a hack, breach or vulnerability is "it" unless it is about
cryptocurrency or a blockchain. If an article fits several categories, pick the one it is mainly about."""

ARTICLE_SYSTEM = f"""You classify technology news articles for an English news site.
{CATEGORIES}
Answer with JSON only."""

EVENT_SYSTEM = f"""You classify one news event, given the articles that report it, for an English technology news site.
{CATEGORIES}
high_risk: true if the event is a hack, theft or breach; criminal charges, arrests or lawsuits; fines, sanctions or
regulation; or crypto asset prices and fund flows. Such news needs two independent sources before it is published.
Answer with JSON only."""

Category = Literal['it', 'ai', 'crypto', 'other']


class ArticleAnswer(BaseModel):
    category: Category


class EventAnswer(BaseModel):
    category: Category
    high_risk: bool


class EventClassifier(ModelStep):
    max_tokens = 64
    CHARS = 1500  # of the first article; the others by title only

    def classify(self, articles: list[Article]) -> EventAnswer:
        first, *others = articles
        titles = '\n'.join(f'- {article.source.name}: {article.title}' for article in others)
        user = article_block(first, self.CHARS) + (f'\n\nOther articles on the event:\n{titles}' if titles else '')
        return self.ask(EVENT_SYSTEM, user, EventAnswer).value
