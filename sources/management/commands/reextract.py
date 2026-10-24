from itertools import groupby

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand

from sources.models import Article
from sources.services.collector import TrafilaturaExtractor, clean_articles, extract_text


class Command(BaseCommand):
    help = ('Download the pages of stored articles again and clean the texts (boilerplate, teasers). '
            'For articles collected before a collector fix. Sources with full_text off are skipped.')

    def add_arguments(self, parser):
        parser.add_argument('--source', action='append', dest='slugs', metavar='SLUG', help='Only these sources')
        parser.add_argument('--status', action='append', dest='statuses', choices=Article.Status.values,
                            help='Only articles with this status (default: all but duplicates)')

    def handle(self, slugs, statuses, **options):
        config = settings.COLLECTOR
        articles = Article.objects.filter(source__full_text=True).select_related('source').order_by('source', 'id')
        if slugs:
            articles = articles.filter(source__slug__in=slugs)
        articles = articles.filter(status__in=statuses) if statuses else articles.exclude(
            status=Article.Status.DUPLICATE)

        headers = {'User-Agent': config.user_agent}
        with httpx.Client(headers=headers, timeout=config.timeout, follow_redirects=True) as http:
            extractor = TrafilaturaExtractor(http)
            for source, group in groupby(articles, key=lambda article: article.source):
                group = list(group)
                for article in group:
                    if text := extract_text(extractor, article.canonical_url, frozenset(source.boilerplate)):
                        article.text, article.status = text, Article.Status.EXTRACTED
                    elif not article.text:
                        article.status = Article.Status.EXTRACT_FAILED
                        article.save(update_fields=['status'])
                    # a failed download keeps the old text: it is cleaned below like the rest
                clean_articles(source, group)
                failed = sum(not article.text for article in group)
                self.stdout.write(f'{source.slug}: {len(group)} articles, {failed} without full text, '
                                  f'{len(source.boilerplate)} boilerplate paragraphs known')
