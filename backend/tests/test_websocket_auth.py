"""The execution stream must not open for anyone who guesses a thread id.

The endpoint used to call `websocket.accept()` as its first statement. It currently emits
placeholder events, so nothing leaked yet -- but it is the socket a real LangGraph run
would be wired into, and by then the mistake would be conversation content.
"""

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.security import create_access_token


# The ws router carries prefix "/ws/v1" and is mounted under the "/api/v1" api_router,
# so the real path is the doubled-up one below. Spelling it wrong makes every refusal
# test pass for the wrong reason -- a missing route also refuses the connection.
WS_PATH = "/api/v1/ws/v1/execution"


def _connect(client, thread_id: str, token: str | None):
    url = f"{WS_PATH}/{thread_id}"
    if token:
        url = f"{url}?token={token}"
    return client.websocket_connect(url)


def _seed_workflow(session, user, thread_id: str):
    from app.models.models import AgentWorkflow

    workflow = AgentWorkflow(
        tenant_id=user.tenant_id,
        initiator_id=user.id,
        title="WebSocket auth fixture",
        thread_id=thread_id,
    )
    session.add(workflow)
    session.flush()
    return workflow


def _admin(session):
    from app.models.models import User

    user = session.query(User).filter(User.email == "admin@company.com").first()
    assert user is not None
    return user


def test_a_valid_token_opens_the_owning_tenants_thread(client, transactional_db_session):
    """The guard must still let the intended caller through."""
    user = _admin(transactional_db_session)
    thread_id = "ws-auth-happy-path-thread"
    _seed_workflow(transactional_db_session, user, thread_id)
    token = create_access_token(
        subject=user.id, role=user.role, tenant_id=user.tenant_id
    )

    with _connect(client, thread_id, token) as ws:
        first = ws.receive_json()
        assert first["event"] == "NODE_TRANSITION"
        assert first["thread_id"] == thread_id


def test_connection_without_a_token_is_refused(client):
    with pytest.raises(WebSocketDisconnect):
        with _connect(client, "any-thread", None):
            pass


def test_connection_with_a_garbage_token_is_refused(client):
    with pytest.raises(WebSocketDisconnect):
        with _connect(client, "any-thread", "not-a-jwt"):
            pass


def test_a_refresh_token_does_not_open_the_stream(client):
    """Only an access token authenticates; a refresh token is for /auth/refresh alone."""
    client.cookies.clear()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@company.com", "password": "Password123!"},
    )
    assert login.status_code == 200

    with pytest.raises(WebSocketDisconnect):
        with _connect(client, "any-thread", login.cookies["refresh_token"]):
            pass


def test_a_valid_token_still_cannot_open_an_unrelated_thread(
    client, transactional_db_session
):
    """Authentication is not authorisation: the thread must belong to the caller's tenant."""
    from app.models.models import User

    user = (
        transactional_db_session.query(User)
        .filter(User.email == "admin@company.com")
        .first()
    )
    assert user is not None
    token = create_access_token(
        subject=user.id, role=user.role, tenant_id=user.tenant_id
    )
    with pytest.raises(WebSocketDisconnect):
        with _connect(client, "a-thread-that-belongs-to-nobody", token):
            pass
