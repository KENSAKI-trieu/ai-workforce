"""
Pytest configuration and shared fixtures for AI Workforce backend tests.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

os.environ.setdefault("POSTGRES_PASSWORD", "test-database-password")
os.environ.setdefault("SECRET_KEY", "test-signing-key-not-for-production")
os.environ.setdefault("KNOWLEDGE_STORAGE_PATH", "data/test-knowledge")
os.environ["EMBEDDING_BACKEND"] = "deterministic"
os.environ["EMBEDDING_LOCAL_FILES_ONLY"] = "false"
os.environ["AI_SERVICE_URL"] = ""
# The fixtures below authenticate with this deterministic test-only password.
# Override ambient values so developer shells and CI cannot make the suite flaky.
os.environ["SEED_DEFAULT_PASSWORD"] = "Password123!"

from app.main import app
from app.core.database import get_db, sync_engine
from app.db.init_db import init_db


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    """Ensure database schema and initial seed data are populated before each module runs."""
    init_db()


@pytest.fixture(scope="module")
def transactional_db_session():
    """Run each test module in a rollback-only outer transaction."""
    connection = sync_engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(scope="module")
def client(transactional_db_session):
    """FastAPI client whose writes are rolled back after the module."""

    def override_get_db():
        yield transactional_db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="module")
def ceo_token_headers(client):
    """Obtain JWT Auth Headers for CEO user."""
    res = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@company.com", "password": "Password123!"},
    )
    assert res.status_code == 200, f"CEO login failed: {res.text}"
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def employee_token_headers(client):
    """Obtain JWT Auth Headers for Employee user."""
    res = client.post(
        "/api/v1/auth/login",
        json={"email": "employee@company.com", "password": "Password123!"},
    )
    assert res.status_code == 200, f"Employee login failed: {res.text}"
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
