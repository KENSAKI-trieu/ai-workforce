"""Deterministic, explainable validation for generated legal drafts."""

from __future__ import annotations

from typing import Any

from app.domains.legal.legal_documents.schemas import get_document_schema


def _warning(code: str, title: str, message: str, recommendation: str) -> dict[str, str]:
    return {
        "code": code,
        "severity": "HIGH" if code in {"INDEFINITE_CONFIDENTIALITY", "UNLIMITED_LIABILITY"} else "MEDIUM",
        "title": title,
        "message": message,
        "recommendation": recommendation,
    }


def _text(fields: dict[str, str], name: str) -> str:
    return fields.get(name, "").strip()


def validate_document_fields(document_type: str, raw_fields: dict[str, Any]) -> dict[str, Any]:
    schema = get_document_schema(document_type)
    fields = {
        str(key): str(value).strip()
        for key, value in (raw_fields or {}).items()
        if value is not None
    }
    missing_fields = [
        field["name"]
        for field in schema["fields"]
        if field["required"] and not fields.get(field["name"], "").strip()
    ]
    labels = {field["name"]: field["label"] for field in schema["fields"]}
    warnings: list[dict[str, str]] = []
    normalized_type = document_type.upper()

    if normalized_type == "NDA":
        confidentiality_duration = _text(fields, "confidentiality_duration").lower()
        if any(term in confidentiality_duration for term in ("vô thời hạn", "vĩnh viễn", "perpetual", "indefinite")):
            warnings.append(_warning(
                "INDEFINITE_CONFIDENTIALITY",
                "Nghĩa vụ bảo mật không giới hạn thời gian",
                "Thời hạn bảo mật đang được mô tả là vô thời hạn.",
                "Cân nhắc giới hạn 3-5 năm và chỉ duy trì vô thời hạn đối với bí mật kinh doanh.",
            ))
    elif normalized_type == "EMPLOYMENT_CONTRACT":
        probation = _text(fields, "probation").lower()
        if probation and not any(term in probation for term in ("ngày", "day", "tháng", "month")):
            warnings.append(_warning(
                "UNCLEAR_PROBATION",
                "Thời gian thử việc chưa rõ",
                "Thông tin thử việc chưa thể hiện rõ thời lượng.",
                "Ghi rõ số ngày thử việc, mức lương thử việc và ngày kết thúc.",
            ))
    elif normalized_type == "FREELANCER_CONTRACT":
        if len(_text(fields, "acceptance_criteria")) < 40:
            warnings.append(_warning(
                "VAGUE_ACCEPTANCE",
                "Tiêu chí nghiệm thu còn ngắn",
                "Điều kiện để chấp nhận deliverable có thể chưa đủ khách quan.",
                "Bổ sung test, định dạng bàn giao, thời hạn phản hồi và quy tắc mặc nhiên nghiệm thu.",
            ))
    elif normalized_type == "SERVICE_AGREEMENT":
        liability = _text(fields, "liability_cap").lower()
        if any(term in liability for term in ("không giới hạn", "unlimited", "không hạn chế")):
            warnings.append(_warning(
                "UNLIMITED_LIABILITY",
                "Trách nhiệm không giới hạn",
                "Điều khoản đang để trách nhiệm ở mức không giới hạn.",
                "Đặt mức trần trách nhiệm, thường dựa trên phí đã trả trong một khoảng thời gian xác định.",
            ))
    elif normalized_type == "SOFTWARE_DEVELOPMENT_CONTRACT":
        if len(_text(fields, "requirements")) < 80:
            warnings.append(_warning(
                "AMBIGUOUS_SCOPE",
                "Phạm vi phần mềm có nguy cơ mơ hồ",
                "Danh sách yêu cầu quá ngắn để làm căn cứ kiểm soát scope và feature creep.",
                "Liệt kê module, vai trò người dùng, tích hợp, yêu cầu phi chức năng và nội dung không thuộc phạm vi.",
            ))
        if len(_text(fields, "acceptance_criteria")) < 60:
            warnings.append(_warning(
                "WEAK_ACCEPTANCE",
                "Tiêu chí nghiệm thu chưa đủ chi tiết",
                "Chưa đủ cơ sở xác định khi nào phần mềm được coi là hoàn thành.",
                "Bổ sung test case, thời hạn UAT, mức lỗi được chấp nhận và quy trình xác nhận nghiệm thu.",
            ))
    elif normalized_type == "MAINTENANCE_CONTRACT":
        distinction = _text(fields, "bug_vs_feature").lower()
        if not any(term in distinction for term in ("change request", "tính năng mới", "feature mới", "ngoài phạm vi")):
            warnings.append(_warning(
                "BUG_FEATURE_BOUNDARY",
                "Ranh giới bug và feature mới chưa rõ",
                "Mô tả hiện tại có thể khiến yêu cầu phát triển mới bị coi là bảo trì.",
                "Định nghĩa bug theo acceptance criteria và bắt buộc feature mới đi qua Change Request riêng.",
            ))

    return {
        "valid": not missing_fields,
        "missing_fields": [
            {"name": name, "label": labels.get(name, name)} for name in missing_fields
        ],
        "warnings": warnings,
        "normalized_fields": fields,
        "template": {
            "id": schema["id"],
            "label": schema["label"],
            "output_description": schema["output_description"],
        },
    }
