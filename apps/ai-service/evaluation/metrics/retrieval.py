def recall_at_k(relevant_ids: set[str], retrieved_ids: list[str], k: int) -> float:
    if not relevant_ids:
        return 0.0
    return len(relevant_ids.intersection(retrieved_ids[:k])) / len(relevant_ids)


def reciprocal_rank(relevant_ids: set[str], retrieved_ids: list[str]) -> float:
    for rank, item_id in enumerate(retrieved_ids, start=1):
        if item_id in relevant_ids:
            return 1.0 / rank
    return 0.0
