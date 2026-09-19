"""Contract reviews and redline decisions must survive the browser session.

Before this, a reviewer's Accept/Reject/Edit lived in React state alone: a refresh
erased the work and nothing recorded who had accepted which clause.
"""

import pytest


CONTRACT = """
HỢP ĐỒNG DỊCH VỤ CÔNG NGHỆ
Bên A: Công ty Dịch vụ Alpha
Bên B: Công ty Khách hàng Beta

Điều 1. Phạm vi dịch vụ
Bên A cung cấp một hệ thống.

Điều 2. Phạt vi phạm
Mức phạt vi phạm là 30% giá trị hợp đồng.

Điều 3. Trách nhiệm
Bên A chịu trách nhiệm không giới hạn với mọi thiệt hại.

Điều 4. Chấm dứt
Bên B có quyền đơn phương chấm dứt hợp đồng bất kỳ lúc nào.
"""


@pytest.fixture(scope="module")
def manager_token_headers(client):
    """A user in the tenant who is neither an approver nor the review's creator."""
    res = client.post(
        "/api/v1/auth/login",
        json={"email": "hr.manager@company.com", "password": "Password123!"},
    )
    assert res.status_code == 200, f"Manager login failed: {res.text}"
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def unique_contract(marker: str) -> str:
    """A contract distinct from every other test's.

    Reviews are idempotent on (creator, contract text, perspective), so two tests
    submitting the identical contract would share one review -- and one test's
    decisions would leak into the other's assertions.
    """
    return CONTRACT + f"\nĐiều 9. Ghi chú\nBản dùng cho kịch bản {marker}.\n"


def _review(client, headers, *, party="PARTY_A", text=CONTRACT):
    response = client.post(
        "/api/v1/legal/review-document",
        files={"file": ("service.txt", text.encode(), "text/plain")},
        data={"represented_party": party},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_review_is_persisted_and_listed_for_its_creator(client, employee_token_headers):
    payload = _review(client, employee_token_headers)

    assert payload["review_id"]
    assert payload["redline_url"].endswith(f"/{payload['review_id']}/redline")

    listed = client.get("/api/v1/legal/contract-reviews", headers=employee_token_headers)
    assert listed.status_code == 200
    assert payload["review_id"] in {item["review_id"] for item in listed.json()}


def test_reuploading_the_same_contract_resumes_the_same_review(
    client, employee_token_headers
):
    """Re-running a review must not orphan the decisions already recorded on it."""
    first = _review(client, employee_token_headers, party="PARTY_B")
    finding_key = first["findings"][0]["finding_key"]

    saved = client.put(
        f"/api/v1/legal/contract-reviews/{first['review_id']}/decisions/{finding_key}",
        json={"decision": "ACCEPTED"},
        headers=employee_token_headers,
    )
    assert saved.status_code == 200

    second = _review(client, employee_token_headers, party="PARTY_B")
    assert second["review_id"] == first["review_id"]
    assert [item["finding_key"] for item in second["decisions"]] == [finding_key]


def test_decision_survives_and_records_its_author(client, employee_token_headers):
    payload = _review(
        client, employee_token_headers, party="NEUTRAL", text=unique_contract("decision-author")
    )
    review_id = payload["review_id"]
    finding_key = payload["findings"][0]["finding_key"]

    response = client.put(
        f"/api/v1/legal/contract-reviews/{review_id}/decisions/{finding_key}",
        json={"decision": "EDITED", "revised_text": "Mức phạt tối đa 8%."},
        headers=employee_token_headers,
    )
    assert response.status_code == 200

    reopened = client.get(
        f"/api/v1/legal/contract-reviews/{review_id}", headers=employee_token_headers
    )
    assert reopened.status_code == 200
    body = reopened.json()
    decision = next(
        item for item in body["decisions"] if item["finding_key"] == finding_key
    )
    assert decision["decision"] == "EDITED"
    assert decision["revised_text"] == "Mức phạt tối đa 8%."
    assert decision["decided_by_name"]
    # The reopened review still carries the analyzer output the decisions refer to.
    assert body["findings"] and body["risk_level"] == payload["risk_level"]


def test_decision_can_be_cleared(client, employee_token_headers):
    payload = _review(
        client, employee_token_headers, party="PARTY_A", text=unique_contract("clear-decision")
    )
    review_id = payload["review_id"]
    finding_key = payload["findings"][1]["finding_key"]

    client.put(
        f"/api/v1/legal/contract-reviews/{review_id}/decisions/{finding_key}",
        json={"decision": "REJECTED"},
        headers=employee_token_headers,
    )
    cleared = client.delete(
        f"/api/v1/legal/contract-reviews/{review_id}/decisions/{finding_key}",
        headers=employee_token_headers,
    )
    assert cleared.status_code == 200
    assert finding_key not in {item["finding_key"] for item in cleared.json()["decisions"]}


def test_invalid_decisions_are_refused(client, employee_token_headers):
    payload = _review(
        client, employee_token_headers, party="PARTY_A", text=unique_contract("invalid-decisions")
    )
    review_id = payload["review_id"]
    finding_key = payload["findings"][0]["finding_key"]

    blank_edit = client.put(
        f"/api/v1/legal/contract-reviews/{review_id}/decisions/{finding_key}",
        json={"decision": "EDITED", "revised_text": "   "},
        headers=employee_token_headers,
    )
    assert blank_edit.status_code == 422

    unknown_finding = client.put(
        f"/api/v1/legal/contract-reviews/{review_id}/decisions/f-doesnotexist",
        json={"decision": "ACCEPTED"},
        headers=employee_token_headers,
    )
    assert unknown_finding.status_code == 422

    bad_verb = client.put(
        f"/api/v1/legal/contract-reviews/{review_id}/decisions/{finding_key}",
        json={"decision": "MAYBE"},
        headers=employee_token_headers,
    )
    assert bad_verb.status_code == 422


def test_review_is_hidden_from_other_staff_but_visible_to_executives(
    client, employee_token_headers, manager_token_headers, ceo_token_headers
):
    payload = _review(
        client, employee_token_headers, party="PARTY_A", text=unique_contract("isolation")
    )
    review_id = payload["review_id"]

    assert client.get(
        f"/api/v1/legal/contract-reviews/{review_id}", headers=manager_token_headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/legal/contract-reviews/{review_id}", headers=ceo_token_headers
    ).status_code == 200


def test_reading_a_review_requires_authentication(client, employee_token_headers):
    review_id = _review(client, employee_token_headers)["review_id"]

    assert client.get(f"/api/v1/legal/contract-reviews/{review_id}").status_code == 401
    assert client.get("/api/v1/legal/contract-reviews").status_code == 401


def test_redline_download_is_authenticated_and_scoped(
    client, employee_token_headers, manager_token_headers
):
    """The endpoint this replaces served anyone, logged in or not."""
    payload = _review(
        client, employee_token_headers, party="PARTY_A", text=unique_contract("redline-acl")
    )
    url = f"/api/v1/legal/contract-reviews/{payload['review_id']}/redline"

    assert client.get(url).status_code == 401
    assert client.get(url, headers=manager_token_headers).status_code == 404


def test_redline_needs_an_accepted_revision_then_returns_a_docx(
    client, employee_token_headers
):
    payload = _review(
        client, employee_token_headers, party="PARTY_B", text=unique_contract("redline-docx")
    )
    review_id = payload["review_id"]
    url = f"/api/v1/legal/contract-reviews/{review_id}/redline"

    empty = client.get(url, headers=employee_token_headers)
    assert empty.status_code == 409

    finding = payload["findings"][0]
    client.put(
        f"/api/v1/legal/contract-reviews/{review_id}/decisions/{finding['finding_key']}",
        json={"decision": "ACCEPTED"},
        headers=employee_token_headers,
    )

    response = client.get(url, headers=employee_token_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    # A real DOCX is a zip; the old stub returned plain text.
    assert response.content[:2] == b"PK"


def test_changing_a_decision_invalidates_the_cached_redline(
    client, employee_token_headers
):
    """A cached file must not outlive the decisions it was built from."""
    payload = _review(
        client, employee_token_headers, party="NEUTRAL", text=unique_contract("redline-cache")
    )
    review_id = payload["review_id"]
    url = f"/api/v1/legal/contract-reviews/{review_id}/redline"
    first_finding = payload["findings"][0]["finding_key"]
    second_finding = payload["findings"][1]["finding_key"]

    client.put(
        f"/api/v1/legal/contract-reviews/{review_id}/decisions/{first_finding}",
        json={"decision": "ACCEPTED"},
        headers=employee_token_headers,
    )
    before = client.get(url, headers=employee_token_headers).content

    client.put(
        f"/api/v1/legal/contract-reviews/{review_id}/decisions/{second_finding}",
        json={"decision": "EDITED", "revised_text": "Câu chữ thay thế do người rà soát viết."},
        headers=employee_token_headers,
    )
    after = client.get(url, headers=employee_token_headers)

    assert after.status_code == 200
    assert after.content != before
