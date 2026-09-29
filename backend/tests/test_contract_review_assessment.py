"""The model reads each clause; the rules are the floor under it.

Live 2026-09-29, the rules alone: an office lease was typed a service agreement and faulted
for lacking an SLA and IP terms, while none of its one-sided terms was raised; an employment
contract that withheld the employee's diploma was faulted only for lacking an IP clause.
"""

import json

from app.domains.legal import contract_review_store
from app.domains.legal.chat_contract_review import run_chat_contract_review
from app.domains.legal.contract_review.analyzer import review_contract
from app.domains.legal.contract_review.clause_parser import split_contract_clauses
from app.clients.ai_service_client import AIServiceError
from app.domains.legal.contract_review.llm_assessment import (
    MAP_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
    REVISIONS_MISSING_NOTICE,
    RULES_ONLY_NOTICE,
    parse_assessment,
    review_with_assessment,
)
from app.models.models import User

LEASE = """HỢP ĐỒNG THUÊ VĂN PHÒNG
Bên cho thuê (Bên A): Công ty CP Bất động sản Sông Hồng
Bên thuê (Bên B): Công ty TNHH Giải pháp Số An Khang

Điều 1. Diện tích thuê: 320 m2 sàn tầng 12, tòa nhà Sông Hồng Tower.
Điều 2. Thời hạn thuê: 03 năm. Bên A được quyền điều chỉnh giá thuê hằng năm theo thông báo mà không cần sự đồng ý của Bên B.
Điều 3. Đặt cọc: Bên B đặt cọc 06 tháng tiền thuê. Bên A không hoàn trả tiền cọc nếu Bên B chấm dứt hợp đồng trước hạn vì bất kỳ lý do gì.
Điều 4. Phạt vi phạm: Bên B vi phạm nghĩa vụ thanh toán chịu phạt 30% giá trị hợp đồng.
Điều 5. Thanh toán: Bên B thanh toán trong 03 ngày, phần còn lại trong 05 ngày.
"""


def _clause_id(clauses, number):
    return next(clause["id"] for clause in clauses if clause["number"] == number)


def _model_reply(clauses):
    return {
        "contract_type": "LEASE",
        "contract_type_label": "Hợp đồng thuê văn phòng",
        "contract_type_confidence": 0.93,
        "parties": {
            "PARTY_A": {"name": "Công ty CP Bất động sản Sông Hồng", "role": "Bên cho thuê"},
            "PARTY_B": {"name": "Công ty TNHH Giải pháp Số An Khang", "role": "Bên thuê"},
        },
        "checklist": [
            {"category": "DEPOSIT", "label": "Đặt cọc", "status": "PRESENT", "clause_ids": [_clause_id(clauses, "3")], "severity_if_missing": "MEDIUM"},
            {"category": "REPAIR", "label": "Sửa chữa, bảo trì", "status": "MISSING", "clause_ids": [], "severity_if_missing": "MEDIUM"},
        ],
        "findings": [
            {
                "clause_id": _clause_id(clauses, "2"), "category": "PAYMENT", "finding_type": "COMMERCIAL_RISK",
                "severity": "HIGH", "impact": "ADVERSE", "issue": "Bên cho thuê tự điều chỉnh giá",
                "evidence": "Bên A được quyền điều chỉnh giá thuê hằng năm theo thông báo",
                "reason": "Bên thuê không kiểm soát được chi phí.", "legal_basis": None,
                "recommendation": "Giới hạn mức tăng.", "suggested_revision": "Giá thuê tăng tối đa 5%/năm.",
            },
            {
                "clause_id": _clause_id(clauses, "3"), "category": "DEPOSIT", "finding_type": "COMMERCIAL_RISK",
                "severity": "HIGH", "impact": "ADVERSE", "issue": "Mất trắng tiền cọc",
                "evidence": "trích dẫn không có trong hợp đồng",
                "reason": "Bên thuê mất 6 tháng tiền thuê.", "legal_basis": "Điều 328 Bộ luật Dân sự 2015",
                "recommendation": "Hoàn cọc theo tỷ lệ.", "suggested_revision": "Tiền cọc được hoàn trừ thiệt hại thực tế.",
            },
            {
                "clause_id": "clause-99", "category": "TERMINATION", "finding_type": "COMMERCIAL_RISK",
                "severity": "CRITICAL", "impact": "ADVERSE", "issue": "Điều khoản bịa", "evidence": "",
                "reason": "", "legal_basis": None, "recommendation": "", "suggested_revision": "",
            },
            {
                "clause_id": None, "category": "REPAIR", "finding_type": "MISSING_CLAUSE",
                "severity": "MEDIUM", "impact": "ADVERSE", "issue": "Thiếu điều khoản sửa chữa", "evidence": "",
                "reason": "Không rõ ai chịu chi phí.", "legal_basis": None,
                "recommendation": "Bổ sung.", "suggested_revision": "Bên A chịu sửa chữa kết cấu.",
            },
        ],
    }


class FakeModel:
    enabled = True

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def generate_text(self, messages, **_kwargs):
        self.calls += 1
        content = self.reply if isinstance(self.reply, str) else json.dumps(self.reply, ensure_ascii=False)
        return {"provider": "gemini", "model": "flash", "content": content, "usage": {"total_tokens": 10}}


def test_a_lease_is_judged_as_a_lease_not_a_service_agreement():
    clauses = split_contract_clauses(LEASE)
    assessment = parse_assessment(json.dumps(_model_reply(clauses)), clauses, document_scope="FULL")

    result = review_contract(LEASE, "lease.txt", "PARTY_B", assessment=assessment)

    assert result["contract_type"] == "LEASE"
    assert result["contract_type_label"] == "Hợp đồng thuê văn phòng"
    assert result["contract_type_confidence"] == 0.93
    assert result["review_version"] == "3.0" and result["review_engine"] == "LLM_ASSISTED"
    categories = {finding["category"] for finding in result["findings"]}
    # No service-agreement checklist borrowed for a type the rule pack does not know.
    assert not categories & {"SLA", "SCOPE", "INTELLECTUAL_PROPERTY", "DATA_PROTECTION"}
    assert {"PAYMENT", "DEPOSIT", "REPAIR"} <= categories
    assert [row["category"] for row in result["checklist"]] == ["DEPOSIT", "REPAIR"]
    assert result["represented_party_label"] == "Bên B · Bên thuê"
    assert result["metadata"]["party_a"] == "Công ty CP Bất động sản Sông Hồng"
    assert result["metadata"]["party_mapping_warnings"] == []


def test_the_model_cannot_invent_a_clause_or_a_quote():
    clauses = split_contract_clauses(LEASE)
    assessment = parse_assessment(json.dumps(_model_reply(clauses)), clauses, document_scope="FULL")

    result = review_contract(LEASE, "lease.txt", "PARTY_B", assessment=assessment)

    assert "Điều khoản bịa" not in {finding["issue"] for finding in result["findings"]}
    deposit = next(finding for finding in result["findings"] if finding["category"] == "DEPOSIT")
    # The made-up quote is replaced by the clause itself.
    assert "Bên A không hoàn trả tiền cọc" in deposit["evidence"]
    assert deposit["sources"][0]["type"] == "AI_LEGAL_BASIS"
    assert "Legal cần xác nhận" in deposit["sources"][0]["note"]


def test_a_rule_the_model_skipped_is_still_raised_and_its_heuristic_noise_is_not():
    clauses = split_contract_clauses(LEASE)
    assessment = parse_assessment(json.dumps(_model_reply(clauses)), clauses, document_scope="FULL")

    findings = review_contract(LEASE, "lease.txt", "PARTY_B", assessment=assessment)["findings"]

    penalty = [finding for finding in findings if finding["category"] == "PENALTY"]
    assert len(penalty) == 1 and "30%" in penalty[0]["issue"]
    # "03 ngày" and "05 ngày" in one payment clause are two instalments, not a conflict.
    assert not [finding for finding in findings if finding["finding_type"] == "INTERNAL_CONFLICT"]


def test_a_rule_finding_the_model_made_too_is_not_doubled():
    clauses = split_contract_clauses(LEASE)
    reply = _model_reply(clauses)
    reply["findings"].append({
        "clause_id": _clause_id(clauses, "4"), "category": "PENALTY", "finding_type": "LEGAL_ISSUE",
        "severity": "CRITICAL", "impact": "ADVERSE", "issue": "Phạt 30% vượt trần 8%", "evidence": "phạt 30% giá trị hợp đồng",
        "reason": "Vượt trần.", "legal_basis": "Điều 301 Luật Thương mại 2005", "recommendation": "Giảm.", "suggested_revision": "Phạt 8%.",
    })
    assessment = parse_assessment(json.dumps(reply), clauses, document_scope="FULL")

    findings = review_contract(LEASE, "lease.txt", "PARTY_B", assessment=assessment)["findings"]

    assert [finding["issue"] for finding in findings if finding["category"] == "PENALTY"] == ["Phạt 30% vượt trần 8%"]


def test_a_rule_the_model_rated_lower_stays_as_the_floor():
    """Live: the model called a penalty "unbalanced" at MEDIUM; the cap the rule knows was lost."""
    clauses = split_contract_clauses(LEASE)
    reply = _model_reply(clauses)
    reply["findings"].append({
        "clause_id": _clause_id(clauses, "4"), "category": "PENALTY", "finding_type": "COMMERCIAL_RISK",
        "severity": "MEDIUM", "impact": "ADVERSE", "issue": "Mức phạt không cân xứng", "evidence": "",
        "reason": "Lệch.", "legal_basis": None, "recommendation": "Cân bằng.", "suggested_revision": "",
    })
    assessment = parse_assessment(json.dumps(reply), clauses, document_scope="FULL")

    findings = review_contract(LEASE, "lease.txt", "PARTY_B", assessment=assessment)["findings"]

    penalty = {finding["issue"]: finding["severity"] for finding in findings if finding["category"] == "PENALTY"}
    assert penalty["Mức phạt không cân xứng"] == "MEDIUM"
    assert penalty["Mức phạt vi phạm 30% cần được kiểm tra"] == "CRITICAL"


def test_an_excerpt_reports_no_missing_clause():
    clauses = split_contract_clauses(LEASE)
    assessment = parse_assessment(json.dumps(_model_reply(clauses)), clauses, document_scope="EXCERPT")

    result = review_contract(LEASE, "lease.txt", "PARTY_B", document_scope="EXCERPT", assessment=assessment)

    assert not [finding for finding in result["findings"] if finding["finding_type"] == "MISSING_CLAUSE"]
    assert {row["status"] for row in result["checklist"]} <= {"PRESENT", "NOT_IN_EXCERPT"}


def test_a_known_type_keeps_its_checklist_and_the_model_can_mark_a_group_present():
    service = """HỢP ĐỒNG DỊCH VỤ
Bên A: Công ty Alpha
Bên B: Công ty Beta
Điều 1. Phạm vi dịch vụ: Bên A vận hành hệ thống cho Bên B theo Phụ lục 1.
Điều 2. Bồi thường: tổng mức bồi thường của mỗi Bên tối đa bằng phí 12 tháng.
"""
    clauses = split_contract_clauses(service)
    reply = {
        "contract_type": "SERVICE_AGREEMENT", "contract_type_label": "x", "contract_type_confidence": 0.9,
        "parties": {}, "checklist": [],
        "findings": [{
            "clause_id": _clause_id(clauses, "2"), "category": "LIABILITY", "finding_type": "COMMERCIAL_RISK",
            "severity": "LOW", "impact": "BALANCED", "issue": "Có trần bồi thường", "evidence": "",
            "reason": "Cân bằng.", "legal_basis": None, "recommendation": "Giữ.", "suggested_revision": "",
        }],
    }
    assessment = parse_assessment(json.dumps(reply), clauses, document_scope="FULL")

    result = review_contract(service, "service.txt", "PARTY_A", assessment=assessment)

    assert result["contract_type_label"] == "Hợp đồng dịch vụ"
    rows = {row["category"]: row for row in result["checklist"]}
    assert "SLA" in rows and rows["SLA"]["status"] == "MISSING"
    # The rules look for "giới hạn trách nhiệm"; the model read the cap in "tối đa bằng".
    assert rows["LIABILITY"]["status"] == "PRESENT"
    assert not [f for f in result["findings"] if f["category"] == "LIABILITY" and f["finding_type"] == "MISSING_CLAUSE"]


def test_without_the_model_the_rules_review_and_say_so():
    for reply in ("not json at all", {"contract_type": "??"}):
        result = review_with_assessment(LEASE, "lease.txt", "PARTY_B", client=FakeModel(reply))
        assert result["review_engine"] == "RULES" and result["review_version"] == "2.0"
        assert result["review_disclaimer"].startswith(RULES_ONLY_NOTICE)

    class Echo(FakeModel):
        def generate_text(self, messages, **kwargs):
            return {**super().generate_text(messages, **kwargs), "provider": "local"}

    assert review_with_assessment(LEASE, "lease.txt", "PARTY_B", client=Echo({}))["review_engine"] == "RULES"


EMPLOYMENT = """HỢP ĐỒNG LAO ĐỘNG
Người sử dụng lao động: Công ty TNHH Bảo Long
Người lao động: Lê Thị Mai, điện thoại 0912 345 678, CCCD số 001202012345
Điều 1. Thử việc: 90 ngày, hưởng 70% mức lương chính thức.
Điều 2. Người lao động nộp bản gốc bằng tốt nghiệp cho công ty giữ.
Điều 3. Tiền lương: 6.500.000 đồng/tháng.
Điều 4. Lê Thị Mai không được kết hôn trong 12 tháng đầu.
"""


class StagedModel:
    """Plays each stage of the reading: the map, a review batch, the revisions."""

    enabled = True

    def __init__(self, contract, *, fail=(), fail_batches=0):
        self.clauses = split_contract_clauses(contract)
        self.fail, self.fail_batches = set(fail), fail_batches
        self.calls: list[tuple[str, dict]] = []

    def _id(self, number):
        return next(clause["id"] for clause in self.clauses if clause["number"] == number)

    def generate_text(self, messages, **_kwargs):
        system, payload = messages[0]["content"], json.loads(messages[1]["content"])
        stage = "map" if system == MAP_SYSTEM_PROMPT else "review" if system == REVIEW_SYSTEM_PROMPT else "revise"
        self.calls.append((stage, payload))
        if stage in self.fail:
            raise AIServiceError("provider down")
        if stage == "review" and self.fail_batches:
            self.fail_batches -= 1
            raise AIServiceError("provider down")
        if stage == "map":
            reply = {
                "contract_type": "EMPLOYMENT_CONTRACT", "contract_type_confidence": 0.97,
                "parties": {"PARTY_A": {"name": "Công ty TNHH Bảo Long", "role": "Người sử dụng lao động"},
                            "PARTY_B": {"name": "[NGƯỜI_1]", "role": "Người lao động"}},
                "clause_categories": [
                    {"clause_id": clause["id"], "category": {"1": "PROBATION", "2": "DOCUMENTS", "3": "SALARY", "4": "PERSONAL_RIGHTS"}.get(clause["number"], "PARTIES")}
                    for clause in payload["clauses"]
                ],
                "checklist": [],
                "findings": [
                    {"clause_id": None, "category": "WORKING_TIME", "finding_type": "MISSING_CLAUSE",
                     "severity": "MEDIUM", "issue": "Thiếu thời giờ làm việc", "reason": "r", "recommendation": "Bổ sung."},
                    # The map judging a clause the review also judges: a duplicate.
                    {"clause_id": self._id("1"), "category": "PROBATION", "finding_type": "AMBIGUOUS_CLAUSE",
                     "severity": "HIGH", "issue": "Thử việc dài (bản đồ)", "reason": "r", "recommendation": "x"},
                    {"clause_id": self._id("3"), "category": "SALARY", "finding_type": "INTERNAL_CONFLICT",
                     "severity": "MEDIUM", "issue": "Lương mâu thuẫn Điều 9", "reason": "r", "recommendation": "x"},
                ],
            }
        elif stage == "review":
            shown = {clause["id"]: clause for clause in payload["clauses_to_review"]}
            reply = {"findings": [
                {"clause_id": clause_id, "category": clause["category"], "finding_type": "LEGAL_ISSUE",
                 "severity": "CRITICAL", "impact": "ADVERSE", "favors": "PARTY_A", "confidence": 0.9,
                 "issue": f"Trái luật ở điều {clause['number']}", "evidence": clause["text"],
                 "reason": f"{clause['text'][:40]}", "legal_basis": "Điều 25 Bộ luật Lao động 2019",
                 "recommendation": "Sửa."}
                for clause_id, clause in shown.items() if clause["number"] in {"1", "2", "4"}
            ]}
        else:
            reply = {"revisions": [{"id": problem["id"], "suggested_revision": f"Điều sửa cho {problem['issue']}"} for problem in payload["problems"]]}
        return {"provider": "gemini", "content": json.dumps(reply, ensure_ascii=False), "usage": {"total_tokens": 5}}


def _sent(model):
    return " ".join(json.dumps(payload, ensure_ascii=False) for _, payload in model.calls)


def test_the_contract_is_read_in_stages_each_batch_seeing_only_its_clauses():
    model = StagedModel(EMPLOYMENT)
    metered = []

    result = review_with_assessment(EMPLOYMENT, "hd.txt", "PARTY_A", client=model, on_usage=metered.append)

    stages = [stage for stage, _ in model.calls]
    assert stages[0] == "map" and stages[-1] == "revise"
    assert 2 <= stages.count("review") <= 4
    assert len(metered) == len(model.calls)
    reviewed = [
        {clause["number"] for clause in payload["clauses_to_review"]}
        for stage, payload in model.calls if stage == "review"
    ]
    # Every clause is reviewed once, the party details not at all.
    assert sorted(number for batch in reviewed for number in batch) == ["1", "2", "3", "4"]
    assert all("outline" in payload for stage, payload in model.calls if stage == "review")

    findings = {finding["clause"]: finding for finding in result["findings"] if finding["finding_type"] == "LEGAL_ISSUE"}
    assert set(findings) == {"1", "2", "4"}
    assert findings["1"]["confidence"] == 0.9 and findings["1"]["favors"] == "PARTY_A"
    assert findings["1"]["suggested_revision"].startswith("Điều sửa cho")
    assert any(finding["finding_type"] == "MISSING_CLAUSE" and finding["category"] == "WORKING_TIME" for finding in result["findings"])
    assert result["review_disclaimer"].startswith("Kết quả là hỗ trợ")


def test_the_provider_never_sees_personal_data_and_the_review_shows_it_back():
    model = StagedModel(EMPLOYMENT)

    result = review_with_assessment(EMPLOYMENT, "hd.txt", "PARTY_A", client=model)

    sent = _sent(model)
    for personal in ("Lê Thị Mai", "0912 345 678", "001202012345"):
        assert personal not in sent
    assert "[NGƯỜI_1]" in sent and "Công ty TNHH Bảo Long" in sent and "6.500.000 đồng" in sent
    marriage = next(finding for finding in result["findings"] if finding["clause"] == "4")
    assert "Lê Thị Mai" in marriage["reason"] and "Lê Thị Mai" in marriage["evidence"]
    assert result["metadata"]["party_b"] == "Lê Thị Mai"


def test_a_failed_batch_is_named_and_the_rest_still_counts():
    model = StagedModel(EMPLOYMENT, fail_batches=1)

    result = review_with_assessment(EMPLOYMENT, "hd.txt", "PARTY_A", client=model)

    assert result["review_engine"] == "LLM_ASSISTED"
    assert result["unreviewed_clauses"]
    assert f"các điều {', '.join(result['unreviewed_clauses'])}" in result["review_disclaimer"]


def test_without_the_map_or_any_batch_the_rules_review_and_say_so():
    for model in (StagedModel(EMPLOYMENT, fail={"map"}), StagedModel(EMPLOYMENT, fail={"review"})):
        result = review_with_assessment(EMPLOYMENT, "hd.txt", "PARTY_A", client=model)
        assert result["review_engine"] == "RULES"
        assert result["review_disclaimer"].startswith(RULES_ONLY_NOTICE)


def test_findings_without_revisions_say_so():
    result = review_with_assessment(EMPLOYMENT, "hd.txt", "PARTY_A", client=StagedModel(EMPLOYMENT, fail={"revise"}))

    assert result["review_engine"] == "LLM_ASSISTED"
    assert REVISIONS_MISSING_NOTICE in result["review_disclaimer"]


def test_no_stage_starts_once_the_budget_is_spent():
    model = StagedModel(EMPLOYMENT)

    result = review_with_assessment(EMPLOYMENT, "hd.txt", "PARTY_A", client=model, budget_seconds=5)

    assert model.calls == [] and result["review_engine"] == "RULES"


def test_sending_the_same_contract_again_reopens_the_review_without_the_model(
    transactional_db_session, monkeypatch
):
    db = transactional_db_session
    user = db.query(User).filter(User.email == "employee@company.com").one()
    contract = EMPLOYMENT + "\nĐiều 9. Bản dùng cho kịch bản reopen.\n"
    model = StagedModel(contract)
    monkeypatch.setattr("app.domains.legal.contract_review.llm_assessment.get_ai_service_client", lambda: model)

    first, review = run_chat_contract_review(db, user, contract, represented_party="PARTY_A", document_scope="FULL")
    calls = len(model.calls)
    again, same = run_chat_contract_review(db, user, contract, represented_party="PARTY_A", document_scope="FULL")

    assert calls >= 3 and len(model.calls) == calls
    assert same.id == review.id
    # Decisions are checked against the stored row, so the card must carry its keys.
    assert [f["finding_key"] for f in again["findings"]] == [f["finding_key"] for f in first["findings"]]
    assert contract_review_store.finding_keys(same) == {f["finding_key"] for f in again["findings"]}


def test_the_map_keeps_only_what_the_review_did_not_raise():
    result = review_with_assessment(EMPLOYMENT, "hd.txt", "PARTY_A", client=StagedModel(EMPLOYMENT))

    issues = {finding["issue"] for finding in result["findings"]}
    assert "Thử việc dài (bản đồ)" not in issues
    assert "Lương mâu thuẫn Điều 9" in issues
