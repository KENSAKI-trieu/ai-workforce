"""Contract reviews and chat messages are sealed at rest once a key is configured."""

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.core import encryption
from app.core.config import settings
from app.core.encryption import EncryptionKeyMissing, is_sealed, seal, unseal
from app.db import encrypt_existing_content
from app.models.models import AIAgent, ChatConversation, ChatMessage, ContractReview, User

KEY = Fernet.generate_key().decode()
OLD_KEY = Fernet.generate_key().decode()
CONTRACT = "HỢP ĐỒNG LAO ĐỘNG\nNgười lao động: Lê Thị Mai\nĐiều 1. Lương 6.500.000 đồng."


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", KEY)
    return KEY


def test_a_value_round_trips_and_is_not_readable_sealed(key):
    sealed = seal(CONTRACT)
    assert is_sealed(sealed) and "Lê Thị Mai" not in sealed
    assert unseal(sealed) == CONTRACT
    assert seal(sealed) == sealed  # never sealed twice


def test_without_a_key_values_are_stored_as_before(monkeypatch):
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", "")
    assert seal(CONTRACT) == CONTRACT
    assert unseal(CONTRACT) == CONTRACT


def test_a_sealed_value_without_its_key_is_an_error_not_garbage(monkeypatch):
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", KEY)
    sealed = seal(CONTRACT)
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", "")
    with pytest.raises(EncryptionKeyMissing):
        unseal(sealed)
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(EncryptionKeyMissing):
        unseal(sealed)


def test_a_rotated_key_still_opens_what_the_old_one_sealed(monkeypatch):
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", OLD_KEY)
    sealed_old = seal(CONTRACT)
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", f"{KEY},{OLD_KEY}")
    assert unseal(sealed_old) == CONTRACT
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", KEY)
    assert unseal(seal(CONTRACT)) == CONTRACT


def _message(db, content):
    user = db.query(User).filter(User.email == "employee@company.com").one()
    agent = db.query(AIAgent).filter(AIAgent.tenant_id == user.tenant_id, AIAgent.role_code == "LEGAL").one()
    conversation = ChatConversation(tenant_id=user.tenant_id, user_id=user.id, ai_agent_id=agent.id, title="enc")
    db.add(conversation)
    db.flush()
    message = ChatMessage(conversation_id=conversation.id, sender="USER", content=content)
    db.add(message)
    db.flush()
    return user, message


def _review(db, user, contract_text):
    review = ContractReview(
        tenant_id=user.tenant_id, created_by_id=user.id, source="CHAT", document_name="x",
        content_hash="h" * 64, idempotency_key=f"enc-test-{abs(hash(contract_text))}",
        represented_party="PARTY_A", contract_type="EMPLOYMENT_CONTRACT", review_version="3.0",
        risk_score=70, risk_level="HIGH", total_findings=1, contract_text=contract_text,
        result={"clauses": [{"text": contract_text}], "risk_score": 70}, status="OPEN",
    )
    db.add(review)
    db.flush()
    return review


def test_the_database_holds_only_ciphertext_and_the_app_reads_plain(transactional_db_session, key):
    db = transactional_db_session
    user, message = _message(db, CONTRACT)
    review = _review(db, user, CONTRACT)

    stored_message = db.execute(text("select content from chat_messages where id = :i"), {"i": message.id}).scalar()
    stored_text, result_type = db.execute(
        text("select contract_text, jsonb_typeof(result) from contract_reviews where id = :i"), {"i": review.id}
    ).one()
    assert is_sealed(stored_message) and is_sealed(stored_text) and result_type == "string"
    assert "Lê Thị Mai" not in stored_message + stored_text

    db.expire_all()
    assert db.get(ChatMessage, message.id).content == CONTRACT
    reread = db.get(ContractReview, review.id)
    assert reread.contract_text == CONTRACT
    assert reread.result == {"clauses": [{"text": CONTRACT}], "risk_score": 70}


def test_rows_written_before_the_key_stay_readable_and_can_be_sealed(transactional_db_session, monkeypatch):
    db = transactional_db_session
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", "")
    user, message = _message(db, "tin nhắn cũ " + CONTRACT)
    review = _review(db, user, "bản cũ " + CONTRACT)
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEY", KEY)
    db.expire_all()
    assert db.get(ChatMessage, message.id).content.startswith("tin nhắn cũ")

    # Narrowed to this test's rows: the module transaction sees the whole dev database.
    monkeypatch.setattr(encrypt_existing_content, "TARGETS", (
        (ChatMessage, ("content",), f"id = '{message.id}' AND content NOT LIKE 'enc:v1:%'"),
        (ContractReview, ("contract_text", "result"), f"id = '{review.id}' AND jsonb_typeof(result) <> 'string'"),
    ))
    counts = encrypt_existing_content.seal_existing(db, apply=True)

    assert counts == {"chat_messages": 1, "contract_reviews": 1}
    assert is_sealed(db.execute(text("select content from chat_messages where id = :i"), {"i": message.id}).scalar())
    assert db.execute(text("select jsonb_typeof(result) from contract_reviews where id = :i"), {"i": review.id}).scalar() == "string"
    db.expire_all()
    assert db.get(ContractReview, review.id).contract_text.startswith("bản cũ")


def test_the_cipher_is_cached_per_key():
    assert encryption._cipher(KEY) is encryption._cipher(KEY)
