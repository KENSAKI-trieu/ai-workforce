from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class LLMResult:
    content: str
    model: str
    provider: str
    usage: dict[str, int] = field(default_factory=dict)


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def generate(self, messages: list[dict[str, str]], *, model: str | None = None) -> LLMResult:
        raise NotImplementedError
