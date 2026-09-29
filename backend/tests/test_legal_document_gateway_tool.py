"""`generate_legal_document` through the tool gateway: drafting a document from chat.

The model used to see `fields` as a free-form object, so it guessed the names; the graph
stopped for an approval of the call, and only after someone approved did the tool find the
required fields missing. When a draft did get through, it opened a second approval of its
own and the user read raw JSON. The tool now tells the model every template's fields, says
what is missing before anything is stored, and its own approval is the only one.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.security import create_internal_tool_token
from app.models.models import Notification, User, WorkflowApproval
from app.tools.registry import tool_registry

NDA_FIELDS = {
    "nda_type": "mutual",
    "party_a": "NovaSoft",
    "party_b": "Đối tác Gamma",
    "purpose": "Đánh giá cơ hội hợp tác Project Y",
    "confidential_information": "Mã nguồn và tài liệu kỹ thuật",
    "effective_date": "2026-10-01",
    "duration": "2 năm",
    "confidentiality_duration": "vô thời hạn",
    "governing_law": "Việt Nam",
    "dispute_resolution": "Trọng tài VIAC",
}


@pytest.fixture(scope="module")
def admin(transactional_db_session):
    return transactional_db_session.query(User).filter(User.email == "admin@company.com").one()


def _invoke(client, user, **arguments):
    token = create_internal_tool_token(user, agent_role="LEGAL")
    return client.post(
        "/api/v1/internal/tools/generate_legal_document/invoke",
        headers={"Authorization": f"Bearer {token}"},
        json={"input": {
            "tenant_id": str(user.tenant_id),
            "audit": {
                "correlation_id": str(uuid.uuid4()),
                "idempotency_key": f"test:{uuid.uuid4()}",
            },
            **arguments,
        }},
    )


def _document_approvals(db, user):
    return [
        approval
        for approval in db.query(WorkflowApproval).filter(
            WorkflowApproval.action_type == "LEGAL_DOCUMENT_APPROVAL"
        ).all()
        if (approval.payload or {}).get("requester_id") == str(user.id)
    ]


def test_the_model_is_told_every_template_and_its_fields():
    definition = tool_registry.get("generate_legal_document")
    metadata = definition.public_metadata()

    assert metadata["terminal"] is True
    assert metadata["opens_approval"] is True
    for expected in ("NDA (", "party_a*", "confidentiality_duration*", "mutual|unilateral",
                     "SERVICE_AGREEMENT (", "MAINTENANCE_CONTRACT ("):
        assert expected in definition.description


def test_missing_fields_are_named_and_nothing_is_stored(client, transactional_db_session, admin):
    before = len(_document_approvals(transactional_db_session, admin))

    response = _invoke(
        client, admin, document_type="nda",
        fields={"party_a": "NovaSoft", "party_b": "Đối tác Gamma", "purpose": "Hợp tác"},
    )

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["status"] == "NEEDS_FIELDS"
    assert result["created"] is False
    assert "confidential_information" in result["missing_fields"]
    assert "party_a" not in result["missing_fields"]
    assert "Phạm vi thông tin mật" in result["reply"]
    assert len(_document_approvals(transactional_db_session, admin)) == before


def test_an_unknown_template_lists_the_ones_there_are(client, transactional_db_session, admin):
    response = _invoke(client, admin, document_type="SHAREHOLDER_AGREEMENT", fields={})

    result = response.json()["result"]
    assert result["status"] == "UNKNOWN_DOCUMENT_TYPE"
    assert "`NDA`" in result["reply"]


def test_a_complete_draft_opens_one_approval_like_the_form_does(
    client, transactional_db_session, admin
):
    before = len(_document_approvals(transactional_db_session, admin))

    response = _invoke(client, admin, document_type="NDA", fields=NDA_FIELDS)

    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["status"] == "SUBMITTED"
    approvals = _document_approvals(transactional_db_session, admin)
    assert len(approvals) == before + 1
    approval = transactional_db_session.get(WorkflowApproval, uuid.UUID(result["approval_id"]))
    assert approval.status == "WAITING"
    # The shared submission, not a copy: the preview link and the approvers' notices the
    # chat path used to miss.
    assert approval.payload["preview_url"].endswith(f"{result['artifact_id']}/preview")
    assert transactional_db_session.query(Notification).filter(
        Notification.entity_id == str(approval.id)
    ).count() > 0
    # Words for the user, with what was used and what to watch.
    assert "Thỏa thuận bảo mật (NDA)" in result["reply"]
    assert "Bên B: Đối tác Gamma" in result["reply"]
    assert "Nghĩa vụ bảo mật không giới hạn thời gian" in result["reply"]
    assert not result["reply"].lstrip().startswith("{")
