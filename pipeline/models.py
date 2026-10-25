"""Pipeline state (spec §6, local DB): events grouped from articles, their facts, and the drafts written from them."""
from django.db import models

from sources.models import Article, Category


class ArticleEmbedding(models.Model):
    """The article's embedding for grouping, computed once (title + start of the text)."""

    article = models.OneToOneField(Article, on_delete=models.CASCADE, primary_key=True, related_name='embedding')
    model = models.CharField(max_length=200)
    vector = models.JSONField()  # normalized floats; a few hundred articles a day: numpy is enough, no index


class Event(models.Model):
    """One real-world happening, reported by one or more articles."""

    class Status(models.TextChoices):
        COLLECTING = 'collecting', 'Waiting for more sources'
        FACTS = 'facts', 'Facts verified'
        DRAFTED = 'drafted', 'Draft written'
        SKIPPED = 'skipped', 'Not for the site (category other, too few facts)'
        FAILED = 'failed', 'A step failed: see error'

    title = models.CharField(max_length=500, help_text='Title of the first article until a draft exists')
    category = models.CharField(max_length=10, choices=Category, blank=True)
    high_risk = models.BooleanField(
        default=False, help_text='Hacks, charges and lawsuits, fines and regulation, crypto prices: two sources needed',
    )
    articles = models.ManyToManyField(Article, related_name='events')
    status = models.CharField(max_length=20, choices=Status, default=Status.COLLECTING, db_index=True)
    first_published_at = models.DateTimeField(null=True, blank=True)
    last_article_at = models.DateTimeField(null=True, blank=True, db_index=True)
    has_new_articles = models.BooleanField(default=False, help_text='Articles added after the draft was written')
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-last_article_at', '-id']

    def __str__(self):
        return self.title

    @property
    def sources(self) -> set[str]:
        return {article.source.slug for article in self.articles.all()}


class Fact(models.Model):
    """A statement extracted from one article, with the quote it rests on."""

    class Status(models.TextChoices):
        VERIFIED = 'verified', 'Verified'
        REJECTED = 'rejected', 'Rejected'

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='facts')
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name='facts')
    statement = models.TextField()
    quote = models.TextField()
    kind = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=Status)
    problems = models.JSONField(default=list, blank=True, help_text='Why it was rejected')

    class Meta:
        ordering = ['event', 'id']

    def __str__(self):
        return self.statement


class StockImage(models.Model):
    """A stock photo picked for an event's drafts (stage `images`), kept in MEDIA_ROOT until it is on the site."""

    external_id = models.CharField(max_length=100, unique=True, help_text='pixabay-<id>: never used for two events')
    source = models.CharField(max_length=20)
    file = models.CharField(max_length=300, help_text='Relative to MEDIA_ROOT')
    author = models.CharField(max_length=200)
    author_url = models.URLField(max_length=500, blank=True)
    page_url = models.URLField(max_length=500)
    query = models.CharField(max_length=200, help_text='The search that found it')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.external_id


class Draft(models.Model):
    """A news card for an event, after the checks. Published from here once the server side exists."""

    class Status(models.TextChoices):
        UNCHECKED = 'unchecked', 'Written, card check pending'
        READY = 'ready', 'Ready to publish'
        REVIEW = 'review', 'Needs a human'
        BLOCKED = 'blocked', 'Not publishable'

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='drafts')
    headline = models.CharField(max_length=300)
    summary = models.TextField()
    body = models.TextField(help_text='After the card check: failed sentences cut')
    written_body = models.TextField(help_text='As the writer wrote it')
    image_queries = models.JSONField(default=list, blank=True,
                                     help_text='Stock photo searches from the writer: abstract, no names or brands')
    image = models.ForeignKey(StockImage, null=True, blank=True, on_delete=models.SET_NULL, related_name='drafts')
    image_searched = models.BooleanField(default=False, help_text='Searched once: none found -> the category cover')
    status = models.CharField(max_length=20, choices=Status, db_index=True)
    reasons = models.JSONField(default=list, blank=True, help_text='Why it needs review or is blocked')
    checks = models.JSONField(default=list, blank=True, help_text='Card check: every sentence with its verdict')
    facts = models.ManyToManyField(Fact, related_name='drafts')
    writer_model = models.CharField(max_length=200)
    checker_model = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.headline


class PipelineRun(models.Model):
    """One `pipeline` command: what each stage did."""

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    stats = models.JSONField(default=dict)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']
