import shutil
import tempfile
from pathlib import Path

import httpx
from django.test import TestCase

from pipeline.models import Draft, Event, StockImage
from pipeline.steps.images import ImageError, ImagePicker, Pixabay

JPEG = b'\xff\xd8\xff\xe0 a photo'


def hit(photo_id, user='Josch13'):
    return {'id': photo_id, 'pageURL': f'https://pixabay.com/photos/x-{photo_id}/', 'user': user, 'user_id': 48777,
            'largeImageURL': f'https://pixabay.com/get/g{photo_id}_1280.jpg'}


class FakePixabay:
    """Search answers by query; any /get/ link is a JPEG."""

    def __init__(self, results, status=200):
        self.results, self.status, self.searches = results, status, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == '/api/':
            self.searches.append(request.url.params['q'])
            return httpx.Response(self.status, json={'hits': self.results.get(request.url.params['q'], [])})
        return httpx.Response(200, content=JPEG, headers={'content-type': 'image/jpeg'})


class ImagePickerTests(TestCase):
    def setUp(self):
        self.media = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)

    def draft(self, queries, category='ai'):
        event = Event.objects.create(title='t', category=category, status=Event.Status.DRAFTED)
        return Draft.objects.create(event=event, headline='h', summary='s', body='b', written_body='b',
                                    status=Draft.Status.READY, writer_model='w', image_queries=queries)

    def pick(self, site, draft):
        with httpx.Client(transport=httpx.MockTransport(site)) as http:
            return ImagePicker(Pixabay(http, 'secret-key'), self.media).pick(draft)

    def test_the_first_unused_photo_is_downloaded_with_its_credit(self):
        StockImage.objects.create(external_id='pixabay-1', source='pixabay', file='f', author='a',
                                  page_url='https://pixabay.com/1', query='q')
        site = FakePixabay({'server room': [hit(1), hit(2)]})

        image = self.pick(site, self.draft(['server room']))

        self.assertEqual((image.external_id, image.author, image.query), ('pixabay-2', 'Josch13', 'server room'))
        self.assertEqual(image.author_url, 'https://pixabay.com/users/Josch13-48777/')
        self.assertEqual((self.media / image.file).read_bytes(), JPEG)

    def test_the_category_is_searched_when_the_writers_queries_find_nothing(self):
        site = FakePixabay({'artificial intelligence': [hit(5)]})

        image = self.pick(site, self.draft(['glowing abstract thing']))

        self.assertEqual(site.searches, ['glowing abstract thing', 'artificial intelligence'])
        self.assertEqual(image.external_id, 'pixabay-5')

    def test_nothing_found_and_a_later_draft_of_the_event_keeps_the_photo(self):
        self.assertIsNone(self.pick(FakePixabay({}), self.draft(['nothing'])))

        first = self.draft(['chip'])
        first.image = self.pick(FakePixabay({'chip': [hit(7)]}), first)
        first.save()
        again = Draft.objects.create(event=first.event, headline='h2', summary='s', body='b', written_body='b',
                                     status=Draft.Status.READY, writer_model='w', image_queries=['other'])
        site = FakePixabay({'other': [hit(8)]})

        self.assertEqual(self.pick(site, again).external_id, 'pixabay-7')
        self.assertEqual(site.searches, [])

    def test_an_error_does_not_show_the_key(self):
        with self.assertRaises(ImageError) as error:
            self.pick(FakePixabay({}, status=429), self.draft(['x']))

        self.assertIn('429', str(error.exception))
        self.assertNotIn('secret-key', str(error.exception))
