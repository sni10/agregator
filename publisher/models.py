from django.db import models
from django.utils import timezone

from pipeline.models import Draft


class OutboundJob(models.Model):
    """A checked draft on its way to the site. Kept until sent: the site may be down or the PC offline."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Waiting to be sent'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Gave up: see the error, resend from the admin'

    draft = models.OneToOneField(Draft, on_delete=models.CASCADE, related_name='outbound')
    # event-<pk>: every draft of an event updates the same story on the site
    external_id = models.CharField(max_length=100, db_index=True)
    payload = models.JSONField()
    status = models.CharField(max_length=10, choices=Status, default=Status.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True)
    remote_status = models.CharField(max_length=20, blank=True, help_text="The story's status on the site")
    remote_revision = models.PositiveIntegerField(null=True, blank=True)
    remote_slug = models.CharField(max_length=120, blank=True)
    remote_tags = models.JSONField(null=True, blank=True, help_text="The story's tags on the site")
    remote_image = models.CharField(max_length=100, blank=True, help_text="The story's photo on the site")
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.external_id} ({self.get_status_display()})'
