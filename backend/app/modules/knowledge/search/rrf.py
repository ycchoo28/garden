"""Reciprocal rank fusion. Pure: merges ranked lists of IDs into one ranking."""

from collections.abc import Hashable, Sequence

RRF_K = 60


def rrf_merge[T: Hashable](
    rankings: Sequence[Sequence[T]], k: int = RRF_K
) -> list[tuple[T, float]]:
    """Score each ID by the sum of 1 / (k + rank) over the lists it appears in, best first.

    Ranks start at 1. Ties keep the order in which the IDs were first seen, so the result is stable.
    """
    scores: dict[T, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: -pair[1])
