from django.db import models


class Category(models.TextChoices):
    IT = 'it', 'IT'
    AI = 'ai', 'AI'
    CRYPTO = 'crypto', 'Crypto'


class Source(models.Model):
    class License(models.TextChoices):
        UNVERIFIED = 'unverified', 'Terms not checked yet'
        SUMMARY = 'summary', 'Links and short quotes allowed'
        DENIED = 'denied', 'Do not use'

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=100, unique=True)
    feed_url = models.URLField(max_length=500, unique=True)
    site_url = models.URLField(max_length=500, blank=True)
    category = models.CharField(max_length=10, choices=Category)
    trust_tier = models.PositiveSmallIntegerField(
        default=2, help_text='1 = primary (vendor, official, research), 2 = established media, 3 = aggregator / blog',
    )
    license_policy = models.CharField(max_length=20, choices=License, default=License.UNVERIFIED)
    enabled = models.BooleanField(default=True)
    full_text = models.BooleanField(
        default=True, help_text='Download article pages. Off for sites that block bots: the feed summary is used',
    )
    boilerplate = models.JSONField(
        default=list, blank=True, editable=False,
        help_text='Paragraphs repeated across the articles of this source (promo, share buttons): cut from texts',
    )
    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ['category', 'name']

    def __str__(self):
        return self.name


class Article(models.Model):
    class Status(models.TextChoices):
        NEW = 'new', 'Feed entry only'
        EXTRACTED = 'extracted', 'Full text extracted'
        EXTRACT_FAILED = 'extract_failed', 'Full text not available'
        DUPLICATE = 'duplicate', 'Same text as another article'

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name='articles')
    canonical_url = models.URLField(max_length=1000, unique=True)
    title = models.CharField(max_length=500)
    author = models.CharField(max_length=300, blank=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    fetched_at = models.DateTimeField(auto_now_add=True)
    summary = models.TextField(blank=True, help_text='Plain text from the feed entry')
    text = models.TextField(blank=True, help_text='Plain text extracted from the article page')
    language = models.CharField(max_length=10, blank=True)
    content_hash = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=20, choices=Status, default=Status.NEW)
    extract_attempts = models.PositiveSmallIntegerField(default=0, help_text='Page downloads made for the full text')

    class Meta:
        ordering = ['-published_at', '-id']

    def __str__(self):
        return self.title

    @property
    def body(self) -> str:
        """The best text available: the extracted page, else the feed summary."""
        return self.text or self.summary
