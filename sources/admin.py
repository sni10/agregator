from django.contrib import admin

from .models import Article, Source


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'trust_tier', 'license_policy', 'enabled', 'full_text', 'last_polled_at',
                    'has_error']
    list_filter = ['category', 'enabled', 'license_policy', 'trust_tier']
    list_editable = ['enabled', 'full_text']
    search_fields = ['name', 'feed_url']
    prepopulated_fields = {'slug': ['name']}
    readonly_fields = ['boilerplate']

    @admin.display(boolean=True, description='error')
    def has_error(self, source):
        return bool(source.last_error)


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ['title', 'source', 'published_at', 'status']
    list_filter = ['source__category', 'status', 'source']
    search_fields = ['title', 'summary', 'text']
    date_hierarchy = 'published_at'
    list_select_related = ['source']
    readonly_fields = ['fetched_at', 'content_hash']
