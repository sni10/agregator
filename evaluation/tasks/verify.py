import random

from pipeline.steps.facts import TEXT_BUDGET
from pipeline.steps.facts import VERIFY_SYSTEM as SYSTEM
from pipeline.steps.facts import VerifyAnswer as Answer
from verification.text_match import NUMBER, claim_problems

from .base import Case, Score, Task, article_block, events_with_articles


def change_number(statement: str) -> str | None:
    """Same statement with one number altered: a trap the checker must not accept."""
    match = NUMBER.search(statement)
    if not match:
        return None
    digits = match.group().replace(',', '')
    value = float(digits)
    changed = value + 1 if value.is_integer() and 1900 < value < 2100 else value * 2 + 1
    text = f'{changed:g}' if '.' in digits else str(int(changed))
    return statement[:match.start()] + text + statement[match.end():]


class VerifyTask(Task):
    """Each hand-written fact against its own event's articles (expected supports), the same fact with a changed
    number (expected not supports) and, for facts without numbers, against another event's articles (unclear)."""

    name = 'verify'
    schema = Answer
    max_tokens = 400

    def cases(self):
        rng = random.Random(0)
        events = [event for event in events_with_articles() if event.articles.all()]
        for event in events:
            others = [other for other in events if other.pk != event.pk]
            for fact in event.facts.all():
                yield Case(key=f'fact {fact.pk} true', expected='supports', event=event,
                           data={'statement': fact.statement, 'articles_of': event.pk})
                if changed := change_number(fact.statement):
                    yield Case(key=f'fact {fact.pk} changed', expected='not supports', event=event,
                               data={'statement': changed, 'articles_of': event.pk})
                elif others:
                    other = rng.choice(others)
                    yield Case(key=f'fact {fact.pk} other', expected='not supports', event=event,
                               data={'statement': fact.statement, 'articles_of': other.pk})

    def ask(self, case):
        blocks = '\n\n'.join(article_block(article, chars) for article, chars in self._articles(case))
        return self.complete_json(SYSTEM, f'Statement: {case.data["statement"]}\n\n{blocks}', Answer)

    def score(self, case, completion):
        """`correct` is the pipeline's verdict: the model says supports and the code finds nothing wrong with it."""
        answer = completion.value
        expected = case.expected == 'supports'
        model_supports = answer.verdict == 'supports'
        problems = []
        if model_supports:
            # the text the model saw, not the whole articles: a quote from beyond the budget is not checkable
            source = '\n\n'.join(f'{article.title}\n{article.body[:chars]}' for article, chars in self._articles(case))
            problems = claim_problems(case.data['statement'], answer.quote, source)
        return Score((model_supports and not problems) == expected, {
            'model_correct': model_supports == expected,
            'problems': problems,
        })

    @staticmethod
    def _articles(case):
        articles = list(events_with_articles().get(pk=case.data['articles_of']).articles.all())
        return [(article, TEXT_BUDGET // len(articles)) for article in articles]

    def summarize(self, results):
        answered = [r for r in results if not r.error]
        traps = [r for r in answered if r.output['expected'] != 'supports']
        true = [r for r in answered if r.output['expected'] == 'supports']
        return {
            # a trap accepted = a wrong fact would get published: this rate must be near zero
            'false_support_rate': rate(traps, lambda r: not r.correct),
            'true_support_rate': rate(true, lambda r: r.correct),
            # the model alone, without the code checks: shows what the checks add
            'model_false_support_rate': rate(traps, lambda r: not r.output.get('model_correct', r.correct)),
            'model_true_support_rate': rate(true, lambda r: r.output.get('model_correct', r.correct)),
        }


def rate(results, condition) -> float | None:
    return round(sum(bool(condition(r)) for r in results) / len(results), 3) if results else None

