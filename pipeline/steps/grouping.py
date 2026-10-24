"""Articles -> events. Embeddings find candidates, the worker model confirms: a false merge glues two stories
into one card, so a candidate event takes an article only if the article is close to every member (complete
linkage) and the model says it is the same happening as the closest member (stage 0: precision 0.987)."""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
from pydantic import BaseModel

from llm.client import LLMClient, ReasoningEffort
from sources.models import Article

from ..models import ArticleEmbedding, Event
from .common import ModelStep, article_block

SAME_EVENT_SYSTEM = """You decide whether two news articles report the same specific event.
Same event: the same release, incident, deal, ruling, announcement or study, even if the articles differ in detail.
Not the same event: the same company, product or topic but a different happening (another release, another day's
announcement, a follow-up development, an opinion piece about the general topic).
Answer with JSON only: same_event, and a one-sentence reason."""

EMBED_CHARS = 1000  # title + start of the text: enough to tell stories apart, fits nomic's 2048-token context
NOMIC_PREFIX = 'clustering: '  # nomic-embed-text expects a task prefix
COMPARE_CHARS = 1500  # of each article, for the model's yes/no


class SameEventAnswer(BaseModel):
    same_event: bool
    reason: str


def embedding_text(article: Article) -> str:
    return f'{NOMIC_PREFIX}{article.title}\n{article.body[:EMBED_CHARS]}'


@dataclass
class GroupingStats:
    articles: int = 0
    embedded: int = 0
    attached: int = 0  # joined an existing event
    new_events: int = 0
    asked: int = 0  # model confirmations
    rejected: list[str] = field(default_factory=list)  # candidates the model said no to, with its reason


class Grouper(ModelStep):
    max_tokens = 200

    def __init__(self, client: LLMClient, model: str, embedding_model: str, threshold: float, window: timedelta,
                 reasoning_effort: ReasoningEffort | None = None):
        super().__init__(client, model, reasoning_effort)
        self.embedding_model = embedding_model
        self.threshold = threshold
        self.window = window

    def group(self, articles: list[Article], progress: Callable[[str], None] = lambda line: None) -> GroupingStats:
        """Each article, oldest first, joins the best open event or starts a new one."""
        stats = GroupingStats(articles=len(articles))
        if not articles:
            return stats
        stats.embedded = self._embed_missing(articles)
        oldest = min(a.published_at or a.fetched_at for a in articles)
        events = list(Event.objects.filter(last_article_at__gte=oldest - self.window)
                      .exclude(status=Event.Status.FAILED).prefetch_related('articles__source'))
        members = {event.pk: list(event.articles.all()) for event in events}
        vectors = self._vectors([a for group in members.values() for a in group] + articles)

        for number, article in enumerate(articles, 1):
            event = self._find_event(article, events, members, vectors, stats)
            if event is None:
                event = Event.objects.create(title=article.title[:500])
                events.append(event)
                members[event.pk] = []
                stats.new_events += 1
                mark = 'new event'
            else:
                stats.attached += 1
                mark = f'-> event {event.pk} "{members[event.pk][0].title[:50]}"'
            members[event.pk].append(article)
            _attach(event, article)
            progress(f'{number:>4}/{len(articles)} {article.source.slug}: {article.title[:60]}  {mark}')
        return stats

    def _find_event(self, article, events, members, vectors, stats) -> Event | None:
        candidates = []
        for event in events:
            group = members[event.pk]
            if not group or not self._in_window(article, event):
                continue
            similarities = [float(vectors[article.pk] @ vectors[member.pk]) for member in group]
            if min(similarities) >= self.threshold:
                closest = group[int(np.argmax(similarities))]
                candidates.append((min(similarities), event, closest))
        for _, event, closest in sorted(candidates, key=lambda c: c[0], reverse=True):
            stats.asked += 1
            answer = self.same_event(article, closest)
            if answer.same_event:
                return event
            stats.rejected.append(f'{article.pk} vs {closest.pk}: {answer.reason}')
        return None

    def same_event(self, a: Article, b: Article) -> SameEventAnswer:
        user = f'{article_block(a, COMPARE_CHARS)}\n\n{article_block(b, COMPARE_CHARS)}'
        return self.ask(SAME_EVENT_SYSTEM, user, SameEventAnswer).value

    def _in_window(self, article: Article, event: Event) -> bool:
        published = article.published_at or article.fetched_at
        return event.last_article_at is None or abs(published - event.last_article_at) <= self.window

    def _embed_missing(self, articles: list[Article]) -> int:
        done = set(ArticleEmbedding.objects.filter(article__in=articles, model=self.embedding_model)
                   .values_list('article_id', flat=True))
        missing = [a for a in articles if a.pk not in done]
        if missing:
            raw = np.array(self.client.embed([embedding_text(a) for a in missing], self.embedding_model),
                           dtype=np.float32)
            normalized = raw / np.linalg.norm(raw, axis=1, keepdims=True)
            ArticleEmbedding.objects.bulk_create(
                [ArticleEmbedding(article=a, model=self.embedding_model, vector=v.tolist())
                 for a, v in zip(missing, normalized, strict=True)],
                update_conflicts=True, unique_fields=['article'], update_fields=['model', 'vector'],
            )
        return len(missing)

    def _vectors(self, articles: list[Article]) -> dict[int, np.ndarray]:
        rows = ArticleEmbedding.objects.filter(article__in=articles).values_list('article_id', 'vector')
        return {pk: np.array(vector, dtype=np.float32) for pk, vector in rows}


def _attach(event: Event, article: Article) -> None:
    published = article.published_at or article.fetched_at
    event.articles.add(article)
    event.first_published_at = min(filter(None, [event.first_published_at, published]))
    event.last_article_at = max(filter(None, [event.last_article_at, published]))
    if event.status == Event.Status.DRAFTED:
        event.has_new_articles = True
    event.save(update_fields=['first_published_at', 'last_article_at', 'has_new_articles', 'updated_at'])


def ungrouped_articles(since) -> list[Article]:
    """Articles with some text, not duplicates, not in an event yet, published since `since`, oldest first."""
    return list(
        Article.objects.exclude(status=Article.Status.DUPLICATE).filter(events=None)
        .filter(published_at__gte=since).exclude(text='', summary='')
        .select_related('source').order_by('published_at', 'id')
    )
