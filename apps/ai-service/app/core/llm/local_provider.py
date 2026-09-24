from app.core.llm.base import LLMProvider, LLMResult


class LocalProvider(LLMProvider):
    name = "local"

    def generate(self, messages: list[dict[str, str]], *, model: str | None = None) -> LLMResult:
        last_user_message = next(
            (item["content"] for item in reversed(messages) if item["role"] == "user"),
            "",
        )
        return LLMResult(
            content=f"Local provider received: {last_user_message}",
            model=model or "local-deterministic",
            provider=self.name,
            usage={"prompt_tokens": sum(len(item["content"].split()) for item in messages), "completion_tokens": 4},
        )
