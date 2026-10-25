import json
from datetime import timedelta

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from pydantic import SecretStr

from llm.client import Completion, Usage
from pipeline.models import Draft, Event, Fact
from pipeline.models_manager import LmsModelManager, LoadSpec
from pipeline.runner import Pipeline
from pipeline.steps.facts import quote_context
from sources.models import Article, Category, Source

RELEASE = ('Example Corp released Model X on Monday.\nModel X is 40% cheaper than Model W.\n'
           'It supports 12 languages.\nThe company did not give a release date for Europe.')
HACK = 'Attackers drained $12 million from the Bridgy bridge on Friday.\nBridgy paused deposits.\nNo arrests were made.'


class FakeLM:
    """Embeddings by topic; JSON answers chosen by which step's system prompt is asking."""

    def __init__(self, card_verdict='supports'):
        self.card_verdict = card_verdict
        self.calls = []

    def embed(self, texts, model):
        return [[1.0, 0.0] if 'Model X' in text else [0.0, 1.0] for text in texts]

    def complete_json(self, *, model, system, user, schema, max_tokens, reasoning_effort):
        step = system.split('\n', 1)[0]
        self.calls.append((model, step))
        hack = 'Bridgy' in user
        if step.startswith('You decide whether two'):
            value = {'same_event': True, 'reason': 'same release'}
        elif step.startswith('You classify one news event'):
            value = {'category': 'crypto', 'high_risk': True} if hack else {'category': 'ai', 'high_risk': False}
        elif step.startswith('You extract'):
            article_id = int(user.split('<article id="', 1)[1].split('"', 1)[0])
            lines = (HACK if hack else RELEASE).split('\n')
            value = {'facts': [{'statement': line.rstrip('.'), 'article_id': article_id, 'quote': line,
                                'kind': 'general'} for line in lines]
                     + [{'statement': 'Model X has 70 billion parameters', 'article_id': article_id,
                         'quote': 'Model X has 70 billion parameters', 'kind': 'number'}]}
        elif step.startswith('You check one statement'):
            statement = user.split('Statement: ', 1)[1].split('\n', 1)[0]
            value = {'verdict': 'supports', 'quote': statement}
        elif step.startswith('You write'):
            value = ({'headline': 'Bridgy bridge loses $12 million', 'summary': 'Attackers drained $12 million.',
                      'body': 'Attackers drained $12 million from the Bridgy bridge on Friday. Bridgy paused '
                              'deposits. No arrests were made.', 'image_queries': ['bridge night']} if hack else
                     {'headline': 'Model X is 40% cheaper', 'summary': 'Example Corp released Model X on Monday.',
                      'body': 'Model X is 40% cheaper than Model W. It supports 12 languages. '
                              'The company did not give a release date for Europe. It is the best model ever.',
                      'image_queries': [' circuit board ', '', 'server room', 'data center', 'chip']})
        elif step.startswith('You check one sentence'):
            sentence = user.rsplit(': ', 1)[1]
            verdict = 'unclear' if 'best model ever' in sentence else self.card_verdict
            value = {'verdict': verdict, 'fact_ids': [1]}
        else:
            raise AssertionError(f'unexpected prompt: {step}')
        return Completion(schema.model_validate(value), json.dumps(value), model, Usage(10, 5, 0, 0.1))


class FakeManager:
    def __init__(self):
        self.used = []

    def use(self, *specs):
        self.used.append(tuple(spec.model for spec in specs))


def make_article(source, slug, title, text, hours_ago):
    return Article.objects.create(source=source, canonical_url=f'https://example.com/{slug}', title=title, text=text,
                                  content_hash=slug, status=Article.Status.EXTRACTED,
                                  published_at=timezone.now() - timedelta(hours=hours_ago))


class PipelineTests(TestCase):
    def setUp(self):
        ai = Source.objects.create(name='AI news', slug='ai', feed_url='https://example.com/ai', category=Category.AI)
        tech = Source.objects.create(name='Tech', slug='tech', feed_url='https://example.com/t', category=Category.IT)
        coins = Source.objects.create(name='Coins', slug='coins', feed_url='https://example.com/c',
                                      category=Category.CRYPTO)
        make_article(ai, 'x1', 'Example Corp ships Model X', RELEASE, hours_ago=2)
        make_article(tech, 'x2', 'Model X is out, and cheaper', RELEASE, hours_ago=1)
        make_article(coins, 'h1', 'Bridgy bridge drained', HACK, hours_ago=10)
        self.lm, self.manager = FakeLM(), FakeManager()

    def run_pipeline(self, events=None, **config):
        lm_studio = settings.LM_STUDIO.model_copy(update={'worker_model': 'worker', 'writer_model': 'writer',
                                                          'embedding_model': 'embed'})
        config = settings.PIPELINE.model_copy(update=config)
        publishing = settings.PUBLISHING.model_copy(update={'api_key': SecretStr('')})  # never the real site
        pixabay = settings.PIXABAY.model_copy(update={'api_key': SecretStr('')})  # nor the real Pixabay
        return Pipeline(self.lm, lm_studio, config, self.manager, progress=lambda line: None,
                        publishing=publishing, pixabay=pixabay, events=events).run()

    def test_articles_become_checked_drafts(self):
        run = self.run_pipeline()

        self.assertEqual(run.stats['group']['new_events'], 2)
        release, hack = Event.objects.get(category='ai'), Event.objects.get(category='crypto')
        self.assertEqual(release.articles.count(), 2)

        # the invented "70 billion parameters" fact fails the code check and is never verified
        self.assertEqual(release.facts.filter(status=Fact.Status.REJECTED).count(), 1)
        self.assertFalse(release.facts.filter(status=Fact.Status.VERIFIED, statement__contains='70 billion').exists())

        draft = release.drafts.get()
        self.assertEqual(draft.status, Draft.Status.READY)
        self.assertNotIn('best model ever', draft.body)  # cut by the card check
        self.assertIn('best model ever', draft.written_body)
        self.assertEqual(draft.image_queries, ['circuit board', 'server room', 'data center'])  # cleaned, at most 3

        # one source on a hack: the card is fine, but a human must look
        hack_draft = hack.drafts.get()
        self.assertEqual(hack_draft.status, Draft.Status.REVIEW)
        self.assertIn('high-risk event with 1 source', hack_draft.reasons[0])

        # both are queued for the site; no key, so nothing is sent
        self.assertEqual(run.stats['publish'], {'queued': 2, 'waiting': 2, 'sent': 0})

    def test_models_are_loaded_by_phase_and_each_step_uses_its_model(self):
        self.run_pipeline()

        self.assertEqual(self.manager.used, [('worker', 'embed'), ('worker', 'embed'), ('writer',), ('worker', 'embed')])
        writing = {model for model, step in self.lm.calls if step.startswith('You write')}
        self.assertEqual(writing, {'writer'})
        self.assertNotIn('writer', {model for model, step in self.lm.calls if not step.startswith('You write')})

    def test_a_second_run_has_nothing_to_do(self):
        self.run_pipeline()
        calls = len(self.lm.calls)

        run = self.run_pipeline()

        self.assertEqual(len(self.lm.calls), calls)
        self.assertEqual(run.stats['group'], {'articles': 0})
        self.assertEqual(Draft.objects.count(), 2)

    def test_a_single_source_event_waits_for_other_sources(self):
        self.run_pipeline(single_source_wait_hours=24)

        hack = Event.objects.get(articles__canonical_url__endswith='/h1')
        self.assertEqual((hack.status, hack.drafts.count()), (Event.Status.COLLECTING, 0))

    def test_unsupported_headline_sends_the_draft_to_review(self):
        self.lm.card_verdict = 'unclear'

        self.run_pipeline()

        self.assertEqual(set(Draft.objects.values_list('status', flat=True)), {Draft.Status.REVIEW})

    def test_a_chosen_event_is_written_again(self):
        self.run_pipeline()
        release = Event.objects.get(category='ai')
        calls = len(self.lm.calls)

        run = self.run_pipeline(events=[release.pk])

        self.assertEqual(release.drafts.count(), 2)
        self.assertEqual(Event.objects.get(category='crypto').drafts.count(), 1)  # the other one is left alone
        self.assertEqual(run.stats['group'], {'articles': 0, 'skipped': 'rewriting chosen events only'})
        self.assertGreater(len(self.lm.calls), calls)

    def test_a_late_article_joins_the_drafted_event(self):
        self.run_pipeline()
        make_article(Source.objects.get(slug='coins'), 'x3', 'Model X pricing explained', RELEASE, hours_ago=0)

        self.run_pipeline()

        release = Event.objects.get(category='ai')
        self.assertEqual((release.articles.count(), release.has_new_articles), (3, True))


class QuoteContextTests(TestCase):
    def test_the_quote_paragraph_and_its_neighbours(self):
        source = Source.objects.create(name='S', slug='s', feed_url='https://example.com/s', category=Category.AI)
        article = make_article(source, 'q', 'Title', 'One.\nTwo has the quote.\nThree.\nFour.', hours_ago=1)

        self.assertEqual(quote_context('the quote', article), 'One.\nTwo has the quote.\nThree.')


class LmsModelManagerTests(SimpleTestCase):
    def test_unloads_what_is_not_wanted_and_loads_what_is_missing(self):
        commands = []

        def run(args):
            commands.append(args)
            return json.dumps([{'identifier': 'big-writer'}, {'identifier': 'embed'}]) if args[0] == 'ps' else ''

        LmsModelManager(ttl=300, run=run).use(LoadSpec('worker', context=8192), LoadSpec('embed'))

        self.assertEqual(commands, [
            ['ps', '--json'],
            ['unload', 'big-writer'],
            ['load', 'worker', '--parallel', '1', '--ttl', '300', '-y', '--context-length', '8192'],
        ])
