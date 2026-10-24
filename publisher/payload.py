"""Draft -> the site's publishing payload (agregator-backend, POST /publishing/publications)."""
from pipeline.models import Draft

STATUSES = {Draft.Status.READY: 'published', Draft.Status.REVIEW: 'review'}  # what the site does with it


def external_id(draft: Draft) -> str:
    return f'event-{draft.event_id}'


def build_payload(draft: Draft) -> dict:
    event = draft.event
    cut = sum(1 for check in draft.checks if check['problems'] and check['part'] == 'body')
    return {
        'external_id': external_id(draft),
        'category': event.category,
        'type': 'event',
        'status': STATUSES[draft.status],
        'title': draft.headline,
        'summary': draft.summary,
        'body_md': draft.body,
        'sources': sources(event),
        'event_at': event.first_published_at.isoformat() if event.first_published_at else None,
        'image_queries': draft.image_queries,
        'quality': {
            'status': draft.status,
            'verified_claims': draft.facts.count(),
            'removed_sentences': cut,
            'reasons': draft.reasons,
            'models': [model for model in (draft.writer_model, draft.checker_model) if model],
        },
    }


def sources(event) -> list[dict]:
    """Every article of the event, oldest first."""
    articles = sorted(event.articles.select_related('source'), key=lambda a: a.published_at or a.fetched_at)
    return [{'title': article.title[:500], 'url': article.canonical_url, 'publisher': article.source.name,
             'published_at': article.published_at.isoformat() if article.published_at else None}
            for article in articles]
