import io
import zipfile

import pytest
from pypdf import PdfReader

from app.main import app
from app.services.legal_document_generator import generate_legal_document
from app.services.legal_documents import list_document_schemas, validate_document_fields
from app.services.legal_service import (
    audit_contract_text,
    check_software_licenses,
    compare_contract_texts,
    detect_sensitive_data,
)


def test_contract_audit_scores_material_and_missing_clauses():
    result = audit_contract_text(
        "Customer owns all intellectual property. Supplier accepts unlimited liability. "
        "Payment is due within 90 days after invoice.",
        "msa.txt",
    )
    assert result["risk_level"] == "HIGH"
    assert result["risk_score"] >= 70
    assert {item["category"] for item in result["risks"]} >= {
        "INTELLECTUAL_PROPERTY",
        "LIABILITY",
        "TERMINATION",
    }
    assert all(item["evidence"] for item in result["risks"])


def test_privacy_checker_does_not_return_detected_values():
    source = "email,phone,cccd\ncustomer@example.com,0901234567,012345678901"
    result = detect_sensitive_data(source, ["email", "phone", "cccd"])
    assert result["requires_legal_approval"] is True
    assert {item["type"] for item in result["findings"]} >= {"EMAIL", "PHONE", "CCCD"}
    assert "customer@example.com" not in str(result)


def test_contract_comparison_reports_modified_terms():
    result = compare_contract_texts(
        "Clause 5 - Payment: 30 days\nClause 6 - Notice: 30 days",
        "Clause 5 - Payment: 90 days\nClause 6 - Notice: 30 days",
    )
    assert result["total_changes"] == 1
    assert result["changes"][0]["type"] == "MODIFIED"


def test_license_checker_flags_agpl_dependency():
    result = check_software_licenses(
        "requirements.txt", "fastapi==0.111\nultralytics==8.3"
    )
    assert result["risk_level"] == "HIGH"
    assert result["commercial_use_requires_review"] is True


def test_document_generator_outputs_valid_docx_archive():
    content, filename, media_type = generate_legal_document(
        "INTERNSHIP_CONTRACT",
        "docx",
        {
            "company": "NovaSoft",
            "intern": "Nguyễn Văn A",
            "university": "Đại học Bách khoa",
            "department": "Engineering",
            "supervisor": "Trần Thị B",
            "start_date": "2026-09-01",
            "end_date": "2026-12-01",
            "working_schedule": "4 buổi/tuần",
            "allowance": "5.000.000 VNĐ/tháng",
            "responsibilities": "Hỗ trợ kiểm thử\nViết tài liệu kỹ thuật",
            "confidentiality_duration": "3 năm",
            "ip_ownership": "Công ty sở hữu sản phẩm trong phạm vi nhiệm vụ",
        },
    )
    assert filename.endswith(".docx")
    assert "officedocument" in media_type
    assert io.BytesIO(content).read(2) == b"PK"
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "THỎA THUẬN THỰC TẬP" in document_xml
    assert "Nguyễn Văn A" in document_xml
    assert "DỰ THẢO - CẦN PHÊ DUYỆT PHÁP LÝ" in document_xml


def test_all_seven_document_types_expose_distinct_schema_fields():
    templates = list_document_schemas()
    assert len(templates) == 7
    assert {template["id"] for template in templates} == {
        "NDA",
        "EMPLOYMENT_CONTRACT",
        "FREELANCER_CONTRACT",
        "INTERNSHIP_CONTRACT",
        "SERVICE_AGREEMENT",
        "SOFTWARE_DEVELOPMENT_CONTRACT",
        "MAINTENANCE_CONTRACT",
    }
    fields_by_type = {
        template["id"]: {field["name"] for field in template["fields"]}
        for template in templates
    }
    assert "confidential_information" in fields_by_type["NDA"]
    assert "social_insurance" in fields_by_type["EMPLOYMENT_CONTRACT"]
    assert "acceptance_criteria" in fields_by_type["FREELANCER_CONTRACT"]
    assert "bug_vs_feature" in fields_by_type["MAINTENANCE_CONTRACT"]


@pytest.mark.parametrize(
    "document_type", [template["id"] for template in list_document_schemas()]
)
def test_each_document_type_generates_a_valid_docx(document_type):
    schema = next(
        template for template in list_document_schemas() if template["id"] == document_type
    )
    fields = {
        field["name"]: field.get("default") or f"Nội dung cho {field['label']}"
        for field in schema["fields"]
    }

    content, filename, media_type = generate_legal_document(
        document_type, "docx", fields
    )

    assert filename.endswith(".docx")
    assert "officedocument" in media_type
    assert content[:2] == b"PK"


def test_pdf_generator_preserves_vietnamese_text():
    schema = next(template for template in list_document_schemas() if template["id"] == "NDA")
    fields = {
        field["name"]: field.get("default") or f"Nội dung cho {field['label']}"
        for field in schema["fields"]
    }

    content, filename, media_type = generate_legal_document("NDA", "pdf", fields)

    assert filename.endswith(".pdf")
    assert media_type == "application/pdf"
    extracted = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
    assert "THỎA THUẬN BẢO MẬT THÔNG TIN" in extracted
    assert "CẦN PHÊ DUYỆT PHÁP LÝ" in extracted


def test_document_validator_returns_explainable_nda_warning():
    schema = next(template for template in list_document_schemas() if template["id"] == "NDA")
    fields = {
        field["name"]: field.get("default") or "Thông tin hợp lệ"
        for field in schema["fields"]
    }
    fields["confidentiality_duration"] = "Vô thời hạn"

    result = validate_document_fields("NDA", fields)

    assert result["valid"] is True
    assert result["warnings"][0]["code"] == "INDEFINITE_CONFIDENTIALITY"
    assert result["warnings"][0]["severity"] == "HIGH"


def test_document_generator_rejects_missing_required_fields():
    with pytest.raises(ValueError, match="Thiếu thông tin bắt buộc"):
        generate_legal_document("NDA", "docx", {"party_a": "NovaSoft"})


def test_legal_api_routes_use_the_shared_v1_prefix_once():
    route_paths = {route.path for route in app.routes}
    expected_paths = {
        "/api/v1/legal/audit-contract",
        "/api/v1/legal/review-document",
        "/api/v1/legal/compare-documents",
        "/api/v1/legal/privacy-check",
        "/api/v1/legal/license-check",
        "/api/v1/legal/generate-document",
        "/api/v1/legal/document-templates",
        "/api/v1/legal/validate-document",
        "/api/v1/legal/download-redline/{file_id}",
    }

    assert expected_paths <= route_paths
    assert not any(path.startswith("/api/v1/api/v1/") for path in route_paths)


def test_contract_review_upload_endpoint(client, employee_token_headers):
    response = client.post(
        "/api/v1/legal/review-document",
        files={
            "file": (
                "msa.txt",
                b"Customer owns all intellectual property. Supplier accepts unlimited liability.",
                "text/plain",
            )
        },
        data={"represented_party": "NEUTRAL"},
        headers=employee_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["document_name"] == "msa.txt"
    assert data["risk_level"] == "HIGH"
    assert data["total_risks_found"] > 0


def test_contract_comparison_upload_endpoint(client, employee_token_headers):
    response = client.post(
        "/api/v1/legal/compare-documents",
        files={
            "old_file": ("v1.txt", b"Payment is due in 30 days.", "text/plain"),
            "new_file": ("v2.txt", b"Payment is due in 90 days.", "text/plain"),
        },
        headers=employee_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["old_document"] == "v1.txt"
    assert data["new_document"] == "v2.txt"
    assert data["total_changes"] == 1


def test_privacy_upload_endpoint(client, employee_token_headers):
    response = client.post(
        "/api/v1/legal/privacy-check",
        files={
            "file": (
                "customers.csv",
                b"email|phone\ncustomer@example.com|0901234567",
                "text/csv",
            )
        },
        headers=employee_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["document_name"] == "customers.csv"
    assert data["contains_sensitive_data"] is True


def test_license_upload_endpoint(client, employee_token_headers):
    response = client.post(
        "/api/v1/legal/license-check",
        files={
            "file": (
                "requirements.txt",
                b"fastapi==0.111\nultralytics==8.3",
                "text/plain",
            )
        },
        headers=employee_token_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["manifest"] == "requirements.txt"
    assert data["risk_level"] == "HIGH"


def test_direct_document_generator_is_disabled(client, employee_token_headers, ceo_token_headers):
    fields = {
        "nda_type": "mutual",
        "party_a": "NovaSoft",
        "party_b": "Partner",
        "purpose": "Đánh giá cơ hội hợp tác Project X",
        "confidential_information": "Mã nguồn, dữ liệu khách hàng và tài liệu kỹ thuật",
        "effective_date": "2026-08-07",
        "duration": "2 năm",
        "confidentiality_duration": "3 năm sau khi chấm dứt",
        "governing_law": "Việt Nam",
        "dispute_resolution": "Thương lượng, sau đó giải quyết tại VIAC",
    }
    validation_response = client.post(
        "/api/v1/legal/validate-document",
        json={"document_type": "NDA", "fields": fields},
        headers=employee_token_headers,
    )
    assert validation_response.status_code == 200
    assert validation_response.json()["valid"] is True

    response = client.post(
        "/api/v1/legal/generate-document",
        json={
            "document_type": "NDA",
            "output_format": "docx",
            "fields": fields,
        },
        headers=ceo_token_headers,
    )

    assert response.status_code == 410

    blocked = client.post(
        "/api/v1/legal/generate-document",
        json={
            "document_type": "NDA",
            "output_format": "docx",
            "fields": fields,
        },
        headers=employee_token_headers,
    )
    assert blocked.status_code == 410


def test_legal_document_draft_requires_executive_approval_before_creator_download(
    client, employee_token_headers, ceo_token_headers
):
    fields = {
        "nda_type": "mutual",
        "party_a": "NovaSoft",
        "party_b": "Partner",
        "purpose": "Đánh giá cơ hội hợp tác Project Y",
        "confidential_information": "Mã nguồn và tài liệu kỹ thuật",
        "effective_date": "2026-08-08",
        "duration": "2 năm",
        "confidentiality_duration": "3 năm sau khi chấm dứt",
        "governing_law": "Việt Nam",
        "dispute_resolution": "Trọng tài VIAC",
    }
    submitted = client.post(
        "/api/v1/legal/document-drafts",
        json={
            "document_type": "NDA",
            "output_format": "docx",
            "fields": fields,
        },
        headers=employee_token_headers,
    )
    assert submitted.status_code == 201
    draft = submitted.json()
    assert draft["status"] == "WAITING"
    assert draft["can_download"] is False

    blocked_download = client.get(
        draft["download_url"], headers=employee_token_headers
    )
    assert blocked_download.status_code == 403

    pending = client.get(
        "/api/v1/approvals/pending", headers=ceo_token_headers
    ).json()
    approval = next(
        item for item in pending if item["id"] == draft["approval_id"]
    )
    assert approval["action_type"] == "LEGAL_DOCUMENT_APPROVAL"
    assert "draft_storage_key" not in approval["payload"]
    assert "approved_storage_key" not in approval["payload"]

    preview = client.get(
        approval["payload"]["preview_url"], headers=ceo_token_headers
    )
    assert preview.status_code == 200
    assert "THỎA THUẬN BẢO MẬT THÔNG TIN" in preview.json()["content"]

    review_file = client.get(
        approval["payload"]["review_download_url"], headers=ceo_token_headers
    )
    assert review_file.status_code == 200
    with zipfile.ZipFile(io.BytesIO(review_file.content)) as archive:
        draft_xml = archive.read("word/document.xml").decode("utf-8")
    assert "DỰ THẢO - CẦN PHÊ DUYỆT PHÁP LÝ" in draft_xml

    manager_login = client.post(
        "/api/v1/auth/login",
        json={"email": "hr.manager@company.com", "password": "Password123!"},
    )
    assert manager_login.status_code == 200
    manager_headers = {
        "Authorization": f"Bearer {manager_login.json()['access_token']}"
    }
    manager_action = client.post(
        f"/api/v1/approvals/{draft['approval_id']}/action",
        json={"action": "APPROVE"},
        headers=manager_headers,
    )
    assert manager_action.status_code == 403

    approved = client.post(
        f"/api/v1/approvals/{draft['approval_id']}/action",
        json={"action": "APPROVE", "comments": "Đồng ý phát hành"},
        headers=ceo_token_headers,
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"

    creator_documents = client.get(
        "/api/v1/legal/document-drafts", headers=employee_token_headers
    ).json()
    creator_draft = next(
        item for item in creator_documents if item["artifact_id"] == draft["artifact_id"]
    )
    assert creator_draft["status"] == "APPROVED"
    assert creator_draft["can_download"] is True
    assert creator_draft["comments"] == "Đồng ý phát hành"

    final_file = client.get(
        creator_draft["download_url"], headers=employee_token_headers
    )
    assert final_file.status_code == 200
    with zipfile.ZipFile(io.BytesIO(final_file.content)) as archive:
        approved_xml = archive.read("word/document.xml").decode("utf-8")
    assert "ĐÃ PHÊ DUYỆT NỘI BỘ" in approved_xml


def test_document_templates_endpoint(client, employee_token_headers):
    response = client.get(
        "/api/v1/legal/document-templates", headers=employee_token_headers
    )
    assert response.status_code == 200
    assert len(response.json()) == 7
