from dataclasses import dataclass


@dataclass(frozen=True)
class BaseAgent:
    role: str
    name: str
    description: str
    capabilities: tuple[str, ...]

    def can(self, capability: str) -> bool:
        return capability in self.capabilities
