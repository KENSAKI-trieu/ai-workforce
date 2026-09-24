from abc import ABC, abstractmethod
from typing import Any


class BaseReranker(ABC):
    """Scores query/candidate pairs on a normalized 0..1 scale."""

    backend: str
    model_name: str

    @abstractmethod
    def score(
        self,
        query: str,
        documents: list[str],
        candidates: list[dict[str, Any]],
    ) -> list[float]:
        raise NotImplementedError
