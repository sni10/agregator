"""Site chrome in extracted text: promo blocks, price tickers, share buttons, teasers instead of articles.

trafilatura keeps some of it, and it carries numbers that are not the article's ("5-7% yield", "BTC $81,282"):
the number checks of verification/ would accept a model's fact backed only by such junk.
"""
import re
from dataclasses import dataclass, field

MIN_REPEATS = 3  # a paragraph in this many other articles of the source is site chrome, not content
HISTORY = 50  # recent articles of the source to compare with
MAX_KNOWN = 300  # boilerplate paragraphs remembered per source
MIN_TEXT_CHARS = 600  # less after cleaning: a teaser, a paywall or a redirect page, not the article
# "BTC $81,282.50 Up 0.11 percent 0.11%": a price ticker
TICKER_ITEM = re.compile(r'\b(?:up|down) [\d.]+ percent\b', re.IGNORECASE)
MIN_TICKER_ITEMS = 3


@dataclass(frozen=True)
class Cleaned:
    text: str
    removed: int  # paragraphs dropped
    boilerplate: set[str] = field(default_factory=set)  # repeated paragraphs found in this text

    @property
    def is_full(self) -> bool:
        return len(self.text) >= MIN_TEXT_CHARS


def clean_text(text: str, others: list[str], known: set[str] = frozenset()) -> Cleaned:
    """Drop the source's boilerplate paragraphs, price tickers and repeats inside the text.

    Boilerplate is a paragraph already known for the source, or one found in MIN_REPEATS of `others`
    (texts of other articles of the source, with paragraphs or flattened). Found ones are returned to be remembered:
    cleaned texts no longer contain them, so the next run could not find them by repetition.
    """
    kept, seen, found = [], set(), set()
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    for paragraph in paragraphs:
        if paragraph in seen or paragraph in known or is_ticker(paragraph):
            continue
        if sum(paragraph in other for other in others) >= MIN_REPEATS:
            found.add(paragraph)
            continue
        seen.add(paragraph)
        kept.append(paragraph)
    return Cleaned('\n'.join(kept), len(paragraphs) - len(kept), found)


def is_ticker(paragraph: str) -> bool:
    return len(TICKER_ITEM.findall(paragraph)) >= MIN_TICKER_ITEMS
