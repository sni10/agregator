from django.contrib import admin, messages
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import OutboundJob


@admin.register(OutboundJob)
class OutboundJobAdmin(admin.ModelAdmin):
    """The outbox: drafts on their way to the site (pipeline --stage publish sends the pending ones)."""

    list_display = ['external_id', 'headline', 'status', 'remote_status', 'remote_revision', 'attempts',
                    'next_attempt_at', 'sent_at']
    list_filter = ['status', 'remote_status']
    search_fields = ['external_id', 'draft__headline']
    readonly_fields = ['draft_link', 'external_id', 'status', 'attempts', 'next_attempt_at', 'last_error',
                       'remote_status', 'remote_revision', 'remote_slug', 'sent_at', 'payload', 'created_at']
    fields = readonly_fields
    actions = ['resend']
    list_select_related = ['draft']

    def has_add_permission(self, request):
        return False

    @admin.display(description='headline')
    def headline(self, job):
        return job.draft.headline

    @admin.display(description='draft')
    def draft_link(self, job):
        return format_html('<a href="{}">{}</a>', reverse('admin:pipeline_draft_change', args=[job.draft_id]),
                           job.draft.headline)

    @admin.action(description='Send again at the next publish')
    def resend(self, request, queryset):
        queryset.update(status=OutboundJob.Status.PENDING, attempts=0, next_attempt_at=timezone.now())
        self.message_user(request, 'Queued: run pipeline --stage publish.', messages.SUCCESS)
