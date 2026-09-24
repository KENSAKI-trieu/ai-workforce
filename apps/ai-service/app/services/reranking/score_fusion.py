from typing import Any


def reciprocal_rank_fusion(
    dense: list[dict[str, Any]],
    sparse: list[dict[str, Any]],
    *,
    rank_constant: int = 60,
) -> list[dict[str, Any]]:
    scores: dict[str, float] = {}
    items: dict[str, dict[str, Any]] = {}
    for result_set in (dense, sparse):
        for rank, item in enumerate(result_set, start=1):
            key = str(item.get("id") or item.get("content_hash"))
            scores[key] = scores.get(key, 0.0) + 1.0 / (rank_constant + rank)
            items[key] = {**items.get(key, {}), **item}
    maximum = max(scores.values(), default=1.0)
    return sorted(
        ({**items[key], "_rrf_score": score / maximum} for key, score in scores.items()),
        key=lambda item: item["_rrf_score"],
        reverse=True,
    )
