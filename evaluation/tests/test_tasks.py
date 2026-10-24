import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase

from evaluation.models import EvalEvent, EvalResult
from evaluation.runner import rescore_run, run_task
from evaluation.tasks import CardCheckTask, ClassifyTask, ExtractTask, SameEventTask, VerifyTask, WriteTask
from evaluation.tasks.verify import change_number
from llm.client import Completion, LLMError, Usage
from sources.models import Article, Category, Source


class FakeClient:
    """Answers with `answer(user_prompt)`; raises LLMError when it returns an exception."""

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def complete_json(self, *, model, system, user, schema, max_tokens, reasoning_effort):
        self.prompts.append(user)
        value = self.answer(user)
        if isinstance(value, Exception):
            raise value
        return Completion(schema.model_validate(value), json.dumps(value), model, Usage(100, 20, 0, 2.0))


def article(source, slug, title, text):
    return Article.objects.create(source=source, canonical_url=f'https://example.com/{slug}', title=title,
                                  text=text, content_hash=slug)


def run(task_cls, answer):
    client = FakeClient(answer)
    return run_task(task_cls(client, 'fake-model'), progress=lambda line: None), client


class TaskTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        ai = Source.objects.create(name='AI news', slug='ai', feed_url='https://example.com/ai', category=Category.AI)
        crypto = Source.objects.create(name='Coins', slug='coins', feed_url='https://example.com/c',
                                       category=Category.CRYPTO)
        cls.release = EvalEvent.objects.create(title='Model X release', category=Category.AI)
        cls.release.articles.set([
            article(ai, 'x1', 'Example Corp ships Model X', 'Example Corp released Model X on Monday. '
                                                            'It is 40% cheaper than Model W.'),
            article(ai, 'x2', 'Model X is out', 'Model X from Example Corp costs 40% less to run.'),
        ])
        cls.release.facts.create(statement='Model X is 40% cheaper than Model W', kind='number')
        cls.hack = EvalEvent.objects.create(title='Bridge hack', category=Category.CRYPTO, high_risk=True)
        cls.hack.articles.set([article(crypto, 'h1', 'Bridge drained', 'Attackers drained $12 million from a bridge.')])
        cls.hack.facts.create(statement='Attackers drained $12 million from the bridge', kind='number')


class ClassifyTaskTests(TaskTestCase):
    def test_scores_against_event_category_and_lists_mistakes(self):
        result, _ = run(ClassifyTask, lambda user: {'category': 'ai'})

        self.assertEqual(result.metrics['cases'], 3)
        self.assertEqual(result.metrics['accuracy'], round(2 / 3, 3))
        self.assertEqual(result.metrics['mistakes'], {'crypto -> ai': 1})
        # the hack is the only high-risk event: its one article was misclassified
        self.assertEqual((result.metrics['high_risk']['cases'], result.metrics['high_risk']['accuracy']), (1, 0.0))

    def test_no_high_risk_section_without_high_risk_events(self):
        EvalEvent.objects.update(high_risk=False)

        result, _ = run(ClassifyTask, lambda user: {'category': 'ai'})

        self.assertNotIn('high_risk', result.metrics)

    def test_model_error_is_stored_and_run_continues(self):
        result, _ = run(ClassifyTask, lambda user: LLMError('timeout'))

        self.assertEqual((result.metrics['cases'], result.metrics['errors']), (3, 3))
        self.assertEqual(EvalResult.objects.filter(run=result).exclude(error='').count(), 3)


class SameEventTaskTests(TaskTestCase):
    def test_counts_false_merges(self):
        result, _ = run(SameEventTask, lambda user: {'same_event': True, 'reason': 'both about it'})

        # one positive pair (x1+x2) and one negative pair (release vs hack)
        self.assertEqual(result.metrics['cases'], 2)
        self.assertEqual((result.metrics['precision'], result.metrics['recall']), (0.5, 1.0))
        self.assertEqual(result.metrics['false_merges'], 1)


class ExtractTaskTests(TaskTestCase):
    def test_flags_invented_quote_and_number(self):
        x1 = self.release.articles.get(content_hash='x1')

        def answer(user):
            if 'Model X' not in user:
                return {'facts': []}
            return {'facts': [
                {'statement': 'Model X is 40% cheaper than Model W', 'article_id': x1.pk,
                 'quote': 'It is 40% cheaper than Model W.', 'kind': 'number'},
                {'statement': 'Model X has 70 billion parameters', 'article_id': x1.pk,
                 'quote': 'Model X has 70 billion parameters', 'kind': 'number'},
            ]}

        result, _ = run(ExtractTask, answer)

        release = EvalResult.objects.get(run=result, event=self.release)
        self.assertFalse(release.correct)
        self.assertEqual(release.output['recall'], 1.0)
        self.assertEqual(release.output['invented_facts'][0]['problems'],
                         ['quote not in article', "numbers not in article: ['70000000000']"])
        self.assertEqual(result.metrics['invented_rate'], 0.5)


class RescoreTests(TaskTestCase):
    def test_rescoring_restores_scores_and_metrics_without_calling_the_model(self):
        result, client = run(ClassifyTask, lambda user: {'category': 'ai'})
        expected_metrics = dict(result.metrics)
        EvalResult.objects.filter(run=result).update(correct=True)
        result.metrics = {}
        result.save()

        rescore_run(result, ClassifyTask(client=None, model='fake-model'))

        self.assertEqual(EvalResult.objects.filter(run=result, correct=False).count(), 1)
        self.assertEqual({k: v for k, v in result.metrics.items() if k != 'rescored_at'}, expected_metrics)
        self.assertIn('rescored_at', result.metrics)
        self.assertEqual(len(client.prompts), 3)  # only the original run asked the model


class VerifyTaskTests(TaskTestCase):
    def test_change_number(self):
        self.assertEqual(change_number('Drained $12 million'), 'Drained $25 million')
        self.assertEqual(change_number('Ships in 2026'), 'Ships in 2027')
        self.assertIsNone(change_number('No numbers here'))

    def test_code_checks_reject_traps_a_model_that_always_agrees_accepts(self):
        def answer(user):
            quote = 'It is 40% cheaper than Model W.' if 'Model X' in user else 'Attackers drained $12 million'
            return {'verdict': 'supports', 'quote': quote}

        result, _ = run(VerifyTask, answer)

        metrics = result.metrics
        self.assertEqual(metrics['cases'], 4)  # each of 2 facts: true + changed number
        self.assertEqual((metrics['model_true_support_rate'], metrics['model_false_support_rate']), (1.0, 1.0))
        self.assertEqual((metrics['true_support_rate'], metrics['false_support_rate']), (1.0, 0.0))

    def test_support_with_an_invented_quote_is_rejected(self):
        result, _ = run(VerifyTask, lambda user: {'verdict': 'supports', 'quote': 'It is free forever.'})

        self.assertEqual(result.metrics['true_support_rate'], 0.0)
        self.assertEqual(EvalResult.objects.filter(run=result).first().output['problems'], ['quote not in articles'])


class CardCheckTaskTests(TaskTestCase):
    def test_traps_are_caught_and_true_sentences_pass(self):
        def card(user):
            if 'Model X' in user:
                return {'headline': 'Model X is 40% cheaper', 'summary': 'Model X costs 40% less.',
                        'body': 'Model X is 40% cheaper than Model W, its maker says. Model X is 40% cheaper to run.'}
            return {'headline': 'Bridge loses $12 million', 'summary': 'Attackers drained $12 million.',
                    'body': 'Attackers drained $12 million from the bridge. The bridge lost $12 million.'}

        run(WriteTask, card)
        data = {'traps': {'Model X release': 'Example Corp confirmed the price cut.'},
                'joins': {'Model X release': 'Model X got cheaper after the launch of Model W.'}, 'known_bad': {}}

        def verdict(user):  # a checker that sees through the trap but not the join
            return {'verdict': 'unclear' if 'confirmed' in user.rsplit('\n', 1)[1] else 'supports', 'fact_ids': [1]}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'traps.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            with mock.patch('evaluation.tasks.card_check.DATA', path):
                result, _ = run(CardCheckTask, verdict)

        metrics = result.metrics
        self.assertEqual(metrics['cases'], 4)  # 2 cards + 1 trap card + 1 join card
        self.assertEqual((metrics['missed_rate'], metrics['false_alarm_rate']), (0.5, 0.0))
        self.assertEqual(metrics['missed'], {'card': 0, 'trap': 0, 'join': 1})
        self.assertEqual(metrics['card_statuses'], {'passed': 2})
        self.assertEqual((metrics['trap_statuses'], metrics['join_statuses']), ({'trimmed': 1}, {'passed': 1}))


class WriteTaskTests(TaskTestCase):
    def test_flags_numbers_missing_from_facts_and_leaves_quality_to_a_human(self):
        def answer(user):
            if 'Model X' in user:
                return {'headline': 'Model X is 40% cheaper', 'summary': 'Out now.', 'body': 'It costs 40% less.'}
            return {'headline': 'Bridge loses $15 million', 'summary': 'Hack.', 'body': 'Attackers drained it.'}

        result, client = run(WriteTask, answer)

        by_event = {r.event_id: r for r in EvalResult.objects.filter(run=result)}
        self.assertIsNone(by_event[self.release.pk].correct)
        self.assertEqual(by_event[self.hack.pk].output['invented_numbers'], ['15000000'])
        self.assertEqual(result.metrics['cards_with_invented_numbers'], 1)
        self.assertIn('AI news: Example Corp ships Model X', client.prompts[0])
