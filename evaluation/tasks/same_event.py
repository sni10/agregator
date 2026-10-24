import random
from itertools import combinations

from pipeline.steps.grouping import SAME_EVENT_SYSTEM as SYSTEM
from pipeline.steps.grouping import SameEventAnswer as Answer
from sources.models import Article

from .base import Case, Score, Task, article_block, events_with_articles


PAIRS_PER_EVENT = 6  # positives from one big event would otherwise dominate


class SameEventTask(Task):
    """Pairs from one event (expected true) and as many pairs from different events (expected false),
    different events of one category first: "two different OpenAI releases" is the case that matters."""

    name = 'same_event'
    schema = Answer
    max_tokens = 200

    def cases(self):
        rng = random.Random(0)  # the same pairs on every run: models are compared on identical input
        events = [(event, list(event.articles.all())) for event in events_with_articles()]

        positives = []
        for event, articles in events:
            pairs = list(combinations(articles, 2))
            positives += [(event, pair) for pair in rng.sample(pairs, min(PAIRS_PER_EVENT, len(pairs)))]

        same_category, other_category = [], []
        for (event, articles), (other, other_articles) in combinations(events, 2):
            if articles and other_articles:
                pair = (event, (rng.choice(articles), rng.choice(other_articles)))
                (same_category if event.category == other.category else other_category).append(pair)
        rng.shuffle(same_category)
        rng.shuffle(other_category)
        negatives = (same_category + other_category)[:len(positives)]

        for (event, (a, b)), expected in [(p, True) for p in positives] + [(n, False) for n in negatives]:
            yield Case(key=f'{a.pk}+{b.pk}', expected=expected, event=event, article=a, data={'pair': [a.pk, b.pk]})

    def ask(self, case):
        a, b = (Article.objects.select_related('source').get(pk=pk) for pk in case.data['pair'])
        return self.complete_json(SYSTEM, f'{article_block(a, 1500)}\n\n{article_block(b, 1500)}', Answer)

    def score(self, case, completion):
        return Score(completion.value.same_event == case.expected)

    def summarize(self, results):
        answered = [r for r in results if not r.error]
        said_same = [r for r in answered if r.output['answer']['same_event']]
        really_same = [r for r in answered if r.output['expected']]
        true_same = [r for r in said_same if r.output['expected']]
        return {
            # precision matters most: a false merge glues two different stories into one card
            'precision': round(len(true_same) / len(said_same), 3) if said_same else None,
            'recall': round(len(true_same) / len(really_same), 3) if really_same else None,
            'false_merges': len(said_same) - len(true_same),
        }
