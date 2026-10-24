from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html, format_html_join, linebreaks

from .models import Draft, Event, Fact, PipelineRun

CARD = ('<div style="max-width: 46em; font-size: 15px; line-height: 1.5">'
        '<h2 style="margin: 0 0 .4em">{}</h2><p><b>{}</b></p>{}</div>')


class FactInline(admin.TabularInline):
    model = Fact
    fields = ['status', 'statement', 'quote', 'article', 'problems']
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ['title', 'category', 'high_risk', 'status', 'source_count', 'last_article_at', 'draft_link']
    list_filter = ['status', 'category', 'high_risk']
    search_fields = ['title']
    readonly_fields = ['articles_list', 'first_published_at', 'last_article_at', 'has_new_articles', 'error',
                       'created_at', 'updated_at']
    fields = ['title', 'category', 'high_risk', 'status', *readonly_fields]
    inlines = [FactInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('articles__source', 'drafts')

    @admin.display(description='sources')
    def source_count(self, event):
        return len(event.sources)

    @admin.display(description='articles')
    def articles_list(self, event):
        rows = ((reverse('admin:sources_article_change', args=[a.pk]), a.source.name, a.title, a.canonical_url)
                for a in event.articles.all())
        return format_html('<ul>{}</ul>', format_html_join(
            '', '<li><a href="{}">{}: {}</a> (<a href="{}" target="_blank">original</a>)</li>', rows))

    @admin.display(description='draft')
    def draft_link(self, event):
        drafts = list(event.drafts.all())
        if not drafts:
            return '—'
        draft = drafts[0]
        return format_html('<a href="{}">{}</a>', reverse('admin:pipeline_draft_change', args=[draft.pk]),
                           draft.get_status_display())


@admin.register(Draft)
class DraftAdmin(admin.ModelAdmin):
    """The cards the pipeline wrote: ready ones go to the site once it exists, review ones need a human."""

    list_display = ['headline', 'status', 'category', 'sources', 'cut', 'created_at']
    list_filter = ['status', 'event__category', 'event__high_risk']
    search_fields = ['headline', 'body']
    readonly_fields = ['card', 'image_queries', 'reason_list', 'check_table', 'facts_list', 'event', 'written_body',
                       'writer_model', 'checker_model', 'created_at']
    fields = ['status', *readonly_fields]
    list_select_related = ['event']

    def has_add_permission(self, request):
        return False

    @admin.display(description='category', ordering='event__category')
    def category(self, draft):
        return draft.event.get_category_display()

    @admin.display(description='sources')
    def sources(self, draft):
        return len(draft.event.sources)

    @admin.display(description='sentences cut')
    def cut(self, draft):
        return sum(1 for check in draft.checks if check['problems'] and check['part'] == 'body') or '—'

    @admin.display(description='card (as it would be published)')
    def card(self, draft):
        return format_html(CARD, draft.headline, draft.summary, linebreaks(draft.body, autoescape=True))

    @admin.display(description='why review')
    def reason_list(self, draft):
        if not draft.reasons:
            return '—'
        return format_html('<ul>{}</ul>', format_html_join('', '<li>{}</li>', ((r,) for r in draft.reasons)))

    @admin.display(description='card check')
    def check_table(self, draft):
        if not draft.checks:
            return 'not checked yet'
        rows = (('✓' if not c['problems'] else '✗', c['part'],
                 '' if not c['problems'] else 'text-decoration: line-through; color: #b00', c['text'],
                 '; '.join(c['problems'])) for c in draft.checks)
        return format_html('<table>{}</table>', format_html_join(
            '', '<tr><td>{}</td><td>{}</td><td style="{}">{}</td><td>{}</td></tr>', rows))

    @admin.display(description='facts it was written from')
    def facts_list(self, draft):
        rows = ((fact.statement, fact.article.source.name, fact.quote)
                for fact in draft.facts.select_related('article__source'))
        return format_html('<ol>{}</ol>', format_html_join(
            '', '<li>{} <small style="color: #666">— {}: “{}”</small></li>', rows))


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    list_display = ['started_at', 'finished_at', 'stats', 'error']
    readonly_fields = ['started_at', 'finished_at', 'stats', 'error']
