from django.test import SimpleTestCase

from sources.services.normalize import canonical_url, content_hash, html_to_text


class CanonicalUrlTests(SimpleTestCase):
    def test_drops_tracking_params_fragment_and_trailing_slash(self):
        self.assertEqual(
            canonical_url('HTTPS://Example.COM/news/item/?utm_source=rss&id=7&fbclid=x#top'),
            'https://example.com/news/item?id=7',
        )

    def test_keeps_non_default_port_and_drops_default_one(self):
        self.assertEqual(canonical_url('https://example.com:443/a'), 'https://example.com/a')
        self.assertEqual(canonical_url('http://example.com:8080/a'), 'http://example.com:8080/a')

    def test_root_path(self):
        self.assertEqual(canonical_url('https://example.com'), 'https://example.com/')


class ContentHashTests(SimpleTestCase):
    def test_ignores_case_and_whitespace(self):
        self.assertEqual(content_hash('Title', 'Body  text\n'), content_hash('title', 'body text'))

    def test_differs_for_different_text(self):
        self.assertNotEqual(content_hash('Title', 'one'), content_hash('Title', 'two'))


class HtmlToTextTests(SimpleTestCase):
    def test_strips_tags_and_entities(self):
        self.assertEqual(html_to_text('<p>AT&amp;T <b>ships</b>\n 5G</p>'), 'AT&T ships 5G')
