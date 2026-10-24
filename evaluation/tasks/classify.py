from collections import Counter

from pipeline.steps.classify import ARTICLE_SYSTEM as SYSTEM
from pipeline.steps.classify import ArticleAnswer as Answer

from .base import Case, Score, Task, article_block, events_with_articles


class ClassifyTask(Task):
    """Each article of the test set; the expected category is the category of its event."""

    name = 'classify'
    schema = Answer
    max_tokens = 64

    def cases(self):
        seen = set()
        for event in events_with_articles():
            for article in event.articles.all():
                if article.pk not in seen:
                    seen.add(article.pk)
                    yield Case(key=f'article {article.pk}', expected=event.category, event=event, article=article)

    def ask(self, case):
        return self.complete_json(SYSTEM, article_block(case.article, chars=2000), Answer)

    def score(self, case, completion):
        return Score(completion.value.category == case.expected)

    def summarize(self, results):
        mistakes = Counter(
            f'{r.output["expected"]} -> {r.output["answer"]["category"]}' for r in results if r.correct is False
        )
        return {'mistakes': dict(mistakes.most_common())}
