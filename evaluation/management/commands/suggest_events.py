from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from evaluation.models import EvalEvent
from evaluation.services.suggest import candidate_articles, embed_articles, find_groups
from llm.client import LLMError, LMStudioClient

SUGGESTED_NOTE = 'Auto-suggested group: check the articles, fix the title, add key facts.'


class Command(BaseCommand):
    help = ('Suggest test-set events: groups of articles from different sources that look like one story. '
            'Prints them; --create also saves them as eval events to check in the admin.')

    def add_arguments(self, parser):
        parser.add_argument('--threshold', type=float, default=0.8, help='Cosine similarity, 0..1 (default 0.8)')
        parser.add_argument('--days', type=float, default=3, help='Max time between articles of one event')
        parser.add_argument('--min-sources', type=int, default=2)
        parser.add_argument('--create', action='store_true', help='Save the groups as eval events')

    def handle(self, threshold, days, min_sources, create, **options):
        config = settings.LM_STUDIO
        articles = list(candidate_articles())
        self.stdout.write(f'embedding {len(articles)} articles with {config.embedding_model}...')
        try:
            vectors = embed_articles(articles, lambda texts: LMStudioClient.from_settings(config).embed(
                texts, config.embedding_model))
        except LLMError as exc:
            raise CommandError(str(exc)) from exc

        groups = find_groups(articles, vectors, threshold, timedelta(days=days), min_sources)
        for number, group in enumerate(groups, 1):
            category = Counter(a.source.category for a in group.articles).most_common(1)[0][0]
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n#{number} [{category}] {len(group.articles)} articles, {len(group.sources)} sources, '
                f'min similarity {group.similarity:.2f}'
            ))
            for article in group.articles:
                date = f'{article.published_at:%m-%d %H:%M}' if article.published_at else '?'
                self.stdout.write(f'  {article.pk:>5} {date} {article.source.slug:<20} {article.title[:90]}')
            if create:
                event = EvalEvent.objects.create(title=group.articles[0].title[:300], category=category,
                                                 notes=SUGGESTED_NOTE)
                event.articles.set(group.articles)

        summary = f'\n{len(groups)} groups'
        if create:
            summary += ' saved as eval events (notes: "Auto-suggested group")'
        self.stdout.write(self.style.SUCCESS(summary))
