"""Stock photos for drafts (stage `images`, no model). Pixabay, searched with the writer's image_queries and then
the category; the first photo no other event uses. Downloaded at once: Pixabay links expire in 24 hours and must not
be hotlinked, so the file goes to the site with the story (publisher/)."""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..models import Draft, StockImage

SEARCH_URL = 'https://pixabay.com/api/'
MIN_WIDTH = 1200  # the site's largest variant
MAX_BYTES = 15 * 1024 * 1024
# When the writer's searches find nothing new
CATEGORY_QUERIES = {'ai': 'artificial intelligence', 'it': 'computer technology', 'crypto': 'cryptocurrency'}


class ImageError(Exception):
    pass


@dataclass(frozen=True)
class Photo:
    external_id: str
    url: str
    page_url: str
    author: str
    author_url: str


class Pixabay:
    source = 'pixabay'

    def __init__(self, http: httpx.Client, api_key: str):
        self.http = http
        self.api_key = api_key

    def search(self, query: str) -> list[Photo]:
        params = {'key': self.api_key, 'q': query[:100], 'image_type': 'photo', 'orientation': 'horizontal',
                  'min_width': MIN_WIDTH, 'safesearch': 'true', 'per_page': 20}
        hits = self._get(SEARCH_URL, params=params).json().get('hits', [])
        return [Photo(external_id=f'pixabay-{hit["id"]}', url=hit['largeImageURL'], page_url=hit['pageURL'],
                      author=hit['user'], author_url=f'https://pixabay.com/users/{hit["user"]}-{hit["user_id"]}/')
                for hit in hits]

    def download(self, photo: Photo) -> bytes:
        response = self._get(photo.url)
        if not response.headers.get('content-type', '').startswith('image/'):
            raise ImageError(f'{photo.external_id}: not an image ({response.headers.get("content-type")})')
        if len(response.content) > MAX_BYTES:
            raise ImageError(f'{photo.external_id}: larger than {MAX_BYTES} bytes')
        return response.content

    def _get(self, url: str, params=None) -> httpx.Response:
        # httpx's own messages carry the URL, and with it the key: say less
        try:
            response = self.http.get(url, params=params)
        except httpx.TransportError as exc:
            raise ImageError(f'Pixabay: {type(exc).__name__}') from None
        if response.status_code != 200:
            raise ImageError(f'Pixabay: {response.status_code} {response.text[:200]}')
        return response


class ImagePicker:
    def __init__(self, finder: Pixabay, media_root: Path):
        self.finder = finder
        self.media_root = Path(media_root)

    def pick(self, draft: Draft) -> StockImage | None:
        """The event's photo if an earlier draft has one, else a new one; None if nothing new was found."""
        if earlier := StockImage.objects.filter(drafts__event=draft.event_id).first():
            return earlier
        used = set(StockImage.objects.values_list('external_id', flat=True))
        queries = [*draft.image_queries, CATEGORY_QUERIES.get(draft.event.category)]
        for query in filter(None, queries):
            for photo in self.finder.search(query):
                if photo.external_id not in used:
                    return self._store(photo, query)
        return None

    def _store(self, photo: Photo, query: str) -> StockImage:
        name = f'images/{photo.external_id}{Path(urlsplit(photo.url).path).suffix or ".jpg"}'
        path = self.media_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.finder.download(photo))
        return StockImage.objects.create(external_id=photo.external_id, source=self.finder.source, file=name,
                                         author=photo.author, author_url=photo.author_url, page_url=photo.page_url,
                                         query=query[:200])
