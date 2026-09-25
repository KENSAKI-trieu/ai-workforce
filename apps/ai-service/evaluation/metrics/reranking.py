import math


def reciprocal_rank(ranked_ids: list[str], relevant_ids: set[str]) -> float:
    for rank, item_id in enumerate(ranked_ids, start=1):
        if item_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked_ids: list[str], relevance: dict[str, float], k: int) -> float:
    if k <= 0:
        return 0.0

    def dcg(ids: list[str]) -> float:
        return sum(
            (2 ** relevance.get(item_id, 0.0) - 1.0) / math.log2(rank + 2)
            for rank, item_id in enumerate(ids[:k])
        )

    actual = dcg(ranked_ids)
    ideal_relevance = sorted(relevance.values(), reverse=True)[:k]
    ideal = sum(
        (2 ** score - 1.0) / math.log2(rank + 2)
        for rank, score in enumerate(ideal_relevance)
    )
    return actual / ideal if ideal else 0.0
