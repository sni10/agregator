import httpx
from django.conf import settings
from django.core.management.base import BaseCommand

from sources.models import Source
from sources.services.collector import RssCollector, TrafilaturaExtractor, collect_source


class Command(BaseCommand):
    help = 'Poll enabled sources and store new articles (with full text unless --no-full-text). Safe to rerun.'

    def add_arguments(self, parser):
        parser.add_argument('--source', action='append', dest='slugs', metavar='SLUG', help='Only these sources')
        parser.add_argument('--no-full-text', action='store_true', help='Store feed entries only, skip page download')

    def handle(self, slugs, no_full_text, **options):
        config = settings.COLLECTOR
        sources = Source.objects.filter(enabled=True).exclude(license_policy=Source.License.DENIED)
        if slugs:
            sources = sources.filter(slug__in=slugs)

        headers = {'User-Agent': config.user_agent}
        with httpx.Client(headers=headers, timeout=config.timeout, follow_redirects=True) as http:
            collector = RssCollector(http, max_entries=config.max_entries)
            extractor = TrafilaturaExtractor(http) if config.full_text and not no_full_text else None
            total_new = failed = 0
            for source in sources:
                stats = collect_source(source, collector, extractor)
                total_new += stats.new
                if stats.errors:
                    failed += 1
                    self.stdout.write(self.style.ERROR(f'{source.slug}: {stats.errors[0]}'))
                else:
                    self.stdout.write(
                        f'{source.slug}: {stats.new} new, {stats.known} known, {stats.duplicates} duplicates, '
                        f'{stats.extract_failed} without full text, {stats.recovered} recovered on retry'
                    )
        style = self.style.WARNING if failed else self.style.SUCCESS
        self.stdout.write(style(f'done: {total_new} new articles, {failed} sources failed'))
