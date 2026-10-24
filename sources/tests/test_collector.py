from datetime import UTC, datetime
from unittest import mock

import httpx
from django.test import TestCase

from sources.models import Article, Category, Source
from sources.services.cleanup import clean_text
from sources.services.collector import FeedEntry, RssCollector, TrafilaturaExtractor, collect_source, extract_text

LONG = 'The article goes on with details that make it longer than a teaser. ' * 10

FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Example</title>
  <item>
    <title>Model X &amp; friends</title>
    <link>https://example.com/model-x/?utm_source=rss</link>
    <pubDate>Thu, 22 Oct 2026 10:00:00 GMT</pubDate>
    <description>&lt;p&gt;Model X is out.&lt;/p&gt;</description>
  </item>
  <item><title>No link</title></item>
</channel></rss>"""


class FakeCollector:
    def __init__(self, entries=(), error=None):
        self.entries, self.error = list(entries), error

    def fetch(self, source):
        if self.error:
            raise self.error
        return self.entries


class FakeExtractor:
    """`texts[url]` is the page's text, or a list of texts for consecutive downloads."""

    def __init__(self, texts):
        self.texts = texts
        self.calls = 0

    def extract(self, url, boilerplate=frozenset()):
        self.calls += 1
        text = self.texts.get(url)
        return text.pop(0) if isinstance(text, list) else text


def make_source(**fields):
    return Source.objects.create(**{
        'name': 'Example', 'slug': 'example', 'feed_url': 'https://example.com/feed', 'category': Category.AI, **fields,
    })


class RssCollectorTests(TestCase):
    def test_parses_entries_and_skips_ones_without_link(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=FEED))
        with httpx.Client(transport=transport) as http:
            entries = RssCollector(http, max_entries=10).fetch(make_source())

        self.assertEqual(entries, [FeedEntry(
            url='https://example.com/model-x',
            title='Model X & friends',
            published_at=datetime(2026, 10, 22, 10, tzinfo=UTC),
            summary='Model X is out.',
        )])

    def test_rejects_a_page_that_is_not_a_feed(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b'<html>Not found'))
        with httpx.Client(transport=transport) as http, self.assertRaises(ValueError):
            RssCollector(http, max_entries=10).fetch(make_source())


@mock.patch('sources.services.collector.RETRY_PAUSE', 0)
class CollectSourceTests(TestCase):
    def test_stores_new_entries_with_full_text_and_skips_known_urls(self):
        source = make_source()
        entries = [FeedEntry(url='https://example.com/a', title='A', summary='short')]
        extractor = FakeExtractor({'https://example.com/a': f'Full   text of A.\n\n{LONG}'})

        first = collect_source(source, FakeCollector(entries), extractor)
        second = collect_source(source, FakeCollector(entries), extractor)

        article = Article.objects.get()
        self.assertEqual((article.text, article.status), (f'Full text of A.\n{LONG.strip()}', Article.Status.EXTRACTED))
        self.assertEqual((first.new, second.new, second.known), (1, 0, 1))
        source.refresh_from_db()
        self.assertIsNotNone(source.last_polled_at)

    def test_marks_same_text_under_another_url_as_duplicate(self):
        source = make_source()
        entries = [FeedEntry(url=f'https://example.com/{slug}', title='Same', summary='same body') for slug in 'ab']

        stats = collect_source(source, FakeCollector(entries), extractor=None)

        self.assertEqual(stats.duplicates, 1)
        self.assertEqual(
            list(Article.objects.order_by('id').values_list('status', flat=True)),
            [Article.Status.NEW, Article.Status.DUPLICATE],
        )

    def test_records_feed_error_on_the_source(self):
        source = make_source()

        stats = collect_source(source, FakeCollector(error=ValueError('not a feed')), extractor=None)

        source.refresh_from_db()
        self.assertEqual(source.last_error, 'ValueError: not a feed')
        self.assertEqual(stats.errors, [source.last_error])
        self.assertFalse(Article.objects.exists())

    def test_skips_page_download_for_a_source_without_full_text(self):
        source = make_source(full_text=False)
        entries = [FeedEntry(url='https://example.com/a', title='A', summary='Feed summary')]

        collect_source(source, FakeCollector(entries), FakeExtractor({'https://example.com/a': LONG}))

        article = Article.objects.get()
        self.assertEqual((article.text, article.status), ('', Article.Status.NEW))

    def test_cuts_boilerplate_repeated_across_the_batch_and_remembers_it(self):
        source = make_source()
        promo = 'Stablecoins sustain 5-7% yield. TAM grows to $4B in 3 years.'
        urls = [f'https://example.com/{n}' for n in range(5)]
        texts = {url: f'Story {n}. {LONG}\n{promo}\nWhy it matters:' for n, url in enumerate(urls)}

        collect_source(source, FakeCollector([FeedEntry(url=url, title=url) for url in urls]), FakeExtractor(texts))

        for article in Article.objects.all():
            self.assertNotIn('5-7%', article.text)
            self.assertNotIn('Why it matters', article.text)
        source.refresh_from_db()
        self.assertCountEqual(source.boilerplate, [promo, 'Why it matters:'])

        # the cleaned history has no repeats left to find: the remembered paragraph is cut all the same
        url = 'https://example.com/next'
        collect_source(source, FakeCollector([FeedEntry(url=url, title='next')]), FakeExtractor({url: f'{LONG}\n{promo}'}))
        self.assertNotIn('5-7%', Article.objects.get(canonical_url=url).text)

    def test_teaser_is_not_full_text(self):
        source = make_source()
        url = 'https://example.com/teaser'
        teaser = 'Title of the story\nSubtitle of the story. Make preferred on Share this article'

        stats = collect_source(source, FakeCollector([FeedEntry(url=url, title='T')]), FakeExtractor({url: teaser}))

        article = Article.objects.get()
        self.assertEqual((article.text, article.status, stats.extract_failed), ('', Article.Status.EXTRACT_FAILED, 1))
        self.assertEqual(article.summary, teaser)  # the feed gave no summary: the teaser is better than nothing

    def test_next_runs_retry_an_article_without_full_text_a_limited_number_of_times(self):
        source = make_source()
        url = 'https://example.com/a'
        entries = [FeedEntry(url=url, title='A')]
        # every run downloads twice (a teaser is retried at once); the article comes on the 3rd run's 2nd download
        extractor = FakeExtractor({url: ['Teaser'] * 5 + [LONG]})

        first = collect_source(source, FakeCollector(entries), extractor)
        second = collect_source(source, FakeCollector(entries), extractor)
        third = collect_source(source, FakeCollector(entries), extractor)

        article = Article.objects.get()
        self.assertEqual((article.status, article.extract_attempts), (Article.Status.EXTRACTED, 3))
        self.assertEqual((first.extract_failed, second.recovered, third.recovered), (1, 0, 1))

        collect_source(source, FakeCollector(entries), extractor)
        self.assertEqual(extractor.calls, 6)  # extracted: no more downloads

    def test_stops_retrying_after_max_attempts(self):
        source = make_source()
        url = 'https://example.com/a'
        extractor = FakeExtractor({url: 'Copyright notice only'})

        for _ in range(5):
            collect_source(source, FakeCollector([FeedEntry(url=url, title='A')]), extractor)

        self.assertEqual((Article.objects.get().extract_attempts, extractor.calls), (3, 6))


class ExtractTextTests(TestCase):
    def test_downloads_a_teaser_again_once(self):
        extractor = FakeExtractor({'u': ['Teaser', LONG]})
        pauses = []

        self.assertEqual(extract_text(extractor, 'u', sleep=pauses.append), LONG.strip())
        self.assertEqual((extractor.calls, len(pauses)), (2, 1))

    def test_returns_the_short_text_when_the_retry_is_short_too(self):
        extractor = FakeExtractor({'u': 'Copyright notice only'})

        self.assertEqual(extract_text(extractor, 'u', sleep=lambda seconds: None), 'Copyright notice only')
        self.assertEqual(extractor.calls, 2)


class CleanTextTests(TestCase):
    def test_drops_price_tickers_and_repeats_inside_the_text(self):
        ticker = 'BTC $81,282.50 Up 0.11 percent 0.11% ETH $2,395.38 Up 0.61 percent XRP $1.31 Down 0.3 percent'

        cleaned = clean_text(f'Intro.\n{ticker}\nIntro.\nEnd.', others=[])

        self.assertEqual((cleaned.text, cleaned.removed), ('Intro.\nEnd.', 2))

    def test_keeps_a_paragraph_only_two_other_articles_share(self):
        quote = 'The company declined to comment.'

        self.assertEqual(clean_text(f'News.\n{quote}', others=[quote, quote]).text, f'News.\n{quote}')


class TrafilaturaExtractorTests(TestCase):
    STORY = ''.join(f'<p>Paragraph {n} of the story tells what happened and who said what about it.</p>'
                    for n in range(12))
    RELATED = [f'- Related story number {n} with a long enough headline to look like text' for n in range(12)]
    PAGE = (f'<html><body><nav><ul><li><p>{RELATED[0]}</p></li></ul></nav>'
            f'<article><h1>Story</h1>{STORY}</article></body></html>')

    def extract(self, boilerplate=frozenset()):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, html=self.PAGE))
        with httpx.Client(transport=transport) as http:
            return TrafilaturaExtractor(http).extract('https://example.com/story', boilerplate)

    def test_uses_trafilatura_on_an_ordinary_page(self):
        self.assertIn('Paragraph 11 of the story', self.extract())

    def test_takes_the_page_paragraphs_when_trafilatura_picks_known_boilerplate(self):
        # as on The Register: trafilatura returns the "related articles" list, already known for the source
        related = '\n'.join(self.RELATED)
        with mock.patch('sources.services.collector.trafilatura.extract', return_value=related):
            text = self.extract(frozenset(self.RELATED))

        self.assertIn('Paragraph 0 of the story', text)
        self.assertNotIn('Related story', text)

    def test_keeps_trafilatura_when_the_paragraphs_are_not_much_longer(self):
        main = '\n'.join(f'Paragraph {n} of the story tells what happened and who said what about it.'
                         for n in range(8))
        with mock.patch('sources.services.collector.trafilatura.extract', return_value=main):
            self.assertEqual(self.extract(), main)
