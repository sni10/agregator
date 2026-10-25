"""Send the outbox to the site. A story is created on the first send and updated (If-Match) afterwards: a second
draft of the same event, or a retry after a timeout that had in fact gone through. Then its photo, if it has a new one."""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import httpx
from django.conf import settings
from django.utils import timezone

from pipeline.models import StockImage

from .models import OutboundJob

MAX_BACKOFF = timedelta(hours=6)
# Worth retrying: the site is down or busy, the key is being fixed, an admin edit raced the update
RETRY_CODES = {403, 412, 429}


class SendError(Exception):
    def __init__(self, message: str, retry: bool):
        super().__init__(message)
        self.retry = retry


class PublishingClient:
    def __init__(self, http: httpx.Client, base_url: str, api_key: str):
        self.http = http
        self.base_url = base_url.rstrip('/')
        self.headers = {'X-Api-Key': api_key}

    def send(self, external_id: str, payload: dict, request_id: str) -> dict:
        """The story's state on the site after creating or updating it."""
        headers = {**self.headers, 'X-Request-Id': request_id}
        url = f'{self.base_url}/publishing/publications'
        current = self._call('GET', f'{url}/{external_id}', headers, allow_404=True)
        if current is None:
            return self._call('POST', url, headers, json=payload)
        return self._call('PUT', f'{url}/{external_id}', {**headers, 'If-Match': f'"{current["revision"]}"'},
                          json=payload)

    def send_image(self, external_id: str, image: StockImage, request_id: str) -> dict:
        """The story's state after setting its photo (multipart: the file and its credit)."""
        path = Path(settings.MEDIA_ROOT) / image.file
        data = {'external_id': image.external_id, 'source': image.source, 'author': image.author,
                'author_url': image.author_url, 'page_url': image.page_url}
        return self._call('PUT', f'{self.base_url}/publishing/publications/{external_id}/image',
                          {**self.headers, 'X-Request-Id': request_id}, data=data,
                          files={'file': (path.name, path.read_bytes())})

    def _call(self, method, url, headers, json=None, allow_404=False, **content) -> dict | None:
        try:
            response = self.http.request(method, url, headers=headers, json=json, **content)
        except httpx.TransportError as exc:
            raise SendError(f'{method} {url}: {type(exc).__name__}: {exc}', retry=True) from exc
        if allow_404 and response.status_code == 404:
            return None
        if response.status_code >= 400:
            retry = response.status_code >= 500 or response.status_code in RETRY_CODES
            raise SendError(f'{method} {url}: {response.status_code} {response.text[:500]}', retry=retry)
        return response.json()


@dataclass
class SendStats:
    sent: int = 0
    retry: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def send_due(client: PublishingClient, max_attempts: int, progress: Callable[[str], None] = lambda line: None,
             now=None) -> SendStats:
    now = now or timezone.now()
    stats = SendStats()
    jobs = OutboundJob.objects.filter(status=OutboundJob.Status.PENDING, next_attempt_at__lte=now).order_by('id')
    for job in jobs:
        job.attempts += 1
        try:
            state = client.send(job.external_id, job.payload, request_id=f'job-{job.pk}-{job.attempts}')
        except SendError as exc:
            job.last_error = str(exc)
            if exc.retry and job.attempts < max_attempts:
                job.next_attempt_at = now + min(timedelta(minutes=2 ** job.attempts), MAX_BACKOFF)
                stats.retry += 1
                progress(f'  {job.external_id}: retry at {job.next_attempt_at:%H:%M} — {exc}')
            else:
                job.status = OutboundJob.Status.FAILED
                stats.failed += 1
                progress(f'  {job.external_id}: FAILED — {exc}')
            stats.errors.append(str(exc))
        else:
            job.status, job.sent_at, job.last_error = OutboundJob.Status.SENT, now, ''
            job.remote_status, job.remote_revision, job.remote_slug = state['status'], state['revision'], state['slug']
            job.remote_image = state.get('image') or ''
            stats.sent += 1
            progress(f'  {job.external_id}: {state["status"]} r{state["revision"]} /{state["slug"]}')
        job.save()
    return stats


def send_images(client: PublishingClient, progress: Callable[[str], None] = lambda line: None) -> SendStats:
    """Sent stories whose draft has a photo the site does not show yet. A failure is tried again next run."""
    stats = SendStats()
    jobs = (OutboundJob.objects.filter(status=OutboundJob.Status.SENT, draft__image__isnull=False)
            .select_related('draft__image').order_by('id'))
    for job in jobs:
        image = job.draft.image
        if job.remote_image == image.external_id:
            continue
        try:
            state = client.send_image(job.external_id, image, request_id=f'job-{job.pk}-image')
        except (SendError, OSError) as exc:
            stats.failed += 1
            stats.errors.append(str(exc))
            progress(f'  {job.external_id}: photo FAILED — {exc}')
            continue
        job.remote_image = state.get('image') or ''
        job.save(update_fields=['remote_image', 'updated_at'])
        stats.sent += 1
        progress(f'  {job.external_id}: photo {image.external_id} by {image.author}')
    return stats
