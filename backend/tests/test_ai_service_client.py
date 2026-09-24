import httpx
import pytest

from app.core.config import settings
from app.clients.ai_service_client import AIServiceClient, AIServiceError


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://ai-service.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("contract failure", request=request, response=response)

    def json(self) -> dict:
        return self.payload


class _StreamResponse(_Response):
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        super().__init__({}, status_code=status_code)
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def iter_lines(self):
        return iter(self.lines)


def _client(monkeypatch) -> AIServiceClient:
    monkeypatch.setattr(settings, "AI_SERVICE_URL", "http://ai-service.test")
    monkeypatch.setattr(settings, "AI_SERVICE_INTERNAL_TOKEN", "internal-secret")
    return AIServiceClient()


def test_agent_route_client_contract(monkeypatch) -> None:
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return _Response({
            "role": "LEGAL",
            "agent_name": "Legal Agent",
            "capabilities": ["contract_review"],
        })

    monkeypatch.setattr(httpx, "post", fake_post)
    result = _client(monkeypatch).route_agent(
        "Rà soát hợp đồng",
        requested_role="LEGAL",
    )

    assert captured["url"].endswith("/v1/agents/route")
    assert captured["json"] == {
        "message": "Rà soát hợp đồng",
        "requested_role": "LEGAL",
    }
    assert captured["headers"] == {"X-AI-Service-Key": "internal-secret"}
    assert result["role"] == "LEGAL"


def test_llm_generate_client_contract(monkeypatch) -> None:
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, json=json)
        return _Response({
            "content": "ok",
            "provider": "local",
            "model": "local-deterministic",
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        })

    monkeypatch.setattr(httpx, "post", fake_post)
    result = _client(monkeypatch).generate_text(
        [{"role": "user", "content": "hello baseline"}],
        provider="local",
    )

    assert captured["url"].endswith("/v1/llm/generate")
    assert captured["json"]["provider"] == "local"
    assert captured["json"]["model"] is None
    assert result["usage"]["prompt_tokens"] == 2


def test_ai_service_http_failure_is_wrapped(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: _Response({}, status_code=503),
    )
    with pytest.raises(AIServiceError, match="/v1/token-count") as caught:
        _client(monkeypatch).count_tokens(["baseline"])
    assert caught.value.status_code == 503


def test_ai_service_client_preserves_acl_failure_status(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: _Response({}, status_code=403),
    )
    with pytest.raises(AIServiceError) as caught:
        _client(monkeypatch).run_orchestration({}, internal_tool_jwt="denied")
    assert caught.value.status_code == 403


def test_orchestration_client_sends_separate_tool_jwt(monkeypatch) -> None:
    captured = {}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers)
        return _Response({"status": "COMPLETED", "state": {}, "interrupts": []})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = _client(monkeypatch).run_orchestration(
        {"tenant_id": "tenant-1"},
        internal_tool_jwt="signed-user-context",
    )

    assert captured["url"].endswith("/v1/orchestration/run")
    assert captured["headers"] == {
        "X-AI-Service-Key": "internal-secret",
        "X-Internal-Tool-Authorization": "Bearer signed-user-context",
    }
    assert result["status"] == "COMPLETED"


def test_orchestration_stream_parses_sse_without_exposing_transport_fields(monkeypatch) -> None:
    captured = {}

    def fake_stream(method, url, *, json, headers, timeout):
        captured.update(method=method, url=url, json=json, headers=headers, timeout=timeout)
        return _StreamResponse([
            "event: status",
            'data: {"phase":"ANALYZING"}',
            "",
            "event: token",
            'data: {"delta":"Done"}',
            "",
            "event: result",
            'data: {"status":"COMPLETED","state":{},"interrupts":[]}',
            "",
        ])

    monkeypatch.setattr(httpx, "stream", fake_stream)
    events = list(_client(monkeypatch).stream_orchestration(
        {"tenant_id": "tenant-1"},
        internal_tool_jwt="signed-user-context",
    ))

    assert captured["url"].endswith("/v1/orchestration/run/stream")
    assert captured["headers"]["Accept"] == "text/event-stream"
    assert events == [
        {"event": "status", "phase": "ANALYZING"},
        {"event": "token", "delta": "Done"},
        {"event": "result", "status": "COMPLETED", "state": {}, "interrupts": []},
    ]


def test_chunk_client_forwards_each_ai_progress_response(monkeypatch) -> None:
    captured = {}

    def fake_stream(method, url, *, json, headers, timeout):
        captured.update(json=json)
        return _StreamResponse([
            "event: progress",
            'data: {"processed_segments":1,"total_segments":2,"remaining_segments":1,"chunks_created":1,"chunks":[{"content":"one"}]}',
            "",
            "event: progress",
            'data: {"processed_segments":2,"total_segments":2,"remaining_segments":0,"chunks_created":2,"chunks":[{"content":"two"}]}',
            "",
            "event: result",
            'data: {"processed_segments":2,"total_segments":2,"remaining_segments":0,"chunks_created":2}',
            "",
        ])

    monkeypatch.setattr(httpx, "stream", fake_stream)
    updates = []
    chunks = _client(monkeypatch).chunk_document(
        "content",
        chunk_size=100,
        chunk_overlap=10,
        on_progress=updates.append,
        progress_stream_id="browser-stream-contract",
    )

    assert chunks == [{"content": "one"}, {"content": "two"}]
    assert [update["remaining_segments"] for update in updates] == [1, 0]
    assert captured["json"]["progress_stream_id"] == "browser-stream-contract"


def test_llm_generate_accepts_a_per_call_timeout(monkeypatch) -> None:
    timeouts = []

    def fake_post(url, *, json, headers, timeout):
        timeouts.append(timeout)
        return _Response({"content": "ok", "provider": "local"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(settings, "AI_SERVICE_TIMEOUT_SECONDS", 120.0)
    client = _client(monkeypatch)

    client.generate_text([{"role": "user", "content": "hi"}], timeout=7.5)
    client.generate_text([{"role": "user", "content": "hi"}])

    # A router's short budget must not leak into ordinary generation calls.
    assert timeouts == [7.5, 120.0]
