"""HTTP client for the standalone, stateless AI runtime."""

from functools import lru_cache
import json
from collections.abc import Callable
from typing import Any, Iterator

import httpx

from app.core.config import settings


class AIServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AIServiceClient:
    def __init__(self) -> None:
        self.base_url = (settings.AI_SERVICE_URL or "").rstrip("/")
        self.timeout = settings.AI_SERVICE_TIMEOUT_SECONDS

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    @property
    def headers(self) -> dict[str, str]:
        if not settings.AI_SERVICE_INTERNAL_TOKEN:
            # Sending no credential used to work because the AI service let unauthenticated
            # requests through. It no longer does, and a silent empty header would surface
            # as an opaque 503 from the far side instead of naming the missing setting.
            raise AIServiceError(
                "AI_SERVICE_INTERNAL_TOKEN is not configured; the AI service will reject the call"
            )
        return {"X-AI-Service-Key": settings.AI_SERVICE_INTERNAL_TOKEN}

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise AIServiceError("AI service URL is not configured")
        try:
            headers = {**self.headers, **(extra_headers or {})}
            response = httpx.post(
                f"{self.base_url}{path}",
                json=payload,
                headers=headers,
                timeout=timeout if timeout is not None else self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise AIServiceError(
                f"AI service request failed: {path}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.RequestError as exc:
            raise AIServiceError(f"AI service request failed: {path}") from exc

    def _stream_post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        if not self.enabled:
            raise AIServiceError("AI service URL is not configured")
        headers = {
            **self.headers,
            **(extra_headers or {}),
            "Accept": "text/event-stream",
            "Accept-Encoding": "identity",
        }
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}{path}",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            ) as response:
                response.raise_for_status()
                event_name = "message"
                data_lines: list[str] = []
                for line in response.iter_lines():
                    if not line:
                        if data_lines:
                            data = json.loads("\n".join(data_lines))
                            yield {"event": event_name, **data}
                        event_name, data_lines = "message", []
                    elif line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                if data_lines:
                    data = json.loads("\n".join(data_lines))
                    yield {"event": event_name, **data}
        except httpx.HTTPStatusError as exc:
            raise AIServiceError(
                f"AI service request failed: {path}",
                status_code=exc.response.status_code,
            ) from exc
        except (httpx.RequestError, json.JSONDecodeError) as exc:
            raise AIServiceError(f"AI service stream failed: {path}") from exc

    def chunk_document(
        self,
        content: str,
        *,
        chunk_size: int,
        chunk_overlap: int,
        on_progress: Callable[[dict[str, int]], None] | None = None,
        progress_stream_id: str | None = None,
        progress_completed_before: int = 0,
        progress_total_count: int | None = None,
        progress_chunks_before: int = 0,
    ) -> list[dict[str, Any]]:
        if on_progress is not None:
            chunks: list[dict[str, Any]] = []
            for event in self._stream_post("/v1/rag/chunk/stream", {
                "content": content,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "progress_stream_id": progress_stream_id,
                "progress_completed_before": progress_completed_before,
                "progress_total_count": progress_total_count,
                "progress_chunks_before": progress_chunks_before,
            }):
                if event["event"] != "progress":
                    continue
                chunks.extend(event.get("chunks", []))
                on_progress({
                    "processed_segments": int(event["processed_segments"]),
                    "total_segments": int(event["total_segments"]),
                    "remaining_segments": int(event["remaining_segments"]),
                    "chunks_created": int(event["chunks_created"]),
                })
            return chunks
        result = self._post("/v1/rag/chunk", {
            "content": content,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        })
        return list(result["chunks"])

    def embed(
        self,
        texts: list[str],
        *,
        input_type: str = "document",
        completed_before: int = 0,
        total_count: int | None = None,
        progress_stream_id: str | None = None,
    ) -> dict[str, Any]:
        return self._post("/v1/embeddings", {
            "texts": texts,
            "input_type": input_type,
            "completed_before": completed_before,
            "total_count": total_count or len(texts),
            "progress_stream_id": progress_stream_id,
        })

    def count_tokens(self, texts: list[str]) -> dict[str, Any]:
        return self._post("/v1/token-count", {"texts": texts})

    def rerank(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        top_k: int,
    ) -> list[dict[str, Any]]:
        result = self._post("/v1/rag/rerank", {
            "query": query,
            "candidates": candidates,
            "top_k": top_k,
        })
        return list(result["results"])

    def route_agent(
        self,
        message: str,
        *,
        requested_role: str | None = None,
    ) -> dict[str, Any]:
        return self._post("/v1/agents/route", {
            "message": message,
            "requested_role": requested_role,
        })

    def generate_text(
        self,
        messages: list[dict[str, str]],
        *,
        provider: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        return self._post("/v1/llm/generate", {
            "messages": messages,
            "provider": provider,
            "model": model,
        }, timeout=timeout)

    def run_orchestration(
        self,
        payload: dict[str, Any],
        *,
        internal_tool_jwt: str,
    ) -> dict[str, Any]:
        return self._post(
            "/v1/orchestration/run",
            payload,
            extra_headers={"X-Internal-Tool-Authorization": f"Bearer {internal_tool_jwt}"},
        )

    def stream_orchestration(
        self,
        payload: dict[str, Any],
        *,
        internal_tool_jwt: str,
    ) -> Iterator[dict[str, Any]]:
        return self._stream_post(
            "/v1/orchestration/run/stream",
            payload,
            extra_headers={"X-Internal-Tool-Authorization": f"Bearer {internal_tool_jwt}"},
        )

    def resume_orchestration(
        self,
        payload: dict[str, Any],
        *,
        internal_tool_jwt: str,
    ) -> dict[str, Any]:
        return self._post(
            "/v1/orchestration/resume",
            payload,
            extra_headers={"X-Internal-Tool-Authorization": f"Bearer {internal_tool_jwt}"},
        )


@lru_cache(maxsize=1)
def get_ai_service_client() -> AIServiceClient:
    return AIServiceClient()
