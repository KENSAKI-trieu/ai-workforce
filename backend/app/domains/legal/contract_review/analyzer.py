"""Clause-level contract review with perspective-aware scoring and revisions."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from app.domains.legal.contract_review.clause_parser import (
    detect_contract_type,
    extract_review_metadata,
    split_contract_clauses,
)
from app.domains.legal.contract_review.schemas import (
    CONTRACT_REVIEW_SCHEMAS,
    LAW_SOURCES,
    get_review_schema,
)


SEVERITY_WEIGHT = {"CRITICAL": 35, "HIGH": 22, "MEDIUM": 10, "LOW": 4}
SEVERITY_FLOOR = {"CRITICAL": 90, "HIGH": 70, "MEDIUM": 40, "LOW": 15}
SEVERITY_ORDER = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
VALID_PERSPECTIVES = {"PARTY_A", "PARTY_B", "NEUTRAL"}
# The shape of a review changes with how it was made, and a stored review is reused only
# for the same version (see contract_review_store): a rules-only review saved while the
# model was down must not stand in for the model's reading on the next run.
RULES_REVIEW_VERSION = "2.0"
ASSESSED_REVIEW_VERSION = "3.0"
AI_BASIS_NOTE = "Căn cứ do AI nêu, chưa được đối chiếu với văn bản luật; Legal cần xác nhận trước khi dựa vào."


def _compact(value: str, limit: int = 600) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _perspective_label(perspective: str) -> str:
    return {
        "PARTY_A": "Bên A · Công ty",
        "PARTY_B": "Bên B · Khách hàng",
        "NEUTRAL": "Đánh giá trung lập",
    }[perspective]


def _clause_for_match(clauses: list[dict[str, Any]], start: int, text: str) -> dict[str, Any]:
    # Start at the actual regex hit so a nearby preceding clause cannot steal
    # the finding simply because it appears in the same context window.
    needle = _compact(text[start: start + 100]).lower()
    if needle:
        for width in (80, 60, 40, 24):
            prefix = needle[:width]
            if len(prefix) >= width:
                clause = next(
                    (item for item in clauses if prefix in _compact(item["text"]).lower()),
                    None,
                )
                if clause:
                    return clause

    matched = _compact(text[max(0, start - 80): start + 260])
    for clause in clauses:
        snippet = _compact(clause["text"], 180)
        if snippet and snippet[:80].lower() in matched.lower():
            return clause
    matched_tokens = set(re.findall(r"\w+", matched.lower()))
    return max(
        clauses,
        key=lambda clause: len(
            matched_tokens.intersection(re.findall(r"\w+", clause["text"].lower()))
        ),
    )


def _sources(source_ids: list[str], contract_type: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for source_id in source_ids:
        if source_id not in LAW_SOURCES:
            continue
        source = dict(LAW_SOURCES[source_id])
        source["type"] = source.pop("source_type", "LAW_REFERENCE")
        result.append(source)
    if not result:
        result.append({
            "id": f"APPROVED_TEMPLATE_{contract_type}",
            "type": "APPROVED_TEMPLATE",
            "title": f"Checklist mẫu chuẩn - {get_review_schema(contract_type)['label']}",
            "url": "",
            "note": "Rule pack nội bộ; cần thay bằng template đã được Legal phê duyệt trong Knowledge Base.",
        })
    return result


def _finding_key(
    *,
    contract_type: str,
    category: str,
    finding_type: str,
    issue: str,
    clause: dict[str, Any] | None,
) -> str:
    """A content-derived id for one finding, stable across re-runs.

    ``id`` cannot serve this purpose: it is assigned as findings are appended and
    then reassigned by the sort, so a reviewer decision recorded against
    ``finding-3`` would silently reattach to a different finding the next time the
    same contract is analysed. Every part hashed here comes from the contract text
    or from the rule that fired. Clause *text* is hashed rather than
    ``clause["id"]`` because ``clause-N`` is positional in exactly the same way.
    """
    clause_number = clause["number"] if clause else "MISSING"
    clause_text = _compact(clause["text"], 160) if clause else ""
    payload = "\x1f".join(
        part.casefold()
        for part in (
            contract_type,
            category,
            finding_type,
            _compact(issue, 200),
            clause_number,
            clause_text,
        )
    )
    return f"f-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _finding(
    findings: list[dict[str, Any]],
    *,
    contract_type: str,
    clause: dict[str, Any] | None,
    category: str,
    finding_type: str,
    severity: str,
    issue: str,
    reason: str,
    recommendation: str,
    original_text: str,
    suggested_revision: str,
    perspective: str,
    source_ids: list[str] | None = None,
    impact: str = "SHARED",
    evidence: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    confidence: float | None = None,
    favors: str | None = None,
) -> None:
    findings.append({
        "id": f"finding-{len(findings) + 1}",
        "finding_key": _finding_key(
            contract_type=contract_type,
            category=category,
            finding_type=finding_type,
            issue=issue,
            clause=clause,
        ),
        "clause_id": clause["id"] if clause else None,
        "clause": clause["number"] if clause else "MISSING",
        "clause_title": clause["title"] if clause else "Điều khoản bị thiếu",
        "category": category,
        "finding_type": finding_type,
        "severity": severity,
        "issue": issue,
        "reason": reason,
        "recommendation": recommendation,
        "evidence": _compact(evidence or original_text, 700),
        "original_text": _compact(original_text, 1200),
        "suggested_revision": suggested_revision,
        "perspective": _perspective_label(perspective),
        "impact": impact,
        "sources": sources if sources is not None else _sources(source_ids or [], contract_type),
        # Set by the model's reading: how sure it is, and which party the clause favours.
        # A rule finding is certain of its wording and says nothing of who it favours.
        "confidence": confidence,
        "favors": favors,
        "decision": "PENDING",
    })


def _detect_subject(clause_text: str) -> str:
    normalized = clause_text.lower()
    if re.search(r"mỗi bên|các bên|hai bên|either party|both parties", normalized):
        return "BOTH"
    first_party_a = re.search(r"bên a|party a|nhà cung cấp|vendor|developer|supplier", normalized)
    first_party_b = re.search(r"bên b|party b|khách hàng|customer|client", normalized)
    if first_party_a and (not first_party_b or first_party_a.start() < first_party_b.start()):
        return "PARTY_A"
    if first_party_b:
        return "PARTY_B"
    return "UNKNOWN"


def _party_impact(clause_text: str, perspective: str) -> tuple[str, str, str]:
    subject = _detect_subject(clause_text)
    if subject == "BOTH":
        return "LOW", "Quyền chấm dứt được quy định cho cả hai Bên nên không tạo bất lợi một chiều.", "BALANCED"
    if perspective == "NEUTRAL" or subject == "UNKNOWN":
        return "HIGH", "Điều khoản tạo quyền đơn phương và mất cân bằng giữa các Bên.", "SHARED"
    if subject == perspective:
        return "LOW", "Quyền đơn phương đang có lợi cho bên được đại diện nhưng có thể bị đối tác yêu cầu quyền đối ứng.", "BENEFICIAL"
    return "HIGH", "Quyền đơn phương thuộc về bên đối tác, tạo rủi ro chấm dứt và mất doanh thu/chi phí đã đầu tư.", "ADVERSE"


def _review_party_mapping(
    metadata: dict[str, Any],
    findings: list[dict[str, Any]],
    contract_type: str,
    perspective: str,
) -> None:
    for warning in metadata.get("party_mapping_warnings", []):
        _finding(
            findings,
            contract_type=contract_type,
            clause=None,
            category="PARTY_ROLES",
            finding_type="AMBIGUOUS_CLAUSE",
            severity="HIGH",
            issue="Vai trò Bên A/Bên B trong tài liệu khác quy ước hệ thống",
            reason=warning,
            recommendation="Xác nhận lại tên pháp lý và vai trò trước khi áp dụng kết quả rà soát theo góc nhìn.",
            original_text=f"Bên A: {metadata['party_a']}\nBên B: {metadata['party_b']}",
            suggested_revision="Bên A (Công ty/Nhà cung cấp): [Tên pháp lý]. Bên B (Khách hàng): [Tên pháp lý].",
            perspective=perspective,
            impact="ADVERSE",
        )


def _review_material_terms(
    text: str,
    clauses: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    contract_type: str,
    perspective: str,
) -> None:
    for match in re.finditer(r"(?:phạt(?:\s+vi\s+phạm)?|penalt(?:y|ies))\D{0,35}(\d{1,3})\s*%", text, flags=re.IGNORECASE):
        rate = int(match.group(1))
        if rate <= 8:
            continue
        clause = _clause_for_match(clauses, match.start(), text)
        _finding(findings, contract_type=contract_type, clause=clause, category="PENALTY", finding_type="LEGAL_ISSUE", severity="HIGH" if rate <= 20 else "CRITICAL", issue=f"Mức phạt vi phạm {rate}% cần được kiểm tra", reason="Đối với hợp đồng thương mại thuộc phạm vi áp dụng, mức phạt có thể vượt giới hạn tham chiếu 8% giá trị phần nghĩa vụ bị vi phạm.", recommendation="Legal xác nhận phạm vi áp dụng, căn cứ tính phạt và ngoại lệ trước khi ký.", original_text=clause["text"], suggested_revision="Mức phạt vi phạm không vượt quá 8% giá trị phần nghĩa vụ hợp đồng bị vi phạm, trừ trường hợp pháp luật áp dụng có quy định khác.", perspective=perspective, source_ids=["COMMERCIAL_LAW_ARTICLE_301"])

    material_rules = [
        {
            "pattern": r"unlimited liability|liability.{0,50}(?:without|no) limit|trách nhiệm.{0,60}(?:không giới hạn|vô hạn)",
            "category": "LIABILITY", "type": "COMMERCIAL_RISK", "severity": "HIGH",
            "issue": "Trách nhiệm không giới hạn",
            "reason": "Bên chịu trách nhiệm có thể đối mặt nghĩa vụ tài chính không dự đoán được, kể cả thiệt hại vượt xa giá trị hợp đồng.",
            "recommendation": "Thêm aggregate liability cap và giới hạn thiệt hại gián tiếp với ngoại lệ hẹp.",
            "revision": "Trừ gian lận, cố ý vi phạm và nghĩa vụ không thể giới hạn theo luật, tổng trách nhiệm không vượt quá tổng phí đã thanh toán trong 12 tháng trước sự kiện.",
        },
        {
            "pattern": r"(?:bên a|bên b|party a|party b|khách hàng|client|customer).{0,80}(?:sở hữu toàn bộ|owns all).{0,80}(?:mã nguồn|source code|intellectual property|sở hữu trí tuệ)(?:.{0,80}(?:ngay|kể từ|from commencement|upon signing))?",
            "category": "INTELLECTUAL_PROPERTY", "type": "COMMERCIAL_RISK", "severity": "HIGH",
            "issue": "Chuyển toàn bộ IP/mã nguồn mà chưa tách quyền có trước",
            "reason": "Bên phát triển có thể vô tình chuyển cả background IP; nếu quyền chuyển trước thanh toán thì còn có nguy cơ mất đòn bẩy thu phí.",
            "recommendation": "Chuyển quyền đối với deliverable sau thanh toán đủ; giữ lại background IP và thành phần dùng chung.",
            "revision": "Background IP vẫn thuộc chủ sở hữu ban đầu. Quyền đối với Deliverable chuyển cho Khách hàng sau khi hoàn thành toàn bộ nghĩa vụ thanh toán.",
        },
        {
            "pattern": r"(?:bảo mật|confidential).{0,120}(?:vô thời hạn|vĩnh viễn|perpetual|indefinite)",
            "category": "CONFIDENTIALITY", "type": "AMBIGUOUS_CLAUSE", "severity": "HIGH",
            "issue": "Nghĩa vụ bảo mật không giới hạn thời gian",
            "reason": "Nghĩa vụ vô thời hạn cho mọi loại thông tin có thể quá rộng và khó quản lý.",
            "recommendation": "Giới hạn 3-5 năm, chỉ duy trì theo thời gian được bảo vệ đối với bí mật kinh doanh.",
            "revision": "Nghĩa vụ bảo mật kéo dài 3 năm sau chấm dứt; bí mật kinh doanh được bảo vệ trong thời gian còn đáp ứng điều kiện theo luật.",
        },
    ]
    for rule in material_rules:
        match = re.search(rule["pattern"], text, flags=re.IGNORECASE)
        if not match:
            continue
        clause = _clause_for_match(clauses, match.start(), text)
        severity = rule["severity"]
        reason = rule["reason"]
        impact = "SHARED"
        subject = _detect_subject(clause["text"])
        if rule["category"] == "LIABILITY" and perspective != "NEUTRAL" and subject in {"PARTY_A", "PARTY_B"}:
            if subject == perspective:
                severity = "HIGH"
                reason = f"{_perspective_label(perspective)} là bên chịu trách nhiệm không giới hạn nên rủi ro tài chính trực tiếp ở mức cao."
                impact = "ADVERSE"
            else:
                severity = "LOW"
                reason = "Nghĩa vụ không giới hạn đang đặt lên đối tác nên có lợi cho bên được đại diện, nhưng có thể khó đàm phán hoặc thi hành."
                impact = "BENEFICIAL"
        if rule["category"] == "INTELLECTUAL_PROPERTY" and perspective != "NEUTRAL" and subject in {"PARTY_A", "PARTY_B"}:
            if subject == perspective:
                severity = "LOW"
                reason = "Bên được đại diện nhận quyền sở hữu, nhưng câu chữ 'toàn bộ' vẫn có thể bao gồm background IP ngoài chủ đích."
                impact = "BENEFICIAL"
            else:
                severity = "HIGH"
                reason = "Đối tác nhận toàn bộ quyền sở hữu; bên được đại diện có thể mất deliverable hoặc background IP trước khi nhận đủ thanh toán."
                impact = "ADVERSE"
        _finding(findings, contract_type=contract_type, clause=clause, category=rule["category"], finding_type=rule["type"], severity=severity, issue=rule["issue"], reason=reason, recommendation=rule["recommendation"], original_text=clause["text"], suggested_revision=rule["revision"], perspective=perspective, impact=impact)

    termination_match = re.search(
        r"(?:bên a|bên b|party a|party b|client|customer|vendor).{0,100}(?:đơn phương|có quyền|may).{0,70}(?:chấm dứt|terminate).{0,70}(?:bất kỳ|at any time|không cần|without|ngay lập tức|immediately)",
        text,
        flags=re.IGNORECASE,
    )
    if termination_match:
        clause = _clause_for_match(clauses, termination_match.start(), text)
        severity, reason, impact = _party_impact(clause["text"], perspective)
        _finding(findings, contract_type=contract_type, clause=clause, category="TERMINATION", finding_type="COMMERCIAL_RISK", severity=severity, issue="Quyền chấm dứt đơn phương hoặc không có bồi hoàn", reason=reason, recommendation="Thiết kế quyền đối ứng, cure period, báo trước và thanh toán phần công việc đã hoàn thành.", original_text=clause["text"], suggested_revision="Mỗi Bên chỉ được chấm dứt do vi phạm trọng yếu chưa được khắc phục trong 15 ngày; chấm dứt thuận tiện cần báo trước 30 ngày và thanh toán phần đã thực hiện.", perspective=perspective, impact=impact)


def _review_checklist(
    clauses: list[dict[str, Any]],
    schema: dict[str, Any],
    findings: list[dict[str, Any]],
    contract_type: str,
    perspective: str,
    assess_missing: bool = True,
) -> list[dict[str, Any]]:
    checklist: list[dict[str, Any]] = []
    for item in schema["checklist"]:
        matched_clauses = [
            clause
            for clause in clauses
            if any(re.search(pattern, clause["text"], flags=re.IGNORECASE) for pattern in item["patterns"])
        ]
        checklist.append({
            "category": item["category"],
            "label": item["label"],
            # An excerpt was never meant to carry every clause, so an absent one is
            # recorded as outside the text rather than reported as a defect.
            "status": (
                "PRESENT" if matched_clauses
                else "MISSING" if assess_missing
                else "NOT_IN_EXCERPT"
            ),
            "clause_ids": [clause["id"] for clause in matched_clauses[:5]],
            "severity_if_missing": item["missing_severity"],
        })
        if matched_clauses or not assess_missing:
            continue
        _finding(findings, contract_type=contract_type, clause=None, category=item["category"], finding_type="MISSING_CLAUSE", severity=item["missing_severity"], issue=f"Thiếu điều khoản: {item['label']}", reason=item["reason"], recommendation=item["recommendation"], original_text="Không tìm thấy trong nội dung hợp đồng đã trích xuất.", suggested_revision=item["suggested_revision"], perspective=perspective, source_ids=item.get("source_ids"))
    return checklist


def _review_ambiguity(
    clauses: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    contract_type: str,
    perspective: str,
) -> None:
    if contract_type not in {"SOFTWARE_DEVELOPMENT_CONTRACT", "FREELANCER_CONTRACT", "SERVICE_AGREEMENT"}:
        return
    scope_clause = next((clause for clause in clauses if re.search(r"scope|phạm vi|dịch vụ|yêu cầu", clause["text"], flags=re.IGNORECASE)), None)
    if scope_clause and (len(scope_clause["text"]) < 140 or re.search(r"(?:develop|xây dựng|phát triển)\s+(?:a|an|một)?\s*(?:platform|website|system|nền tảng|hệ thống)\s*\.?$", scope_clause["text"], flags=re.IGNORECASE)):
        _finding(findings, contract_type=contract_type, clause=scope_clause, category="SCOPE", finding_type="AMBIGUOUS_CLAUSE", severity="HIGH", issue="Phạm vi công việc quá chung chung", reason="Không có danh sách tính năng, deliverable, giả định hoặc nội dung ngoài phạm vi nên dễ phát sinh feature creep.", recommendation="Tạo SOW có module, vai trò, tích hợp, yêu cầu phi chức năng, deliverable và exclusions.", original_text=scope_clause["text"], suggested_revision="Phạm vi chỉ gồm các module tại SOW. Mọi chức năng, tích hợp hoặc thay đổi ngoài danh sách phải qua Change Request được phê duyệt.", perspective=perspective)


def _review_conflicts(
    clauses: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    contract_type: str,
    perspective: str,
) -> None:
    payment_values: list[tuple[int, dict[str, Any]]] = []
    term_values: list[tuple[int, str, dict[str, Any]]] = []
    percentages: list[tuple[int, dict[str, Any]]] = []
    for clause in clauses:
        if re.search(r"thanh toán|payment|invoice|hóa đơn", clause["text"], flags=re.IGNORECASE):
            for value in re.findall(r"(\d{1,3})\s*(?:ngày|days?)", clause["text"], flags=re.IGNORECASE):
                payment_values.append((int(value), clause))
            for value in re.findall(r"(\d{1,3})\s*%", clause["text"]):
                percentages.append((int(value), clause))
        if re.search(r"thời hạn|hiệu lực|term|duration|hết hạn", clause["text"], flags=re.IGNORECASE):
            for value, unit in re.findall(r"(\d{1,3})\s*(tháng|năm|months?|years?)", clause["text"], flags=re.IGNORECASE):
                term_values.append((int(value), unit.lower(), clause))

    unique_payment = sorted({value for value, _ in payment_values})
    if len(unique_payment) > 1:
        first_clause, second_clause = payment_values[0][1], next(clause for value, clause in payment_values if value != payment_values[0][0])
        _finding(findings, contract_type=contract_type, clause=first_clause, category="PAYMENT", finding_type="INTERNAL_CONFLICT", severity="HIGH", issue=f"Mâu thuẫn thời hạn thanh toán: {unique_payment}", reason=f"Điều {first_clause['number']} và Điều {second_clause['number']} quy định số ngày thanh toán khác nhau.", recommendation="Chọn một thời hạn thống nhất và xác định rõ sự kiện bắt đầu tính thời hạn.", original_text=f"{first_clause['text']}\n---\n{second_clause['text']}", suggested_revision="Khoản thanh toán đến hạn trong [30] ngày kể từ ngày nhận đủ hóa đơn hợp lệ và hồ sơ nghiệm thu.", perspective=perspective)

    normalized_terms = {(value * 12 if unit.startswith(("năm", "year")) else value) for value, unit, _ in term_values}
    if len(normalized_terms) > 1:
        first_value, first_unit, first = term_values[0]
        first_months = first_value * 12 if first_unit.startswith(("năm", "year")) else first_value
        second = next(
            item[2]
            for item in term_values[1:]
            if (item[0] * 12 if item[1].startswith(("năm", "year")) else item[0]) != first_months
        )
        _finding(findings, contract_type=contract_type, clause=first, category="TERM", finding_type="INTERNAL_CONFLICT", severity="HIGH", issue="Mâu thuẫn về thời hạn hợp đồng", reason=f"Điều {first['number']} và Điều {second['number']} thể hiện thời hạn khác nhau.", recommendation="Thống nhất ngày bắt đầu, ngày kết thúc, gia hạn và ưu tiên áp dụng khi phụ lục khác nhau.", original_text=f"{first['text']}\n---\n{second['text']}", suggested_revision="Hợp đồng có hiệu lực từ [Ngày bắt đầu] đến [Ngày kết thúc]; mọi gia hạn phải được lập thành văn bản ký bởi hai Bên.", perspective=perspective)

    if len(percentages) >= 2:
        total = sum(value for value, _ in percentages)
        if total != 100:
            clause = percentages[0][1]
            _finding(findings, contract_type=contract_type, clause=clause, category="PAYMENT", finding_type="INTERNAL_CONFLICT", severity="MEDIUM", issue=f"Tổng tỷ lệ thanh toán là {total}%", reason="Lịch thanh toán theo tỷ lệ không cộng thành 100%, có thể gây thiếu hoặc trùng nghĩa vụ.", recommendation="Điều chỉnh milestone để tổng tỷ lệ bằng 100% và gắn với deliverable/nghiệm thu.", original_text=clause["text"], suggested_revision="Lịch thanh toán: [30]% khi ký, [40]% khi hoàn thành milestone, [30]% sau nghiệm thu; tổng cộng 100%.", perspective=perspective)


def _apply_parties(metadata: dict[str, Any], parties: dict[str, dict[str, Any]]) -> None:
    """Name and label each side as the contract does, not by the Bên A = Công ty convention."""
    for key, prefix, slot in (("PARTY_A", "Bên A", "party_a"), ("PARTY_B", "Bên B", "party_b")):
        party = parties.get(key) or {}
        if party.get("name"):
            metadata[slot] = party["name"]
            metadata[f"{slot}_source"] = "DOCUMENT"
        if party.get("role"):
            metadata[f"{slot}_label"] = f"{prefix} · {party['role']}"
    # The warnings compared the document with that convention; the roles now come from
    # the document itself.
    metadata["party_mapping_warnings"] = []


def _assessment_sources(item: dict[str, Any]) -> list[dict[str, Any]]:
    basis = item.get("legal_basis")
    if not basis:
        return []
    return [{
        "id": f"AI_BASIS_{hashlib.sha256(basis.casefold().encode('utf-8')).hexdigest()[:12]}",
        "type": "AI_LEGAL_BASIS",
        "title": basis,
        "url": "",
        "note": AI_BASIS_NOTE,
    }]


def _merge_assessment(
    assessment: dict[str, Any],
    clauses: list[dict[str, Any]],
    checklist: list[dict[str, Any]],
    rule_findings: list[dict[str, Any]],
    contract_type: str,
    perspective: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The model's findings, plus each rule finding the model did not already make.

    The rules are the floor: a wording they know is never lost to a model that skipped
    it or rated it lower. A rule finding goes when the model raised the same category on
    the same clause at least as severely -- live, the model called a 15% penalty
    "unbalanced" at MEDIUM and the rule's point, that it exceeds the 8% cap, was lost --
    and a "missing clause" goes when the model found that group in a clause.
    """
    by_id = {clause["id"]: clause for clause in clauses}
    findings: list[dict[str, Any]] = []
    for item in assessment["findings"]:
        clause = by_id.get(item["clause_id"]) if item["clause_id"] else None
        _finding(
            findings,
            contract_type=contract_type,
            clause=clause,
            category=item["category"],
            finding_type=item["finding_type"],
            severity=item["severity"],
            issue=item["issue"],
            reason=item["reason"],
            recommendation=item["recommendation"],
            original_text=clause["text"] if clause else "Không tìm thấy trong nội dung hợp đồng.",
            evidence=item["evidence"] or None,
            suggested_revision=item["suggested_revision"],
            perspective=perspective,
            impact=item["impact"],
            sources=_assessment_sources(item),
            confidence=item.get("confidence"),
            favors=item.get("favors"),
        )
    raised: dict[tuple[str, str], int] = {}
    for finding in findings:
        if finding["clause_id"]:
            key = (finding["clause_id"], finding["category"])
            raised[key] = max(raised.get(key, 0), SEVERITY_ORDER[finding["severity"]])
    found_in_clause: dict[str, list[str]] = {}
    for finding in findings:
        if finding["clause_id"]:
            found_in_clause.setdefault(finding["category"], []).append(finding["clause_id"])
    for row in assessment["checklist"]:
        if row["status"] == "PRESENT" and row["clause_ids"]:
            found_in_clause.setdefault(row["category"], []).extend(row["clause_ids"])
    missing_by_model = {finding["category"] for finding in findings if finding["finding_type"] == "MISSING_CLAUSE"}

    for row in checklist:
        if row["status"] == "MISSING" and found_in_clause.get(row["category"]):
            row["status"] = "PRESENT"
            row["clause_ids"] = list(dict.fromkeys(found_in_clause[row["category"]]))[:5]
    status = {row["category"]: row["status"] for row in checklist}
    for finding in rule_findings:
        if finding["finding_type"] == "MISSING_CLAUSE":
            if status.get(finding["category"]) != "MISSING" or finding["category"] in missing_by_model:
                continue
        elif raised.get((finding["clause_id"], finding["category"]), 0) >= SEVERITY_ORDER[finding["severity"]]:
            continue
        findings.append(finding)
    # A type the rule pack has no checklist for -- a lease, a sale of goods -- is checked
    # against the groups the model says that type needs.
    return findings, checklist or assessment["checklist"]


def _risk_summary(findings: list[dict[str, Any]]) -> tuple[int, str, dict[str, int]]:
    counts = Counter(finding["severity"] for finding in findings)
    raw_score = min(100, sum(SEVERITY_WEIGHT[finding["severity"]] for finding in findings))
    highest = max((finding["severity"] for finding in findings), key=lambda value: SEVERITY_ORDER[value], default="LOW")
    score = max(raw_score, SEVERITY_FLOOR[highest] if findings else 0)
    # A large number of HIGH findings must not be mislabeled as CRITICAL. That
    # label is reserved for a finding that is itself classified CRITICAL.
    level = (
        "CRITICAL"
        if highest == "CRITICAL"
        else "HIGH"
        if score >= 70
        else "MEDIUM"
        if score >= 40
        else "LOW"
    )
    return score, level, {level_name: counts.get(level_name, 0) for level_name in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}


def review_contract(
    contract_text: str,
    document_name: str,
    represented_party: str = "NEUTRAL",
    knowledge_references: list[dict[str, Any]] | None = None,
    document_scope: str = "FULL",
    assessment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Review contract text; ``document_scope`` is FULL or EXCERPT.

    An EXCERPT is judged only on what it says. Faulting a single pasted clause for lacking
    governing law, payment and confidentiality terms scored it CRITICAL and raised a legal
    approval for text that was never meant to be a whole contract.

    ``assessment`` is the model's reading of the same clauses (``llm_assessment``). With
    it, the model decides the contract type, the parties' roles and what each clause
    means, and the rules are the floor under it. The heuristics for vague scope and for
    "conflicting" payment days and percentages are left out then: on real contracts they
    read an advance in 3 days and a balance in 5 as a conflict, and a 0,1%/day interest
    rate as a payment share.
    """
    perspective = represented_party.upper()
    if perspective not in VALID_PERSPECTIVES:
        raise ValueError("represented_party phải là PARTY_A, PARTY_B hoặc NEUTRAL")
    text = contract_text.strip()
    clauses = split_contract_clauses(text)
    if assessment is None:
        detection = detect_contract_type(text)
    else:
        detection = {
            "contract_type": assessment["contract_type"],
            "contract_type_label": assessment["contract_type_label"],
            "confidence": assessment["contract_type_confidence"],
        }
    contract_type = detection["contract_type"]
    # A type the model named that the rule pack does not know has no checklist of its own;
    # borrowing the service agreement's is what faulted a lease for lacking an SLA.
    schema = get_review_schema(contract_type) if assessment is None else CONTRACT_REVIEW_SCHEMAS.get(contract_type)
    metadata = extract_review_metadata(text, clauses)
    findings: list[dict[str, Any]] = []
    party_label = _perspective_label(perspective)

    is_excerpt = document_scope.upper() == "EXCERPT"
    if assessment is None:
        _review_party_mapping(metadata, findings, contract_type, perspective)
    checklist = _review_checklist(
        clauses, schema, findings, contract_type, perspective, assess_missing=not is_excerpt
    ) if schema else []
    _review_material_terms(text, clauses, findings, contract_type, perspective)
    if assessment is None:
        _review_ambiguity(clauses, findings, contract_type, perspective)
        _review_conflicts(clauses, findings, contract_type, perspective)
    else:
        _apply_parties(metadata, assessment["parties"])
        findings, checklist = _merge_assessment(
            assessment, clauses, checklist, findings, contract_type, perspective
        )
        if perspective != "NEUTRAL":
            represented = metadata[f"party_{perspective[-1].lower()}_label"]
            for finding in findings:
                finding["perspective"] = represented
                finding["reason"] = finding["reason"].replace(party_label, represented)
            party_label = represented

    findings.sort(key=lambda item: (-SEVERITY_ORDER[item["severity"]], item["category"], item["clause"]))
    for index, finding in enumerate(findings, 1):
        finding["id"] = f"finding-{index}"
    # Two findings share a key only when the same rule fired on two clauses whose
    # opening text is identical -- indistinguishable to the reviewer as well. The
    # suffix is applied after the sort so the same contract always produces the
    # same assignment, which is what a stored decision depends on.
    key_counts: Counter[str] = Counter()
    for finding in findings:
        key_counts[finding["finding_key"]] += 1
        occurrence = key_counts[finding["finding_key"]]
        if occurrence > 1:
            finding["finding_key"] = f"{finding['finding_key']}-{occurrence}"
    risk_score, risk_level, severity_counts = _risk_summary(findings)
    missing_count = sum(item["status"] == "MISSING" for item in checklist)
    conflict_count = sum(item["finding_type"] == "INTERNAL_CONFLICT" for item in findings)
    policy_count = sum(item["finding_type"] == "POLICY_VIOLATION" for item in findings)
    categories: dict[str, str] = {}
    for finding in findings:
        current = categories.get(finding["category"])
        if current is None or SEVERITY_ORDER[finding["severity"]] > SEVERITY_ORDER[current]:
            categories[finding["category"]] = finding["severity"]

    used_sources: dict[str, dict[str, Any]] = {}
    for finding in findings:
        for source in finding["sources"]:
            used_sources[source["id"]] = source
    for source in knowledge_references or []:
        used_sources[str(source.get("id") or source.get("citation_tag") or len(used_sources))] = source

    return {
        "review_version": RULES_REVIEW_VERSION if assessment is None else ASSESSED_REVIEW_VERSION,
        "review_engine": "RULES" if assessment is None else "LLM_ASSISTED",
        "document_name": document_name,
        "represented_party": perspective,
        "represented_party_label": party_label,
        "document_scope": "EXCERPT" if is_excerpt else "FULL",
        "contract_type": contract_type,
        "contract_type_label": detection["contract_type_label"],
        "contract_type_confidence": detection["confidence"],
        "metadata": metadata,
        "clauses": clauses,
        "checklist": checklist,
        "findings": findings,
        "risks": findings,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "total_risks_found": len(findings),
        "severity_counts": severity_counts,
        "missing_clauses_count": missing_count,
        "internal_conflicts_count": conflict_count,
        "policy_violations_count": policy_count,
        "category_summary": [
            {"category": category, "severity": severity}
            for category, severity in sorted(categories.items())
        ],
        "reference_sources": list(used_sources.values()),
        "requires_legal_approval": any(finding["severity"] in {"CRITICAL", "HIGH"} for finding in findings),
        "review_disclaimer": "Kết quả là hỗ trợ rà soát tự động, không thay thế ý kiến pháp lý. Legal phải xác nhận luật áp dụng, policy và template trước khi chấp thuận sửa đổi.",
        "summary": (
            f"Đã tách {len(clauses)} điều khoản; phát hiện {len(findings)} vấn đề và "
            f"{conflict_count} mâu thuẫn nội bộ. Đây là đoạn trích nên không đánh giá "
            "các điều khoản còn thiếu."
            if is_excerpt
            else f"Đã tách {len(clauses)} điều khoản; phát hiện {len(findings)} vấn đề, "
            f"{missing_count} điều khoản thiếu và {conflict_count} mâu thuẫn nội bộ."
        ),
    }
