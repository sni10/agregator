"""Checked drafts -> outbox -> the site (pipeline stage `publish`)."""
from collections.abc import Callable

import httpx

from pipeline.models import Draft

from .models import OutboundJob
from .payload import build_payload, external_id
from .sender import PublishingClient, send_due, send_images, send_tags


def enqueue() -> int:
    """A job for every ready or review draft not queued yet."""
    drafts = (Draft.objects.filter(status__in=[Draft.Status.READY, Draft.Status.REVIEW], outbound__isnull=True)
              .select_related('event').order_by('id'))
    jobs = [OutboundJob(draft=draft, external_id=external_id(draft), payload=build_payload(draft)) for draft in drafts]
    OutboundJob.objects.bulk_create(jobs)
    return len(jobs)


def publish(config, progress: Callable[[str], None] = lambda line: None) -> dict:
    queued = enqueue()
    waiting = OutboundJob.objects.filter(status=OutboundJob.Status.PENDING).count()
    progress(f'{queued} drafts queued, {waiting} waiting to be sent to {config.url}')
    api_key = config.api_key.get_secret_value()
    if not api_key:
        progress('PUBLISHING_API_KEY is empty: nothing sent')
        return {'queued': queued, 'waiting': waiting, 'sent': 0}
    with httpx.Client(timeout=config.timeout) as http:
        client = PublishingClient(http, config.url, api_key)
        stats = send_due(client, config.max_attempts, progress)
        photos = send_images(client, progress)
        tags = send_tags(client, progress)
    return {'queued': queued, 'sent': stats.sent, 'retry': stats.retry, 'failed': stats.failed,
            'photos': photos.sent, 'photos_failed': photos.failed, 'tags': tags.sent, 'tags_failed': tags.failed}
