"""Feed collection: fetch a source's feed, store new entries, extract the full text of each article."""
import calendar
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

import feedparser
import httpx
import lxml.html
import trafilatura
from lxml import etree
from django.utils import timezone

from sources.models import Article, Source

from . import cleanup
from .normalize import canonical_url, content_hash, html_to_text, normalize_paragraphs, normalize_space

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedEntry:
    url: str
    title: str
    author: str = ''
    published_at: datetime | None = None
    summary: str = ''


class Collector(Protocol):
    """Turns a source into entries. RSS now; a JSON API of a source would be another implementation."""

    def fetch(self, source: Source) -> list[FeedEntry]: ...


class TextExtractor(Protocol):
    def extract(self, url: str, boilerplate: frozenset[str] = frozenset()) -> str | None:
        """The page's main text; `boilerplate` = the source's known site chrome, to judge candidates by."""


class RssCollector:
    def __init__(self, http: httpx.Client, max_entries: int):
        self.http = http
        self.max_entries = max_entries

    def fetch(self, source: Source) -> list[FeedEntry]:
        response = self.http.get(source.feed_url)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if feed.bozo and not feed.entries:
            raise ValueError(f'not a feed: {feed.bozo_exception}')
        return [entry for raw in feed.entries[:self.max_entries] if (entry := self._entry(raw))]

    @staticmethod
    def _entry(raw) -> FeedEntry | None:
        if not raw.get('link') or not raw.get('title'):
            return None
        parsed = raw.get('published_parsed') or raw.get('updated_parsed')
        return FeedEntry(
            url=canonical_url(raw.link),
            title=normalize_space(html_to_text(raw.title)),
            author=normalize_space(raw.get('author', ''))[:300],
            published_at=datetime.fromtimestamp(calendar.timegm(parsed), UTC) if parsed else None,
            summary=html_to_text(raw.get('summary', '')),
        )


class TrafilaturaExtractor:
    """trafilatura, and the page's plain paragraphs where trafilatura picks the wrong block: The Register's
    "related articles" list instead of the story, half of an Ars Technica article."""

    # paragraphs outside navigation, lists of links, sidebars and footers
    PARAGRAPHS = etree.XPath('//p[not(ancestor::li or ancestor::nav or ancestor::aside or ancestor::footer '
                             'or ancestor::header or ancestor::form)]')
    MIN_PARAGRAPH = 40  # shorter <p> are captions, bylines, buttons
    FALLBACK_FACTOR = 2  # the paragraphs win only when they hold this many times more text

    def __init__(self, http: httpx.Client):
        self.http = http

    def extract(self, url: str, boilerplate: frozenset[str] = frozenset()) -> str | None:
        response = self.http.get(url)
        response.raise_for_status()
        main = trafilatura.extract(response.text, url=url, include_comments=False, include_tables=False) or ''
        paragraphs = self._paragraphs(response.content)

        def useful(text):
            return len(cleanup.clean_text(normalize_paragraphs(text), [], boilerplate).text)

        return paragraphs if useful(paragraphs) > self.FALLBACK_FACTOR * useful(main) else main or None

    def _paragraphs(self, content: bytes) -> str:
        try:
            tree = lxml.html.fromstring(content)
        except (etree.ParserError, ValueError):
            return ''
        texts = (normalize_space(p.text_content()) for p in self.PARAGRAPHS(tree))
        return '\n'.join(text for text in texts if len(text) >= self.MIN_PARAGRAPH)


@dataclass
class CollectStats:
    new: int = 0
    known: int = 0
    duplicates: int = 0
    extract_failed: int = 0
    recovered: int = 0  # earlier articles whose full text came on a retry
    errors: list[str] = field(default_factory=list)


def collect_source(source: Source, collector: Collector, extractor: TextExtractor | None) -> CollectStats:
    """Store the source's new entries. Known URLs are skipped, so reruns are safe."""
    stats = CollectStats()
    try:
        entries = collector.fetch(source)
    except (httpx.HTTPError, ValueError) as exc:
        source.last_error = f'{type(exc).__name__}: {exc}'
        source.save(update_fields=['last_error'])
        stats.errors.append(source.last_error)
        return stats

    if not source.full_text:
        extractor = None
    known = set(Article.objects.filter(canonical_url__in=[e.url for e in entries]).values_list('canonical_url', flat=True))
    stored = []
    for entry in entries:
        if entry.url in known:
            stats.known += 1
            continue
        stored.append(_store(source, entry, extractor, stats))
    if extractor:
        retried = _retry_failed(source, extractor)
        # after the whole batch: on a source's first run the batch is the only history to find boilerplate in
        clean_articles(source, stored + retried)
        stats.extract_failed = sum(not article.text for article in stored)
        stats.recovered = sum(bool(article.text) for article in retried)

    source.last_polled_at = timezone.now()
    source.last_error = ''
    source.save(update_fields=['last_polled_at', 'last_error'])
    return stats


def _retry_failed(source: Source, extractor: TextExtractor) -> list[Article]:
    """Download recent articles without full text again: a teaser served one hour is often the article
    the next. Each article gets MAX_EXTRACT_ATTEMPTS downloads within RETRY_WINDOW, then it stays on the summary."""
    articles = list(source.articles.filter(
        status=Article.Status.EXTRACT_FAILED,
        extract_attempts__lt=MAX_EXTRACT_ATTEMPTS,
        fetched_at__gte=timezone.now() - RETRY_WINDOW,
    ))
    for article in articles:
        article.extract_attempts += 1
        if text := extract_text(extractor, article.canonical_url, frozenset(source.boilerplate)):
            article.text, article.status = text, Article.Status.EXTRACTED
        article.save(update_fields=['text', 'status', 'extract_attempts'])
    return articles


def _store(source: Source, entry: FeedEntry, extractor: TextExtractor | None, stats: CollectStats) -> Article:
    text, status = '', Article.Status.NEW
    if extractor:
        text = extract_text(extractor, entry.url, frozenset(source.boilerplate))
        status = Article.Status.EXTRACTED if text else Article.Status.EXTRACT_FAILED

    digest = content_hash(entry.title, text or entry.summary)
    if Article.objects.filter(content_hash=digest).exists():
        status = Article.Status.DUPLICATE
        stats.duplicates += 1

    stats.new += 1
    return Article.objects.create(
        source=source,
        canonical_url=entry.url,
        title=entry.title[:500],
        author=entry.author,
        published_at=entry.published_at,
        summary=entry.summary,
        text=text,
        content_hash=digest,
        status=status,
        extract_attempts=int(bool(extractor)),
    )


TEASER_RETRIES = 1
RETRY_PAUSE = 3.0  # seconds
MAX_EXTRACT_ATTEMPTS = 3  # collect runs that download an article without full text
RETRY_WINDOW = timedelta(hours=24)


def extract_text(extractor: TextExtractor, url: str, boilerplate: frozenset[str] = frozenset(),
                 sleep=time.sleep) -> str:
    """The page's text. A teaser-short text is downloaded again: CoinDesk serves now the article, now a page
    without its body, for the same URL."""
    text = ''
    for attempt in range(1 + TEASER_RETRIES):
        if attempt:
            sleep(RETRY_PAUSE)
        try:
            text = normalize_paragraphs(extractor.extract(url, boilerplate) or '')
        except httpx.HTTPError as exc:
            log.info('extract %s: %s', url, exc)
            return text
        if len(text) >= cleanup.MIN_TEXT_CHARS:
            break
    return text


def clean_articles(source: Source, articles: list[Article]) -> list[Article]:
    """Cut the source's boilerplate from the articles' texts and learn new boilerplate. A text too short after
    that (teaser, paywall, redirect page) is not the article: it moves to the summary if the feed gave none,
    and the article is marked extract_failed."""
    others = list(source.articles.exclude(text='').order_by('-id').values_list('pk', 'text')[:cleanup.HISTORY])
    known = set(source.boilerplate)
    found = set()
    for article in articles:
        if not article.text:
            continue
        cleaned = cleanup.clean_text(article.text, [text for pk, text in others if pk != article.pk], known)
        found |= cleaned.boilerplate
        if cleaned.is_full:
            article.text = cleaned.text
        else:
            article.summary = article.summary or cleaned.text
            article.text = ''
            if article.status == Article.Status.EXTRACTED:
                article.status = Article.Status.EXTRACT_FAILED
        article.save(update_fields=['text', 'summary', 'status'])
    if found - known:
        source.boilerplate = (list(found - known) + source.boilerplate)[:cleanup.MAX_KNOWN]
        source.save(update_fields=['boilerplate'])
    return articles
