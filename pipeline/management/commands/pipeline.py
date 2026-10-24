from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from llm.client import LLMError, LMStudioClient
from pipeline.models_manager import LmsModelManager, ModelLoadError, NoModelManager
from pipeline.runner import STAGES, Pipeline


class Command(BaseCommand):
    help = ('New articles -> events -> facts -> card -> card check -> draft (admin: Pipeline -> Drafts). '
            'Collect first (collect). Stops halfway safely: the next run continues.')

    def add_arguments(self, parser):
        parser.add_argument('--stage', action='append', dest='stages', choices=STAGES,
                            help='Only these stages (repeatable). Default: all, in order')
        parser.add_argument('--max-events', type=int, help='Events taken to facts in this run (PIPELINE_MAX_EVENTS)')
        parser.add_argument('--no-manage-models', action='store_true',
                            help='Do not load/unload models with lms: LM Studio loads them on demand')

    def handle(self, stages, max_events, no_manage_models, **options):
        config = settings.PIPELINE
        if max_events:
            config = config.model_copy(update={'max_events': max_events})
        client = LMStudioClient.from_settings(settings.LM_STUDIO)
        try:
            client.list_models()
        except LLMError as exc:
            raise CommandError(str(exc)) from exc

        manage = config.manage_models and not no_manage_models
        manager = LmsModelManager(config.idle_ttl, progress=self._note) if manage else NoModelManager()
        pipeline = Pipeline(client, settings.LM_STUDIO, config, manager, progress=self.stdout.write)
        try:
            run = pipeline.run(stages or STAGES)
        except ModelLoadError as exc:
            raise CommandError(str(exc)) from exc
        for stage, stats in run.stats.items():
            shown = {key: value for key, value in stats.items() if key != 'rejected'}
            self.stdout.write(self.style.MIGRATE_HEADING(stage) + f' {shown}')
        self.stdout.write(self.style.SUCCESS(f'run {run.pk} done: drafts in /admin/pipeline/draft/'))

    def _note(self, line: str) -> None:
        self.stdout.write(self.style.WARNING(f'[lms] {line}'))
