"""Cards of a write eval run (WRITE_RUN, default 17) and pipeline drafts -> mockups/cards.js (window.CARDS).

uv run manage.py shell -c "exec(open('mockups/export_cards.py', encoding='utf-8').read())"
"""
import json
import os
from pathlib import Path

from evaluation.models import EvalResult
from pipeline.models import Draft
from verification.card_check import paragraphs, split_sentences


def body(text):
    """Paragraphs; an old one-paragraph card is cut into pairs of sentences."""
    parts = paragraphs(text)
    if len(parts) > 1:
        return parts
    sentences = split_sentences(text)
    return [' '.join(sentences[i:i + 2]) for i in range(0, len(sentences), 2)]


def sources(articles):
    seen, out = set(), []
    for a in sorted(articles, key=lambda a: a.published_at or a.fetched_at):
        if a.source.name in seen:
            continue
        seen.add(a.source.name)
        out.append({'name': a.source.name, 'title': a.title, 'url': a.canonical_url})
    return out


def published(articles):
    dates = [a.published_at for a in articles if a.published_at]
    return min(dates).isoformat() if dates else None


RUN = int(os.environ.get('WRITE_RUN', 17))
cards = []
for r in EvalResult.objects.filter(run_id=RUN, error='').select_related('event'):
    articles = list(r.event.articles.select_related('source'))
    card = r.output['answer']
    cards.append({'id': f'e{r.event_id}', 'category': r.event.category, 'headline': card['headline'],
                  'summary': card['summary'], 'body': body(card['body']),
                  'published': published(articles), 'sources': sources(articles)})
for d in Draft.objects.select_related('event'):
    articles = list(d.event.articles.select_related('source'))
    cards.append({'id': f'd{d.pk}', 'category': d.event.category, 'headline': d.headline, 'summary': d.summary,
                  'body': body(d.body), 'published': published(articles), 'sources': sources(articles)})

out = Path('mockups'); out.mkdir(exist_ok=True)
(out / 'cards.js').write_text('window.CARDS = ' + json.dumps(cards, ensure_ascii=False, indent=1) + ';\n',
                              encoding='utf-8')
print(len(cards), 'cards')
