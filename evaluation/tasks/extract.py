from pipeline.steps.facts import EXTRACT_SYSTEM as SYSTEM
from pipeline.steps.facts import ExtractAnswer as Answer
from pipeline.steps.facts import ExtractedFact as Fact
from pipeline.steps.facts import TEXT_BUDGET
from verification.text_match import quote_in_text, same_statement, unsupported_numbers

from .base import Case, Score, Task, article_block, events_with_articles


class ExtractTask(Task):
    """One case per event with facts. Scored automatically:
    - every quote must be found in the cited article (else the fact is invented);
    - every number of a statement must be in the cited article;
    - recall: how many of the hand-written key facts were found (rough word/number match: check by eye too).
    """

    name = 'extract'
    schema = Answer
    max_tokens = 2048

    def cases(self):
        for event in events_with_articles():
            facts = [fact.statement for fact in event.facts.all()]
            articles = list(event.articles.all())
            if facts and articles:
                yield Case(key=f'event {event.pk}', expected=facts, event=event)

    def ask(self, case):
        articles = list(case.event.articles.all())
        per_article = TEXT_BUDGET // len(articles)
        user = '\n\n'.join(article_block(article, per_article) for article in articles)
        return self.complete_json(f'{SYSTEM}\nEvent: {case.event.title}', user, Answer)

    def score(self, case, completion):
        texts = {article.pk: f'{article.title}\n{article.body}' for article in case.event.articles.all()}
        checked = [self._check(fact, texts) for fact in completion.value.facts]
        invented = [c for c in checked if c['problems']]
        found = [gold for gold in case.expected
                 if any(same_statement(gold, fact.statement) for fact in completion.value.facts)]
        details = {
            'facts': len(checked),
            'invented': len(invented),
            'invented_facts': invented,
            'recall': round(len(found) / len(case.expected), 3),
            'missed': [gold for gold in case.expected if gold not in found],
        }
        return Score(not invented, details)

    @staticmethod
    def _check(fact: Fact, texts: dict[int, str]) -> dict:
        text = texts.get(fact.article_id)
        problems = []
        if text is None:
            problems.append(f'unknown article_id {fact.article_id}')
        else:
            if not quote_in_text(fact.quote, text):
                problems.append('quote not in article')
            if missing := unsupported_numbers(fact.statement, text):
                problems.append(f'numbers not in article: {sorted(missing)}')
        return {'statement': fact.statement, 'quote': fact.quote, 'problems': problems}

    def summarize(self, results):
        answered = [r for r in results if not r.error]
        facts = sum(r.output['facts'] for r in answered)
        invented = sum(r.output['invented'] for r in answered)
        return {
            'facts': facts,
            # the main stage 0 metric: share of extracted facts with an invented quote or number
            'invented_rate': round(invented / facts, 3) if facts else None,
            'avg_recall': round(sum(r.output['recall'] for r in answered) / len(answered), 3) if answered else None,
        }
