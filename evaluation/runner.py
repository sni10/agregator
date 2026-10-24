from collections.abc import Callable
from itertools import islice

from django.utils import timezone

from llm.client import Completion, LLMError, Usage

from .models import EvalResult, EvalRun
from .tasks.base import Case, Task


def run_task(task: Task, limit: int | None = None, progress: Callable[[str], None] = print) -> EvalRun:
    """Run every case of the task, store each answer, then the run's metrics. A failed case does not stop the run."""
    run = EvalRun.objects.create(
        task=task.name, model_id=task.model, params={'reasoning_effort': task.reasoning_effort, 'limit': limit},
    )
    results = []
    for number, case in enumerate(islice(task.cases(), limit), 1):
        result = EvalResult(run=run, event=case.event, article=case.article)
        output = {'expected': case.expected, **case.data}
        try:
            completion = task.ask(case)
        except LLMError as exc:
            result.error = str(exc)
            progress(f'{number:>4} {case.key}: ERROR {exc}')
        else:
            score = task.score(case, completion)
            answer = completion.value.model_dump() if hasattr(completion.value, 'model_dump') else completion.value
            output |= {'answer': answer, **score.details}
            usage = completion.usage
            result.raw = completion.raw
            result.correct = score.correct
            result.prompt_tokens = usage.prompt_tokens
            result.completion_tokens = usage.completion_tokens
            result.reasoning_tokens = usage.reasoning_tokens
            result.seconds = usage.seconds
            mark = {True: 'ok', False: 'WRONG', None: '?'}[score.correct]
            progress(f'{number:>4} {case.key}: {mark} ({usage.seconds:.1f}s)')
        result.output = output
        result.save()
        results.append(result)

    run.metrics = run_metrics(task, results)
    run.finished_at = timezone.now()
    run.save(update_fields=['metrics', 'finished_at'])
    return run


def rescore_run(run: EvalRun, task: Task) -> EvalRun:
    """Score the stored answers again with the current scoring code; the model is not called.
    For fixes in the checks (verification/), not in the prompts: a prompt change needs a new run."""
    results = list(run.results.select_related('event', 'article'))
    for result in results:
        if result.error or not result.output or 'answer' not in result.output:
            continue
        # output = expected + case data + answer + score details: the data part is what score() may read
        data = {k: v for k, v in result.output.items() if k not in ('expected', 'answer')}
        case = Case(key='', expected=result.output['expected'], event=result.event, article=result.article,
                    data=data)
        usage = Usage(result.prompt_tokens, result.completion_tokens, result.reasoning_tokens, result.seconds)
        completion = Completion(task.schema.model_validate(result.output['answer']), result.raw, run.model_id, usage)
        score = task.score(case, completion)
        result.correct = score.correct
        result.output = result.output | score.details
        result.save(update_fields=['correct', 'output'])
    run.metrics = run_metrics(task, results) | {'rescored_at': timezone.now().isoformat(timespec='seconds')}
    run.save(update_fields=['metrics'])
    return run


def run_metrics(task: Task, results: list[EvalResult]) -> dict:
    metrics = common_metrics(results) | task.summarize(results)
    # The same metrics on high-risk events alone (hacks, lawsuits, regulation, prices): a model that invents
    # numbers there more often needs a stricter gate or a stronger model for them
    if risky := [r for r in results if r.event and r.event.high_risk]:
        metrics['high_risk'] = common_metrics(risky) | task.summarize(risky)
    return metrics


def common_metrics(results: list[EvalResult]) -> dict:
    answered = [r for r in results if not r.error]
    scored = [r for r in answered if r.correct is not None]
    seconds = sum(r.seconds for r in answered)
    completion_tokens = sum(r.completion_tokens for r in answered)
    return {
        'cases': len(results),
        'errors': len(results) - len(answered),
        'accuracy': round(sum(r.correct for r in scored) / len(scored), 3) if scored else None,
        'avg_seconds': round(seconds / len(answered), 2) if answered else None,
        'tokens_per_second': round(completion_tokens / seconds, 1) if seconds else None,
        'reasoning_tokens': sum(r.reasoning_tokens for r in answered),
    }
