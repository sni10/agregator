"""Send the outbox to the site. A story is created on the first send and updated (If-Match) afterwards: a second
draft of the same event, or a retry after a timeout that had in fact gone through."""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from django.utils import timezone

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

    def _call(self, method, url, headers, json=None, allow_404=False) -> dict | None:
        try:
            response = self.http.request(method, url, headers=headers, json=json)
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
            stats.sent += 1
            progress(f'  {job.external_id}: {state["status"]} r{state["revision"]} /{state["slug"]}')
        job.save()
    return stats
