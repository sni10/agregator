from pipeline.steps.write import CardAnswer as Answer
from pipeline.steps.write import WRITE_SYSTEM as SYSTEM
from pipeline.steps.write import write_prompt
from verification.text_match import rounded_numbers, unsupported_numbers

from .base import Case, Score, Task, events_with_articles


class WriteTask(Task):
    """The hand-written facts of each event -> a news card. Scored automatically only for invented numbers;
    quality (English, accuracy, no filler) is a human 1-5 score in the admin (Eval results -> manual score)."""

    name = 'write'
    schema = Answer
    role = 'writer'
    max_tokens = 2048

    def cases(self):
        for event in events_with_articles():
            facts = [fact.statement for fact in event.facts.all()]
            if facts:
                articles = event.articles.all()
                sources = [f'{a.source.name}: {a.title}' for a in articles]
                sites = len({a.source_id for a in articles})
                yield Case(key=f'event {event.pk}', expected=facts, event=event,
                           data={'sources': sources, 'sites': sites})

    def ask(self, case):
        prompt = write_prompt(case.expected, case.data['sources'], case.data.get('sites'))
        return self.complete_json(SYSTEM, prompt, Answer)

    def score(self, case, completion):
        card = completion.value
        text, facts = f'{card.headline}\n{card.summary}\n{card.body}', '\n'.join(case.expected)
        rounded = rounded_numbers(text, facts)  # "€3.25M" for €3,252,618: fine in a headline
        invented = unsupported_numbers(text, facts) - rounded
        return Score(None if not invented else False, {
            'invented_numbers': sorted(invented),
            'rounded_numbers': sorted(rounded),
            'words': len(card.body.split()),
        })

    def summarize(self, results):
        answered = [r for r in results if not r.error]
        return {'cards_with_invented_numbers': sum(bool(r.output['invented_numbers']) for r in answered)}
