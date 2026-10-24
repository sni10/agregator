from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from evaluation.runner import run_task
from evaluation.tasks import TASKS
from llm.client import LLMError, LMStudioClient


class Command(BaseCommand):
    help = 'Stage 0: run a task over the labeled test set with one model; results go to the admin (Eval runs).'

    def add_arguments(self, parser):
        parser.add_argument('--task', required=True, choices=[*TASKS, 'all'])
        parser.add_argument('--model', help='Model ID (default: LM_STUDIO_WORKER_MODEL, or WRITER for the write task)')
        parser.add_argument('--reasoning-effort', choices=['none', 'low', 'medium', 'high'],
                            help='Default: LM_STUDIO_REASONING_EFFORT')
        parser.add_argument('--limit', type=int, help='Only the first N cases (a quick look before a full run)')

    def handle(self, task, model, reasoning_effort, limit, **options):
        config = settings.LM_STUDIO
        client = LMStudioClient.from_settings(config)
        try:
            available = client.list_models()
        except LLMError as exc:
            raise CommandError(str(exc)) from exc

        for name in TASKS if task == 'all' else [task]:
            task_cls = TASKS[name]
            model_id = model or (config.writer_model if task_cls.role == 'writer' else '') or config.worker_model
            if model_id not in available:
                raise CommandError(f'{model_id} is not in LM Studio. Available: {", ".join(available)}')

            self.stdout.write(self.style.MIGRATE_HEADING(f'{name} / {model_id}'))
            run = run_task(task_cls(client, model_id, reasoning_effort), limit, progress=self.stdout.write)
            if not run.metrics['cases']:
                self.stdout.write(self.style.WARNING('no cases: label events (and their facts) in the admin first'))
                continue
            for key, value in run.metrics.items():
                self.stdout.write(f'  {key}: {value}')
            self.stdout.write(self.style.SUCCESS(f'run {run.pk} saved: /admin/evaluation/evalrun/{run.pk}/change/'))
