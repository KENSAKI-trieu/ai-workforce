"""Contract type, metadata and clause extraction with deterministic evidence."""

from __future__ import annotations

import re
from typing import Any

from app.domains.legal.contract_review.schemas import CONTRACT_REVIEW_SCHEMAS


HEADING_PATTERN = re.compile(
    r"^\s*(?:(điều|article|clause|section)\s+)?"
    r"(\d+(?:\.\d+){0,3})[\s.:\-)]+(.{2,160})$",
    flags=re.IGNORECASE,
)


def _compact(value: str, limit: int = 500) -> str:
    value = re.sub(r"[ \t]+", " ", value).strip()
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def detect_contract_type(text: str) -> dict[str, Any]:
    normalized = text.lower()
    scores: list[tuple[str, int, list[str]]] = []
    for contract_type, schema in CONTRACT_REVIEW_SCHEMAS.items():
        signals: list[str] = []
        score = 0
        for pattern in schema["detection_patterns"]:
            matches = re.findall(pattern, normalized, flags=re.IGNORECASE)
            if matches:
                signals.append(pattern)
                score += min(3, len(matches))
        scores.append((contract_type, score, signals))

    scores.sort(key=lambda item: item[1], reverse=True)
    winner, winner_score, signals = scores[0]
    runner_up_score = scores[1][1] if len(scores) > 1 else 0
    if winner_score == 0:
        winner = "SERVICE_AGREEMENT"
    confidence = 0.35 if winner_score == 0 else min(
        0.98, 0.55 + winner_score * 0.07 + max(0, winner_score - runner_up_score) * 0.04
    )
    return {
        "contract_type": winner,
        "contract_type_label": CONTRACT_REVIEW_SCHEMAS[winner]["label"],
        "confidence": round(confidence, 2),
        "signals": signals[:6],
    }


def split_contract_clauses(text: str) -> list[dict[str, Any]]:
    lines = [line.strip() for line in text.replace("\r\n", "\n").split("\n")]
    clauses: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    preamble: list[str] = []

    for line_number, line in enumerate(lines, 1):
        if not line:
            continue
        match = HEADING_PATTERN.match(line)
        is_upper_heading = (
            len(line) <= 120
            and len(line.split()) <= 14
            and line == line.upper()
            and any(character.isalpha() for character in line)
        )
        if match or is_upper_heading:
            if current:
                current["text"] = _compact("\n".join(current.pop("body")), 4000)
                clauses.append(current)
            if match:
                number = match.group(2)
                title = match.group(3).strip(" .:-")
            else:
                number = str(len(clauses) + 1)
                title = line.title()
            current = {
                "id": f"clause-{len(clauses) + 1}",
                "number": number,
                "title": title,
                "line_start": line_number,
                "body": [line],
            }
        elif current:
            current["body"].append(line)
        else:
            preamble.append(line)

    if current:
        current["text"] = _compact("\n".join(current.pop("body")), 4000)
        clauses.append(current)

    if not clauses:
        paragraphs = [
            _compact(part, 4000)
            for part in re.split(r"\n\s*\n|(?<=\.)\s+(?=(?:Điều|Article|Clause)\s+\d+)", text)
            if part.strip()
        ]
        clauses = [
            {
                "id": f"clause-{index}",
                "number": str(index),
                "title": f"Nội dung {index}",
                "line_start": None,
                "text": paragraph,
            }
            for index, paragraph in enumerate(paragraphs, 1)
        ]
    elif preamble:
        clauses.insert(0, {
            "id": "preamble",
            "number": "0",
            "title": "Phần mở đầu",
            "line_start": 1,
            "text": _compact("\n".join(preamble), 4000),
        })
    return clauses[:200]


def _party(text: str, aliases: list[str]) -> str | None:
    alias_pattern = "|".join(re.escape(alias) for alias in aliases)
    patterns = [
        rf"(?:{alias_pattern})\s*[:\-]\s*([^\n]{{2,180}})",
        rf"(?:{alias_pattern})\s+(?:là|means)\s+([^\n]{{2,180}})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _compact(match.group(1), 180).strip(" .;,–-")
    return None


def extract_review_metadata(text: str, clauses: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    clauses = clauses or split_contract_clauses(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = next(
        (
            _compact(line, 180)
            for line in lines[:25]
            if re.search(r"hợp đồng|thỏa thuận|contract|agreement|nda", line, flags=re.IGNORECASE)
        ),
        "Hợp đồng chưa xác định tên",
    )
    dates = list(dict.fromkeys(re.findall(
        r"\b(?:0?[1-9]|[12]\d|3[01])[/-](?:0?[1-9]|1[0-2])[/-](?:19|20)\d{2}\b|"
        r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b",
        text,
    )))[:12]
    amounts = list(dict.fromkeys(
        _compact(match.group(0), 100)
        for match in re.finditer(
            r"\b\d[\d.,\s]{2,}\s*(?:VND|VNĐ|USD|EUR|đồng)\b",
            text,
            flags=re.IGNORECASE,
        )
    ))[:8]
    payment_terms: list[str] = []
    for clause in clauses:
        if re.search(r"thanh toán|payment|invoice|hóa đơn", clause["text"], flags=re.IGNORECASE):
            payment_terms.append(_compact(clause["text"], 300))
    term = None
    for clause in clauses:
        if re.search(r"thời hạn|hiệu lực|term|effective", clause["text"], flags=re.IGNORECASE):
            term = _compact(clause["text"], 260)
            break
    explicit_party_a = _party(text, ["Bên A", "Party A"])
    explicit_party_b = _party(text, ["Bên B", "Party B"])
    company_name = explicit_party_a or _party(
        text,
        ["Nhà cung cấp", "Bên cung cấp", "Vendor", "Developer", "Employer"],
    )
    customer_name = explicit_party_b or _party(
        text,
        ["Khách hàng", "Bên thuê", "Bên nhận dịch vụ", "Customer", "Client"],
    )
    mapping_warnings: list[str] = []
    party_a_line = explicit_party_a or ""
    party_b_line = explicit_party_b or ""
    if re.search(r"khách hàng|customer|client|bên thuê", party_a_line, flags=re.IGNORECASE):
        mapping_warnings.append(
            "Tài liệu mô tả Bên A như khách hàng, khác quy ước hệ thống Bên A = Công ty."
        )
    if re.search(
        r"nhà cung cấp|vendor|developer|bên cung cấp",
        party_b_line,
        flags=re.IGNORECASE,
    ):
        mapping_warnings.append(
            "Tài liệu mô tả Bên B như nhà cung cấp, khác quy ước hệ thống Bên B = Khách hàng."
        )

    return {
        "contract_title": title,
        "party_a": company_name or "Công ty",
        "party_b": customer_name or "Khách hàng",
        "party_a_role": "COMPANY",
        "party_b_role": "CUSTOMER",
        "party_a_label": "Bên A · Công ty",
        "party_b_label": "Bên B · Khách hàng",
        "party_a_source": "DOCUMENT" if explicit_party_a or company_name else "SYSTEM_DEFAULT",
        "party_b_source": "DOCUMENT" if explicit_party_b or customer_name else "SYSTEM_DEFAULT",
        "party_mapping_source": (
            "DOCUMENT"
            if explicit_party_a and explicit_party_b
            else "DOCUMENT_AND_SYSTEM_DEFAULT"
            if explicit_party_a or explicit_party_b or company_name or customer_name
            else "SYSTEM_DEFAULT"
        ),
        "party_mapping_warnings": mapping_warnings,
        "contract_value": amounts[0] if amounts else None,
        "amounts": amounts,
        "dates": dates,
        "start_date": dates[0] if dates else None,
        "end_date": dates[1] if len(dates) > 1 else None,
        "term": term,
        "payment_terms": payment_terms[:4],
        "clause_count": len(clauses),
    }
