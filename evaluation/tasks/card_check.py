import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from llm.client import Completion
from verification.card_check import Answer as SentenceAnswer
from verification.card_check import Card, CardChecker, checks_from_json, checks_to_json, judge, split_sentences

from ..models import EvalResult, EvalRun
from .base import Case, Score, Task

DATA = Path(__file__).resolve().parents[1] / 'data' / 'card_traps.json'


class StoredCheck(BaseModel):
    part: Literal['headline', 'summary', 'body']
    text: str
    problems: list[str]
    answer: SentenceAnswer | None


class Answer(BaseModel):
    checks: list[StoredCheck]


class CardCheckTask(Task):
    """The card gate (verification/card_check.py) on the cards of the latest write run: each card as written,
    and again with a hand-written false sentence put into its body (evaluation/data/card_traps.json): a trap
    (a claim the facts do not make) and a join (two true facts linked by a relation no fact states).
    Expected to fail: the false sentence and the known bad sentences of real cards; every other sentence should pass."""

    name = 'card_check'
    schema = Answer

    def cases(self):
        run = EvalRun.objects.filter(task='write', finished_at__isnull=False).order_by('-id').first()
        if run is None:
            return
        data = json.loads(DATA.read_text(encoding='utf-8'))
        results = EvalResult.objects.filter(run=run, error='').select_related('event').order_by('event_id')
        for result in results:
            card, facts, title = result.output['answer'], result.output['expected'], result.event.title
            known_bad = data['known_bad'].get(title, [])
            base = {'facts': facts, 'write_run': run.pk}
            yield Case(key=f'event {result.event_id} card', expected=known_bad, event=result.event,
                       data={**base, 'kind': 'card', 'card': card})
            for kind in ('trap', 'join'):
                if sentence := data.get(f'{kind}s', {}).get(title):
                    yield Case(key=f'event {result.event_id} {kind}', expected=[*known_bad, sentence],
                               event=result.event, data={**base, 'kind': kind, 'card': with_sentence(card, sentence)})

    def ask(self, case):
        checker = CardChecker(self.client, self.model, self.reasoning_effort)
        result = checker.check(as_card(case.data['card']), case.data['facts'])
        answer = Answer(checks=checks_to_json(result.checks))
        return Completion(answer, answer.model_dump_json(), self.model, result.usage)

    def score(self, case, completion):
        card = as_card(case.data['card'])
        checks = checks_from_json([check.model_dump() for check in completion.value.checks], case.data['facts'])
        bad = [check for check in checks if any(text in check.sentence.text for text in case.expected)]
        good = [check for check in checks if check not in bad]
        missed = [check.sentence.text for check in bad if check.ok]
        false_alarms = [{'part': c.sentence.part, 'text': c.sentence.text, 'problems': c.problems}
                        for c in good if not c.ok]
        return Score(not missed and not false_alarms, {
            'status': judge(card, checks).status,
            'sentences': len(checks),
            'bad': len(bad),
            'good': len(good),
            'missed': missed,
            'false_alarms': false_alarms,
        })

    def summarize(self, results):
        answered = [r for r in results if not r.error]
        bad = sum(r.output['bad'] for r in answered)
        good = sum(r.output['good'] for r in answered)
        missed = sum(len(r.output['missed']) for r in answered)
        false_alarms = sum(len(r.output['false_alarms']) for r in answered)
        return {
            # a false sentence that passes gets published: this must be near zero
            'missed_rate': round(missed / bad, 3) if bad else None,
            # a true sentence cut or a card sent to review for nothing: costs text and attention, not truth
            'false_alarm_rate': round(false_alarms / good, 3) if good else None,
            # false sentences that passed, by kind
            'missed': {kind: sum(len(r.output['missed']) for r in answered if r.output['kind'] == kind)
                       for kind in ('card', 'trap', 'join')},
            # real cards should mostly pass; a card with a trap should be trimmed (trap cut) or go to review
            'card_statuses': statuses(answered, 'card'),
            'trap_statuses': statuses(answered, 'trap'),
            'join_statuses': statuses(answered, 'join'),
        }


def statuses(results, kind: str) -> dict:
    return dict(Counter(r.output['status'] for r in results if r.output['kind'] == kind))


def as_card(card: dict) -> Card:
    return Card(card['headline'], card['summary'], card['body'])


def with_sentence(card: dict, sentence: str) -> dict:
    """The card with the sentence put after the first sentence of the body."""
    sentences = split_sentences(card['body'])
    return {**card, 'body': ' '.join([*sentences[:1], sentence, *sentences[1:]])}
