import pytest

from app.core.config import settings
from app.main import app, require_internal_token


@pytest.fixture(autouse=True)
def bedrock_enabled(request: pytest.FixtureRequest):
    """Run the whole suite with Bedrock switched on.

    The flag defaulting to off meant nothing exercised the provider, and the first
    thing it hit in production would have been a ValueError raised while the router
    was still being built -- taking every vendor down with it, not just Bedrock.
    A test that is about the flag itself marks `bedrock_flag` and sets its own value.
    """
    if request.node.get_closest_marker("bedrock_flag"):
        yield
        return
    original = settings.BEDROCK_ENABLED
    settings.BEDROCK_ENABLED = True
    try:
        yield
    finally:
        settings.BEDROCK_ENABLED = original


@pytest.fixture(autouse=True)
def no_backend_tool_contracts(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Answer the graph's contract lookup with an empty toolset instead of a live backend.

    Every orchestration run asks the backend which tools it may use. Tests that drive the
    endpoints are about the graph, not that lookup; the ones marked `tool_contracts`
    exercise the lookup itself.
    """
    if not request.node.get_closest_marker("tool_contracts"):
        monkeypatch.setattr("app.tools.gateway.ToolGatewayClient.list_tools", lambda self: [])
    yield


@pytest.fixture(autouse=True)
def bypass_internal_token(request: pytest.FixtureRequest):
    """Supply the internal credential for every test that is not about the credential.

    The endpoints used to let unauthenticated requests through whenever
    AI_SERVICE_INTERNAL_TOKEN was unset, and the suite quietly relied on that. Now that
    a missing token closes the door, tests that care about payloads override the guard
    and the ones marked `internal_auth` exercise it for real.
    """
    if request.node.get_closest_marker("internal_auth"):
        yield
        return
    app.dependency_overrides[require_internal_token] = lambda: None
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_internal_token, None)
