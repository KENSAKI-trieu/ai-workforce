"""Tests for POST /api/v1/auth/change-password and the shared password policy.

Before this endpoint existed there was no way to change a password at all, which also
meant the session-revocation machinery had no caller: a suspected leak could only be
answered by deactivating the account.
"""

import pytest

from app.core.password_policy import MIN_PASSWORD_LENGTH, validate_password

ORIGINAL = "Password123!"
REPLACEMENT = "Replacement456$"


@pytest.fixture
def restore_password(client):
    """Put the shared seed account back the way every other module expects it."""
    yield
    client.cookies.clear()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@company.com", "password": REPLACEMENT},
    )
    if login.status_code == 200:
        client.post(
            "/api/v1/auth/change-password",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
            json={"current_password": REPLACEMENT, "new_password": ORIGINAL},
        )


def _login(client, password=ORIGINAL):
    client.cookies.clear()
    return client.post(
        "/api/v1/auth/login",
        json={"email": "admin@company.com", "password": password},
    )


def test_change_password_requires_authentication(client):
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": ORIGINAL, "new_password": REPLACEMENT},
    )
    assert response.status_code == 401


def test_change_password_rejects_a_wrong_current_password(client):
    login = _login(client)
    response = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"current_password": "NotThePassword!", "new_password": REPLACEMENT},
    )
    assert response.status_code == 401


def test_change_password_rejects_reusing_the_same_password(client):
    login = _login(client)
    response = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"current_password": ORIGINAL, "new_password": ORIGINAL},
    )
    assert response.status_code == 400


def test_change_password_rejects_a_password_below_the_policy(client):
    login = _login(client)
    response = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"current_password": ORIGINAL, "new_password": "Short1!"},
    )
    assert response.status_code == 422


def test_changing_the_password_revokes_other_sessions(client, restore_password):
    """The whole point: a change must take the old sessions away."""
    other_device = _login(client)
    assert other_device.status_code == 200
    stale_refresh = other_device.cookies["refresh_token"]

    this_device = _login(client)
    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {this_device.json()['access_token']}"},
        json={"current_password": ORIGINAL, "new_password": REPLACEMENT},
    )
    assert changed.status_code == 200

    # The other device can no longer rotate its session.
    client.cookies.clear()
    client.cookies.set("refresh_token", stale_refresh)
    assert client.post("/api/v1/auth/refresh").status_code == 401

    # ...while the device that made the change keeps working.
    client.cookies.clear()
    client.cookies.set("refresh_token", changed.cookies["refresh_token"])
    assert client.post("/api/v1/auth/refresh").status_code == 200

    # And the new password is the one that works.
    assert _login(client, ORIGINAL).status_code == 401
    assert _login(client, REPLACEMENT).status_code == 200


def test_policy_rejects_short_passwords():
    with pytest.raises(ValueError, match=str(MIN_PASSWORD_LENGTH)):
        validate_password("Short1!")


def test_policy_rejects_a_notorious_password():
    with pytest.raises(ValueError, match="too common"):
        validate_password("Password1234")


def test_policy_accepts_the_suite_password():
    assert validate_password(ORIGINAL) == ORIGINAL


def test_register_enforces_the_password_policy(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "policy-check@example.com",
            "full_name": "Policy Check",
            "password": "Short1!",
            "tenant_name": "Policy Check Co",
        },
    )
    assert response.status_code == 422
