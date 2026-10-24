from django.core.management.base import BaseCommand, CommandError

from evaluation.models import EvalRun
from evaluation.runner import rescore_run
from evaluation.tasks import TASKS


class Command(BaseCommand):
    help = ('Score stored answers of finished runs again with the current checks (no model calls). '
            'For fixes in scoring code; a prompt change needs a new eval_run.')

    def add_arguments(self, parser):
        parser.add_argument('runs', nargs='*', type=int, metavar='RUN_ID', help='Default: every finished run')
        parser.add_argument('--task', choices=list(TASKS), help='Only runs of this task')

    def handle(self, runs, task, **options):
        queryset = EvalRun.objects.exclude(finished_at=None).order_by('id')
        if runs:
            queryset = queryset.filter(pk__in=runs)
        if task:
            queryset = queryset.filter(task=task)
        if not queryset.exists():
            raise CommandError('no finished runs match')

        for run in queryset:
            before = dict(run.metrics)
            rescore_run(run, TASKS[run.task](client=None, model=run.model_id))
            changed = {key: (before.get(key), value) for key, value in run.metrics.items()
                       if key not in ('high_risk', 'rescored_at') and before.get(key) != value}
            self.stdout.write(self.style.MIGRATE_HEADING(f'run {run.pk} {run.task} / {run.model_id}'))
            for key, (old, new) in changed.items():
                self.stdout.write(f'  {key}: {old} -> {new}')
            if not changed:
                self.stdout.write('  (no changes)')
