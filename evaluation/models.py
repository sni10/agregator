"""Stage 0 test set: real events labeled by hand, and the results of running candidate models on them."""
from django.db import models

from sources.models import Article, Category


class EvalEvent(models.Model):
    """One real event and the collected articles that report it (the gold grouping)."""

    title = models.CharField(max_length=300)
    category = models.CharField(max_length=10, choices=Category)
    articles = models.ManyToManyField(Article, related_name='eval_events')
    high_risk = models.BooleanField(default=False, help_text='Hack, accusation, regulation, crypto prices')
    notes = models.TextField(blank=True, help_text='Why this case is tricky: reprinted press release, conflicts...')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'eval event'

    def __str__(self):
        return self.title


class EvalFact(models.Model):
    """A key fact of the event that a good extraction must find."""

    class Kind(models.TextChoices):
        NUMBER = 'number', 'Number / percent / amount'
        DATE = 'date', 'Date'
        VERSION = 'version', 'Version'
        PRICE = 'price', 'Crypto price'
        QUOTE = 'quote', 'Quote'
        GENERAL = 'general', 'Other statement'

    event = models.ForeignKey(EvalEvent, on_delete=models.CASCADE, related_name='facts')
    statement = models.TextField()
    kind = models.CharField(max_length=10, choices=Kind, default=Kind.GENERAL)

    def __str__(self):
        return self.statement[:80]


class EvalRun(models.Model):
    """One task run with one model over the test set."""

    task = models.CharField(max_length=50)
    model_id = models.CharField(max_length=200)
    params = models.JSONField(default=dict, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f'{self.task} / {self.model_id} / {self.started_at:%Y-%m-%d %H:%M}'


class EvalResult(models.Model):
    """One model answer inside a run."""

    run = models.ForeignKey(EvalRun, on_delete=models.CASCADE, related_name='results')
    event = models.ForeignKey(EvalEvent, on_delete=models.SET_NULL, null=True, blank=True)
    article = models.ForeignKey(Article, on_delete=models.SET_NULL, null=True, blank=True)
    output = models.JSONField(null=True, blank=True)
    raw = models.TextField(blank=True)
    correct = models.BooleanField(null=True, help_text='Empty = not scored automatically')
    manual_score = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=[(n, n) for n in range(1, 6)], help_text='Human 1-5: English, accuracy, no filler',
    )
    error = models.TextField(blank=True)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    reasoning_tokens = models.PositiveIntegerField(default=0)
    seconds = models.FloatField(default=0)

    class Meta:
        ordering = ['id']


class WrittenCard(EvalResult):
    """Answers of the write task, as cards to read in the admin (Evaluation -> Cards)."""

    class Meta:
        proxy = True
        verbose_name = 'card'
        ordering = ['-run_id', 'event_id']
