"""Candidate events for labeling: articles from different sources that look like one story.

Embeddings (nomic-embed via LM Studio) + cosine similarity + a time window, joined into groups.
Only a helper for building the test set by hand: a human checks every group.
"""
from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from pipeline.steps.grouping import embedding_text
from sources.models import Article



@dataclass
class Group:
    articles: list[Article]
    similarity: float  # lowest pairwise similarity inside the group

    @property
    def sources(self) -> set[str]:
        return {article.source.slug for article in self.articles}


def candidate_articles():
    return (Article.objects.exclude(status=Article.Status.DUPLICATE)
            .filter(eval_events=None).select_related('source').order_by('published_at'))


def embed_articles(articles: list[Article], embed) -> np.ndarray:
    texts = [embedding_text(article) for article in articles]
    vectors = np.array(embed(texts), dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def find_groups(articles: list[Article], vectors: np.ndarray, threshold: float, window: timedelta,
                min_sources: int = 2, max_size: int = 6) -> list[Group]:
    """Greedy: the closest pairs first; a group takes an article only if it is similar to every member
    (complete linkage), so one loose link cannot chain two stories together."""
    similarity = vectors @ vectors.T
    n = len(articles)
    pairs = [
        (similarity[i, j], i, j) for i in range(n) for j in range(i + 1, n)
        if similarity[i, j] >= threshold and _close_in_time(articles[i], articles[j], window)
    ]
    group_of: dict[int, list[int]] = {}
    for _, i, j in sorted(pairs, reverse=True):
        a, b = group_of.get(i, [i]), group_of.get(j, [j])
        if a is b or len(a) + len(b) > max_size:
            continue
        if all(similarity[x, y] >= threshold for x in a for y in b):
            merged = a + b
            for member in merged:
                group_of[member] = merged

    unique = {id(members): members for members in group_of.values()}.values()
    groups = []
    for members in unique:
        lowest = min(similarity[x, y] for x in members for y in members if x < y)
        group = Group([articles[m] for m in sorted(members)], float(lowest))
        if len(group.sources) >= min_sources:
            groups.append(group)
    return sorted(groups, key=lambda g: (len(g.sources), g.similarity), reverse=True)


def _close_in_time(a: Article, b: Article, window: timedelta) -> bool:
    if not a.published_at or not b.published_at:
        return True
    return abs(a.published_at - b.published_at) <= window
