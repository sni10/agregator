from datetime import UTC, datetime, timedelta

import numpy as np
from django.test import TestCase

from evaluation.services.suggest import find_groups
from sources.models import Article, Category, Source

DAY = datetime(2026, 10, 22, tzinfo=UTC)


class FindGroupsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sources = [Source.objects.create(name=f'S{n}', slug=f's{n}', feed_url=f'https://example.com/{n}',
                                             category=Category.CRYPTO) for n in range(3)]

    def articles(self, *specs):
        """specs: (source index, days after DAY)"""
        start = Article.objects.count()
        return [Article.objects.create(source=self.sources[source], canonical_url=f'https://example.com/a{n}',
                                       title=f'A{n}', content_hash=f'h{n}', published_at=DAY + timedelta(days=days))
                for n, (source, days) in enumerate(specs, start)]

    @staticmethod
    def vectors(*rows):
        vectors = np.array(rows, dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)

    def test_groups_similar_articles_from_different_sources(self):
        articles = self.articles((0, 0), (1, 0), (2, 0))
        vectors = self.vectors([1, 0], [0.99, 0.1], [0, 1])

        groups = find_groups(articles, vectors, threshold=0.9, window=timedelta(days=3))

        self.assertEqual([g.articles for g in groups], [articles[:2]])

    def test_needs_two_sources_and_the_time_window(self):
        same_source = self.articles((0, 0), (0, 0))
        self.assertEqual(find_groups(same_source, self.vectors([1, 0], [1, 0]), 0.9, timedelta(days=3)), [])

        far_apart = self.articles((1, 0), (2, 10))
        self.assertEqual(find_groups(far_apart, self.vectors([1, 0], [1, 0]), 0.9, timedelta(days=3)), [])

    def test_does_not_chain_two_stories_through_one_loose_link(self):
        # a~b and b~c, but a and c are not similar: complete linkage keeps them apart
        articles = self.articles((0, 0), (1, 0), (2, 0))
        vectors = self.vectors([1, 0], [0.92, 0.39], [0.6, 0.8])

        groups = find_groups(articles, vectors, threshold=0.9, window=timedelta(days=3))

        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].articles), 2)
