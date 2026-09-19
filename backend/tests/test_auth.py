"""
Tests for Authentication & User endpoints (JWT login, current user profile).
"""

import time


def _login(client):
    """Log in from a clean cookie jar.

    The `client` fixture is module-scoped, so a cookie set by an earlier test would
    otherwise ride along and make these assertions meaningless -- a refresh could pass
    on a stale jar cookie rather than the one the test meant to send.
    """
    client.cookies.clear()
    return client.post(
        "/api/v1/auth/login",
        json={"email": "admin@company.com", "password": "Password123!"},
    )


def _refresh_with(client, token: str):
    """Send exactly one refresh cookie: the one named here."""
    client.cookies.clear()
    client.cookies.set("refresh_token", token)
    return client.post("/api/v1/auth/refresh")


def test_login_success(client):
    """Test login with valid user credentials."""
    response = _login(client)
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "admin@company.com"
    assert data["user"]["role"] == "CEO"


def test_login_never_returns_the_refresh_token_in_the_body(client):
    """The refresh token must exist only as an HttpOnly cookie.

    It used to be in the response body as well, and the frontend put it in localStorage,
    which made the HttpOnly cookie decorative: any XSS could read a 30-day credential.
    """
    response = _login(client)
    assert response.status_code == 200
    assert "refresh_token" not in response.json()
    assert "refresh_token" in response.cookies

    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Path=/api/v1/auth" in set_cookie


def test_login_invalid_password(client):
    """Test login with incorrect password."""
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@company.com", "password": "WrongPassword!"},
    )
    assert response.status_code == 401
    assert "detail" in response.json()


def test_login_with_unknown_email_is_not_measurably_faster(client):
    """An unknown address must not answer sooner than a known one.

    Skipping bcrypt when there is no such user leaks which addresses have accounts.
    """

    def elapsed(email: str) -> float:
        started = time.perf_counter()
        client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "WrongPassword!"},
        )
        return time.perf_counter() - started

    # Warm the cached dummy hash so the first call does not pay for generating it.
    elapsed("nobody@company.com")

    unknown = min(elapsed("nobody@company.com") for _ in range(3))
    known = min(elapsed("admin@company.com") for _ in range(3))
    # bcrypt dominates both; a missing hash would make `unknown` an order of magnitude
    # faster, so anything within 2x means the oracle is closed.
    assert unknown > known / 2


def test_get_current_user_profile(client, ceo_token_headers):
    """Test fetching current authenticated user profile."""
    response = client.get("/api/v1/users/me", headers=ceo_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "admin@company.com"
    assert data["role"] == "CEO"
    assert data["department"] == "BOARD"


def test_refresh_token_via_cookie(client):
    """Test refreshing access token using the HttpOnly refresh_token cookie."""
    login_resp = _login(client)
    assert login_resp.status_code == 200
    assert "refresh_token" in login_resp.cookies

    refresh_resp = _refresh_with(client, login_resp.cookies["refresh_token"])
    assert refresh_resp.status_code == 200
    refresh_data = refresh_resp.json()
    assert "access_token" in refresh_data
    assert "refresh_token" not in refresh_data
    assert refresh_data["user"]["email"] == "admin@company.com"


def test_refresh_rejects_a_token_sent_in_the_json_body(client):
    """The JSON-body fallback is gone; only the cookie is accepted."""
    login_resp = _login(client)
    assert login_resp.status_code == 200
    token = login_resp.cookies["refresh_token"]

    # No cookie at all -- the token is offered the way the old fallback accepted it.
    client.cookies.clear()
    refresh_resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": token},
    )
    assert refresh_resp.status_code == 401


def test_refresh_token_invalid(client):
    """Test refreshing access token with an invalid refresh token."""
    assert _refresh_with(client, "invalid_fake_token_string").status_code == 401


def test_reusing_a_rotated_refresh_token_revokes_the_whole_family(client):
    """Replay means two parties hold the credential, so every session from that login dies."""
    login_resp = _login(client)
    original = login_resp.cookies["refresh_token"]

    first = _refresh_with(client, original)
    assert first.status_code == 200
    rotated = first.cookies["refresh_token"]
    assert rotated != original

    # The superseded token must not work a second time.
    assert _refresh_with(client, original).status_code == 401

    # ...and the token issued from it is revoked too, so the thief cannot keep rotating.
    assert _refresh_with(client, rotated).status_code == 401


def test_logout_revokes_the_refresh_token_server_side(client):
    """Logout used to clear a cookie and leave the token valid for another 30 days."""
    login_resp = _login(client)
    refresh_cookie = login_resp.cookies["refresh_token"]

    client.cookies.clear()
    client.cookies.set("refresh_token", refresh_cookie)
    assert client.post("/api/v1/auth/logout").status_code == 200

    assert _refresh_with(client, refresh_cookie).status_code == 401


def test_refresh_requires_a_cookie(client):
    client.cookies.clear()
    assert client.post("/api/v1/auth/refresh").status_code == 401
