import pytest

from app.main import app, require_internal_token


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
