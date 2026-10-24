from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from pydantic import BaseModel

from llm.client import LLMError, LMStudioClient


class Ping(BaseModel):
    category: str
    confidence: float


class Command(BaseCommand):
    help = 'Check LM Studio: list models, send one JSON request to the worker model, print speed.'

    def add_arguments(self, parser):
        parser.add_argument('--model', help='Model ID to test (default: LM_STUDIO_WORKER_MODEL)')

    def handle(self, model, **options):
        config = settings.LM_STUDIO
        client = LMStudioClient.from_settings(config)
        model = model or config.worker_model
        try:
            models = client.list_models()
            self.stdout.write(f'{config.base_url}: {len(models)} models')
            for model_id in models:
                self.stdout.write(f'  {"*" if model_id == model else " "} {model_id}')
            if model not in models:
                raise CommandError(f'{model} is not in LM Studio')

            answer = client.complete_json(
                model=model,
                system='Classify the headline into one of: it, ai, crypto. Answer with JSON only.',
                user='Headline: "Ethereum developers schedule the next network upgrade"',
                schema=Ping,
                max_tokens=256,
            )
        except LLMError as exc:
            raise CommandError(str(exc)) from exc
        usage = answer.usage
        self.stdout.write(self.style.SUCCESS(
            f'{model}: {answer.value.model_dump()} in {usage.seconds:.1f}s, {usage.completion_tokens} tokens '
            f'({usage.reasoning_tokens} thinking), {answer.tokens_per_second:.1f} tok/s, attempts {answer.attempts}'
        ))
