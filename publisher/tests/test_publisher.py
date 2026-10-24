import json
from datetime import timedelta

import httpx
from django.test import TestCase
from django.utils import timezone

from pipeline.models import Draft, Event, Fact
from publisher.models import OutboundJob
from publisher.outbox import enqueue
from publisher.sender import PublishingClient, send_due
from sources.models import Article, Category, Source


class FakeSite:
    """The publishing API in memory: stories by external_id, with revisions."""

    def __init__(self, fail_with=None):
        self.stories, self.requests, self.fail_with = {}, [], fail_with

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, dict(request.headers)))
        if self.fail_with:
            return httpx.Response(self.fail_with, json={'detail': 'nope'})
        external_id = request.url.path.rsplit('/', 1)[1]
        if request.method == 'GET':
            story = self.stories.get(external_id)
            return httpx.Response(200, json=story) if story else httpx.Response(404, json={})
        payload = json.loads(request.content)
        if request.method == 'POST':
            story = {'external_id': payload['external_id'], 'slug': 'a-story', 'status': payload['status'],
                     'revision': 1}
            self.stories[payload['external_id']] = story
            return httpx.Response(201, json=story)
        story = self.stories[external_id]
        assert request.headers['If-Match'] == f'"{story["revision"]}"'
        story.update(revision=story['revision'] + 1, status=payload['status'])
        return httpx.Response(200, json=story)


class PublisherTests(TestCase):
    def setUp(self):
        source = Source.objects.create(name='CoinDesk', slug='coindesk', feed_url='https://example.com/f',
                                       category=Category.CRYPTO)
        article = Article.objects.create(source=source, canonical_url='https://example.com/a', title='ETF outflows',
                                         content_hash='a', published_at=timezone.now() - timedelta(hours=3))
        self.event = Event.objects.create(title='ETF outflows', category='crypto', status=Event.Status.DRAFTED,
                                          first_published_at=article.published_at)
        self.event.articles.add(article)
        fact = Fact.objects.create(event=self.event, article=article, statement='ETFs lost $635M.', quote='q',
                                   kind='number', status=Fact.Status.VERIFIED)
        self.draft = self.draft_for(Draft.Status.READY)
        self.draft.facts.add(fact)

    def draft_for(self, status):
        return Draft.objects.create(event=self.event, headline='ETFs lose $635M', summary='Outflows on Thursday.',
                                    body='First paragraph.\n\nSecond.', written_body='x', status=status,
                                    writer_model='writer', checker_model='checker', image_queries=['coins chart'])

    def send(self, site, max_attempts=10, now=None):
        with httpx.Client(transport=httpx.MockTransport(site)) as http:
            return send_due(PublishingClient(http, 'http://site', 'key'), max_attempts, now=now)

    def test_ready_and_review_drafts_are_queued_once_with_the_site_payload(self):
        Draft.objects.create(event=self.event, headline='h', summary='s', body='b', written_body='b',
                             status=Draft.Status.UNCHECKED, writer_model='w')

        self.assertEqual((enqueue(), enqueue()), (1, 0))

        payload = OutboundJob.objects.get().payload
        self.assertEqual((payload['external_id'], payload['status'], payload['category']),
                         (f'event-{self.event.pk}', 'published', 'crypto'))
        self.assertEqual(payload['body_md'], 'First paragraph.\n\nSecond.')
        self.assertEqual(payload['sources'][0]['publisher'], 'CoinDesk')
        self.assertEqual(payload['quality']['verified_claims'], 1)
        self.assertEqual(payload['image_queries'], ['coins chart'])

    def test_first_send_creates_the_story_and_a_later_draft_updates_it(self):
        site = FakeSite()
        enqueue()
        self.send(site)

        self.draft_for(Draft.Status.REVIEW)
        enqueue()
        stats = self.send(site)

        self.assertEqual(stats.sent, 1)
        self.assertEqual([method for method, _, _ in site.requests], ['GET', 'POST', 'GET', 'PUT'])
        jobs = list(OutboundJob.objects.order_by('id').values_list('status', 'remote_status', 'remote_revision'))
        self.assertEqual(jobs, [('sent', 'published', 1), ('sent', 'review', 2)])
        self.assertEqual(site.requests[1][2]['x-api-key'], 'key')

    def test_site_down_is_retried_later(self):
        enqueue()
        now = timezone.now()

        stats = self.send(FakeSite(fail_with=503), now=now)

        job = OutboundJob.objects.get()
        self.assertEqual((stats.retry, job.status, job.attempts), (1, 'pending', 1))
        self.assertEqual(job.next_attempt_at, now + timedelta(minutes=2))
        self.assertEqual(self.send(FakeSite(), now=now).sent, 0)  # not due yet
        self.assertEqual(self.send(FakeSite(), now=now + timedelta(minutes=3)).sent, 1)

    def test_a_rejected_payload_fails_at_once_and_retries_stop_at_the_limit(self):
        enqueue()
        self.send(FakeSite(fail_with=400))
        self.assertEqual(OutboundJob.objects.get().status, 'failed')

        OutboundJob.objects.update(status='pending', attempts=0)
        self.send(FakeSite(fail_with=503), max_attempts=1)
        self.assertEqual(OutboundJob.objects.get().status, 'failed')

    def test_no_connection_is_retried(self):
        def down(request):
            raise httpx.ConnectError('connection refused')

        enqueue()
        stats = self.send(down)

        self.assertEqual((stats.retry, OutboundJob.objects.get().status), (1, 'pending'))
