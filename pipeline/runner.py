"""The pipeline (spec §5): new articles -> events -> facts -> card -> card check -> draft.

Phases by model, so each big model is loaded once per run:
- worker (9B + embeddings): group articles into events; classify ready events; extract and verify their facts;
- writer (27B): write a card for each event with enough verified facts;
- worker again: check every sentence of each new card, set the draft's status;
- no model: a stock photo for each checked draft (Pixabay), then ready and review drafts to the site (publisher/).
Every step keeps its result in the DB: a run that stops halfway is continued by the next one.
"""
from collections.abc import Callable
from datetime import timedelta

import httpx
from django.conf import settings
from django.db import transaction
from django.db.models import Count, OuterRef, Subquery
from django.utils import timezone

from llm.client import LLMClient, LLMError
from publisher import outbox
from verification.card_check import Card, CardChecker, checks_to_json

from .models import Draft, Event, Fact, PipelineRun
from .models_manager import LoadSpec, LmsModelManager, NoModelManager
from .steps.classify import EventClassifier
from .steps.facts import FactExtractor, FactVerifier, check_facts
from .steps.grouping import Grouper, ungrouped_articles
from .steps.images import ImageError, ImagePicker, Pixabay
from .steps.write import CardWriter

STAGES = ('group', 'facts', 'write', 'check', 'images', 'publish')
MIN_INDEPENDENT_SOURCES = 2  # for high-risk events (spec §8 gates)
MAX_IMAGE_QUERIES = 3


class Pipeline:
    def __init__(self, client: LLMClient, lm_studio, config, manager: LmsModelManager | NoModelManager,
                 progress: Callable[[str], None] = print, publishing=None, pixabay=None,
                 events: list[int] | None = None):
        self.client = client
        self.only = events  # rewrite just these events, whatever their status (pipeline --event)
        self.publishing = publishing or settings.PUBLISHING
        self.pixabay = pixabay or settings.PIXABAY
        self.config = config
        self.progress = progress
        self.manager = manager
        self.worker_model = lm_studio.worker_model
        self.writer_model = lm_studio.writer_model or lm_studio.worker_model
        self.embedding_model = lm_studio.embedding_model
        self.worker = (LoadSpec(self.worker_model, config.worker_context), LoadSpec(self.embedding_model))
        self.writer = (LoadSpec(self.writer_model, config.writer_context, config.writer_gpu),)
        self.window = timedelta(days=config.window_days)

    def run(self, stages=STAGES) -> PipelineRun:
        run = PipelineRun.objects.create()
        try:
            for stage in STAGES:
                if stage in stages:
                    self.progress(f'== {stage}')
                    run.stats[stage] = getattr(self, stage)()
                    run.save(update_fields=['stats'])
        except Exception as exc:
            run.error = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            run.finished_at = timezone.now()
            run.save(update_fields=['finished_at', 'stats', 'error'])
        return run

    # --- worker: events --------------------------------------------------------------------------------------

    def group(self) -> dict:
        if self.only:
            return {'articles': 0, 'skipped': 'rewriting chosen events only'}
        articles = ungrouped_articles(timezone.now() - self.window)
        self.progress(f'{len(articles)} new articles')
        if not articles:
            return {'articles': 0}
        self.manager.use(*self.worker)
        grouper = Grouper(self.client, self.worker_model, self.embedding_model, self.config.group_threshold,
                          self.window)
        stats = grouper.group(articles, self.progress)
        return {'articles': stats.articles, 'embedded': stats.embedded, 'attached': stats.attached,
                'new_events': stats.new_events, 'model_checks': stats.asked, 'rejected': stats.rejected[:50]}

    def ready_events(self) -> list[Event]:
        """Waiting events that may get facts now: two sources, or one that waited long enough for others.
        The most covered first: they matter most and are the safest."""
        sources = Count('articles__source', distinct=True)
        if self.only:
            return list(Event.objects.filter(pk__in=self.only).annotate(source_count=sources)
                        .prefetch_related('articles__source'))
        now = timezone.now()
        events = (Event.objects.filter(status=Event.Status.COLLECTING, last_article_at__gte=now - self.window)
                  .annotate(source_count=sources)
                  .prefetch_related('articles__source').order_by('-source_count', '-last_article_at'))
        wait = now - timedelta(hours=self.config.single_source_wait_hours)
        ready = [e for e in events if e.source_count >= 2 or (e.first_published_at and e.first_published_at <= wait)]
        return ready[:self.config.max_events]

    def facts(self) -> dict:
        events = self.ready_events()
        self.progress(f'{len(events)} events ready')
        counts = {'events': len(events), 'verified': 0, 'rejected': 0, 'skipped': 0, 'failed': 0}
        if not events:
            return counts
        self.manager.use(*self.worker)
        classifier = EventClassifier(self.client, self.worker_model)
        extractor = FactExtractor(self.client, self.worker_model)
        verifier = FactVerifier(self.client, self.worker_model)
        for number, event in enumerate(events, 1):
            articles = sorted(event.articles.all(), key=lambda a: (a.source.trust_tier, a.published_at or a.fetched_at))
            prefix = f'{number:>3}/{len(events)} event {event.pk} ({event.source_count} sources): {event.title[:60]}'
            try:
                result = self._event_facts(event, articles, classifier, extractor, verifier)
            except LLMError as exc:
                self._fail(event, exc)
                counts['failed'] += 1
                self.progress(f'{prefix} FAILED {exc}')
                continue
            counts['verified'] += result['verified']
            counts['rejected'] += result['rejected']
            counts['skipped'] += event.status == Event.Status.SKIPPED
            self.progress(f'{prefix} -> {event.category or "?"}{" high-risk" if event.high_risk else ""}, '
                          f'{result["verified"]} facts verified, {result["rejected"]} rejected'
                          f'{"" if event.status == Event.Status.FACTS else f" SKIPPED: {event.error}"}')
        return counts

    def _event_facts(self, event, articles, classifier, extractor, verifier) -> dict:
        kind = classifier.classify(articles)
        event.category, event.high_risk = ('' if kind.category == 'other' else kind.category), kind.high_risk
        if kind.category == 'other':
            self._set(event, Event.Status.SKIPPED, 'category other: not technology news')
            return {'verified': 0, 'rejected': 0}

        checked = check_facts(extractor, verifier, event.title, articles)
        with transaction.atomic():
            event.facts.all().delete()
            Fact.objects.bulk_create([
                Fact(event=event, article=c.article or articles[0], statement=c.fact.statement, quote=c.fact.quote,
                     kind=c.fact.kind, status=Fact.Status.VERIFIED if c.verified else Fact.Status.REJECTED,
                     problems=c.problems)
                for c in checked if c.article
            ])
        verified = sum(c.verified for c in checked)
        if verified < self.config.min_facts:
            self._set(event, Event.Status.SKIPPED, f'{verified} verified facts, {self.config.min_facts} needed')
        else:
            self._set(event, Event.Status.FACTS)
        return {'verified': verified, 'rejected': len(checked) - verified}

    # --- writer: cards ---------------------------------------------------------------------------------------

    def write(self) -> dict:
        events = Event.objects.filter(status=Event.Status.FACTS).prefetch_related('articles__source')
        events = list(events.filter(pk__in=self.only) if self.only else events)
        self.progress(f'{len(events)} events to write')
        counts = {'events': len(events), 'written': 0, 'failed': 0}
        if not events:
            return counts
        self.manager.use(*self.writer)
        writer = CardWriter(self.client, self.writer_model)
        for number, event in enumerate(events, 1):
            facts = list(event.facts.filter(status=Fact.Status.VERIFIED))
            sources = [f'{a.source.name}: {a.title}' for a in event.articles.all()]
            try:
                card = writer.write([fact.statement for fact in facts], sources, len(event.sources))
            except LLMError as exc:
                self._fail(event, exc)
                counts['failed'] += 1
                self.progress(f'{number:>3}/{len(events)} event {event.pk} FAILED {exc}')
                continue
            with transaction.atomic():
                draft = Draft.objects.create(
                    event=event, headline=card.headline, summary=card.summary, body=card.body,
                    written_body=card.body, status=Draft.Status.UNCHECKED, writer_model=self.writer_model,
                    image_queries=[query.strip() for query in card.image_queries if query.strip()][:MAX_IMAGE_QUERIES],
                )
                draft.facts.set(facts)
                event.title = card.headline[:500]
                self._set(event, Event.Status.DRAFTED)
            counts['written'] += 1
            self.progress(f'{number:>3}/{len(events)} event {event.pk}: {card.headline}')
        return counts

    # --- worker: checks --------------------------------------------------------------------------------------

    def check(self) -> dict:
        drafts = list(Draft.objects.filter(status=Draft.Status.UNCHECKED).select_related('event')
                      .prefetch_related('facts', 'event__articles__source'))
        self.progress(f'{len(drafts)} drafts to check')
        counts = {'drafts': len(drafts), 'ready': 0, 'review': 0, 'failed': 0}
        if not drafts:
            return counts
        self.manager.use(*self.worker)
        checker = CardChecker(self.client, self.worker_model)
        for number, draft in enumerate(drafts, 1):
            facts = [fact.statement for fact in draft.facts.all()]
            try:
                result = checker.check(Card(draft.headline, draft.summary, draft.written_body), facts)
            except LLMError as exc:
                counts['failed'] += 1
                self.progress(f'{number:>3}/{len(drafts)} draft {draft.pk} FAILED {exc} (checked again next run)')
                continue
            reasons = [f'{c.sentence.part}: "{c.sentence.text}" — {"; ".join(c.problems)}'
                       for c in result.failed if c.sentence.part != 'body' or result.status == 'review']
            if result.status == 'review':
                reasons.insert(0, 'card check: headline or summary unsupported, or too much cut')
            sources = draft.event.sources
            if draft.event.high_risk and len(sources) < MIN_INDEPENDENT_SOURCES:
                reasons.append(f'high-risk event with {len(sources)} source: {MIN_INDEPENDENT_SOURCES} needed')
            draft.body = result.card.body
            draft.checks = checks_to_json(result.checks)
            draft.checker_model = self.worker_model
            draft.reasons = reasons
            draft.status = Draft.Status.REVIEW if reasons else Draft.Status.READY
            draft.save(update_fields=['body', 'checks', 'checker_model', 'reasons', 'status'])
            counts['ready' if draft.status == Draft.Status.READY else 'review'] += 1
            cut = len([c for c in result.failed if c.sentence.part == 'body'])
            self.progress(f'{number:>3}/{len(drafts)} draft {draft.pk}: {draft.status.upper()}'
                          f'{f", {cut} sentences cut" if cut else ""} — {draft.headline[:70]}')
        return counts

    # --- no model: photos and the site ----------------------------------------------------------------------

    def images(self) -> dict:
        """A photo for the newest checked draft of each event; drafts already on the site get theirs sent too."""
        newest = Draft.objects.filter(event=OuterRef('event')).order_by('-id').values('id')[:1]
        drafts = (Draft.objects.filter(status__in=[Draft.Status.READY, Draft.Status.REVIEW], image__isnull=True,
                                       image_searched=False, id=Subquery(newest)).select_related('event'))
        drafts = list(drafts.filter(event__in=self.only) if self.only else drafts)
        self.progress(f'{len(drafts)} drafts without a photo')
        counts = {'drafts': len(drafts), 'found': 0, 'none': 0, 'failed': 0}
        api_key = self.pixabay.api_key.get_secret_value()
        if not drafts or not api_key:
            if drafts:
                self.progress('PIXABAY_API_KEY is empty: no photos')
            return counts
        with httpx.Client(timeout=self.pixabay.timeout, follow_redirects=True) as http:
            picker = ImagePicker(Pixabay(http, api_key), settings.MEDIA_ROOT)
            for number, draft in enumerate(drafts, 1):
                prefix = f'{number:>3}/{len(drafts)} draft {draft.pk}'
                try:
                    draft.image = picker.pick(draft)
                except ImageError as exc:
                    counts['failed'] += 1
                    self.progress(f'{prefix} FAILED {exc} (tried again next run)')
                    continue
                draft.image_searched = True
                draft.save(update_fields=['image', 'image_searched'])
                counts['found' if draft.image else 'none'] += 1
                self.progress(f'{prefix}: {draft.image or "none found: the category cover"} — {draft.headline[:60]}')
        return counts

    def publish(self) -> dict:
        return outbox.publish(self.publishing, self.progress)

    # ---------------------------------------------------------------------------------------------------------

    @staticmethod
    def _set(event: Event, status: str, error: str = '') -> None:
        event.status, event.error = status, error
        event.save(update_fields=['status', 'error', 'category', 'high_risk', 'title', 'updated_at'])

    def _fail(self, event: Event, exc: Exception) -> None:
        self._set(event, Event.Status.FAILED, str(exc))
