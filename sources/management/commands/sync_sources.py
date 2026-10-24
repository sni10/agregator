import json
from pathlib import Path

from django.core.management.base import BaseCommand

from sources.models import Source

DEFAULT_FILE = Path(__file__).resolve().parents[2] / 'data' / 'sources.json'


class Command(BaseCommand):
    help = 'Create or update sources from a JSON list (default: sources/data/sources.json). Matched by slug.'

    def add_arguments(self, parser):
        parser.add_argument('file', nargs='?', type=Path, default=DEFAULT_FILE)

    def handle(self, file, **options):
        created = updated = 0
        for item in json.loads(file.read_text(encoding='utf-8')):
            # enabled and license_policy are edited in the admin: the file never overwrites them
            _, is_new = Source.objects.update_or_create(slug=item.pop('slug'), defaults=item)
            created += is_new
            updated += not is_new
        self.stdout.write(self.style.SUCCESS(f'sources: {created} created, {updated} updated'))
