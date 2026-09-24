"""Deterministic legal document analysis used by the Legal Agent tools.

The service intentionally returns explainable findings. An LLM may add context, but
severity, evidence and the risk score remain traceable to the source text.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

from app.domains.legal.contract_review import review_contract


SEVERITY_WEIGHT = {"HIGH": 25, "MEDIUM": 12, "LOW": 5}


def _compact(value: str, limit: int = 220) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _evidence(text: str, match: re.Match[str], radius: int = 100) -> str:
    return _compact(text[max(0, match.start() - radius) : match.end() + radius])


def _finding(
    clause: str,
    severity: str,
    recommendation: str,
    evidence: str,
    category: str,
) -> dict[str, str]:
    return {
        "clause": clause,
        "severity": severity,
        "recommendation": recommendation,
        "evidence": evidence,
        "category": category,
    }


def _first_match(text: str, patterns: list[str]) -> re.Match[str] | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match
    return None


def extract_contract_metadata(text: str) -> dict[str, Any]:
    dates = list(dict.fromkeys(re.findall(
        r"\b(?:0?[1-9]|[12]\d|3[01])[/-](?:0?[1-9]|1[0-2])[/-](?:19|20)\d{2}\b",
        text,
    )))[:8]
    amounts = list(dict.fromkeys(re.findall(
        r"\b\d[\d.,\s]{2,}\s*(?:VND|VNĐ|USD|EUR|đồng)\b",
        text,
        flags=re.IGNORECASE,
    )))[:8]
    payment = _first_match(text, [
        r"(?:payment|thanh toán).{0,180}(?:\d+\s*(?:days?|ngày)|milestone|đợt)",
        r"(?:\d+\s*(?:days?|ngày)).{0,100}(?:invoice|hóa đơn)",
    ])
    expiry = _first_match(text, [
        r"(?:expiry|expiration|hết hạn|đến ngày).{0,50}",
        r"(?:term|thời hạn).{0,100}",
    ])
    return {
        "dates": dates,
        "amounts": [_compact(value, 80) for value in amounts],
        "payment_terms": _evidence(text, payment) if payment else None,
        "expiry_clause": _evidence(text, expiry) if expiry else None,
    }


def audit_contract_text(
    contract_text: str,
    document_name: str = "Contract.pdf",
    represented_party: str = "NEUTRAL",
    document_scope: str = "FULL",
) -> dict[str, Any]:
    """Compatibility wrapper around the independent contract-review module.

    The perspective is a parameter because it changes severity: the same clause is
    HIGH for whoever carries the obligation and LOW for the other side. Callers that
    know which party they act for must say so, or the score they show is not the
    score that applies to them.

    This used to attach a `docx_download_url` pointing at a random id that was never
    stored -- a download link that could only ever 404. A redline is now reachable
    only through a saved review, at /legal/contract-reviews/{id}/redline.
    """
    return review_contract(
        contract_text, document_name, represented_party, document_scope=document_scope
    )


def detect_sensitive_data(text: str, headers: list[str] | None = None) -> dict[str, Any]:
    """Detect common personal and restricted-data indicators without returning values."""
    source = text or ""
    normalized_headers = " ".join(headers or []).lower()
    detectors = {
        "EMAIL": r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "PHONE": r"(?<!\d)(?:\+?84|0)(?:\d[ .-]?){8,10}(?!\d)",
        "PASSPORT": r"\b[A-Z][0-9]{7,8}\b",
        "CCCD": r"(?<!\d)\d{12}(?!\d)",
        "IP_ADDRESS": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    }
    header_aliases = {
        "EMAIL": ("email", "e-mail"),
        "PHONE": ("phone", "mobile", "điện thoại", "sđt"),
        "PASSPORT": ("passport", "hộ chiếu"),
        "CCCD": ("cccd", "cmnd", "citizen id"),
        "ADDRESS": ("address", "địa chỉ"),
        "SALARY": ("salary", "lương", "thu nhập"),
    }
    findings: list[dict[str, Any]] = []
    for data_type, pattern in detectors.items():
        count = len(re.findall(pattern, source, flags=re.IGNORECASE))
        if count:
            findings.append({"type": data_type, "count": count, "severity": "HIGH" if data_type in {"PASSPORT", "CCCD"} else "MEDIUM"})
    found_types = {item["type"] for item in findings}
    for data_type, aliases in header_aliases.items():
        if data_type not in found_types and any(alias in normalized_headers for alias in aliases):
            findings.append({"type": data_type, "count": None, "severity": "HIGH" if data_type in {"PASSPORT", "CCCD", "SALARY"} else "MEDIUM"})
    high = any(item["severity"] == "HIGH" for item in findings)
    return {
        "contains_sensitive_data": bool(findings),
        "requires_legal_approval": high or len(findings) >= 2,
        "risk_level": "HIGH" if high else "MEDIUM" if findings else "LOW",
        "findings": findings,
        "frameworks": ["Internal Privacy Policy", "Vietnam PDPL (Decree 13/2023/ND-CP)"] if findings else [],
        "suggested_action": "Do not share externally. Minimize or redact data and request Legal approval." if findings else "No common sensitive-data indicators detected.",
    }


def compare_contract_texts(old_text: str, new_text: str) -> dict[str, Any]:
    """Return readable clause/line changes for two contract versions."""
    old_lines = [_compact(line, 500) for line in old_text.splitlines() if line.strip()]
    new_lines = [_compact(line, 500) for line in new_text.splitlines() if line.strip()]
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    changes: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changes.append({
            "type": {"replace": "MODIFIED", "delete": "REMOVED", "insert": "ADDED"}[tag],
            "old": old_lines[i1:i2],
            "new": new_lines[j1:j2],
            "old_location": i1 + 1 if i1 < len(old_lines) else None,
            "new_location": j1 + 1 if j1 < len(new_lines) else None,
        })
        if len(changes) >= 50:
            break
    ratio = matcher.ratio()
    return {
        "similarity_percent": round(ratio * 100, 1),
        "total_changes": len(changes),
        "changes": changes,
    }


def check_software_licenses(filename: str, text: str) -> dict[str, Any]:
    """Inspect common dependency manifests and flag reciprocal licenses."""
    dependencies: list[str] = []
    lower_name = filename.lower()
    if lower_name.endswith("package.json") or lower_name.endswith(".json"):
        try:
            payload = json.loads(text)
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                if isinstance(payload.get(key), dict):
                    dependencies.extend(str(name) for name in payload[key])
        except (json.JSONDecodeError, TypeError):
            pass
    elif "requirements" in lower_name:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-")):
                dependencies.append(re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip())

    known = {
        "ultralytics": ("AGPL-3.0", "HIGH", "Commercial distribution or network use may require an enterprise license."),
        "yolov8": ("AGPL-3.0", "HIGH", "Commercial use may require an Ultralytics enterprise license."),
        "react": ("MIT", "LOW", "Retain the copyright and license notice."),
        "next": ("MIT", "LOW", "Retain the copyright and license notice."),
        "fastapi": ("MIT", "LOW", "Retain the copyright and license notice."),
        "pypdf": ("BSD-3-Clause", "LOW", "Retain the copyright and license notice."),
    }
    findings: list[dict[str, str]] = []
    for dependency in sorted(set(dependencies), key=str.lower):
        details = known.get(dependency.lower())
        if details:
            license_name, severity, action = details
            findings.append({"package": dependency, "license": license_name, "severity": severity, "action": action})

    declared_patterns = [
        ("AGPL", "AGPL-3.0", "HIGH"),
        ("GPL", "GPL", "HIGH"),
        ("Apache", "Apache-2.0", "LOW"),
        ("MIT", "MIT", "LOW"),
        ("BSD", "BSD", "LOW"),
    ]
    for marker, license_name, severity in declared_patterns:
        if re.search(rf"\b{re.escape(marker)}(?:[- ]?\d(?:\.\d)?)?\b", text, flags=re.IGNORECASE) and not any(item["license"] == license_name for item in findings):
            findings.append({
                "package": "Manifest declaration",
                "license": license_name,
                "severity": severity,
                "action": "Legal review is required before commercial distribution." if severity == "HIGH" else "Retain the license and attribution notices.",
            })
    high = any(item["severity"] == "HIGH" for item in findings)
    return {
        "manifest": filename,
        "dependencies_scanned": len(set(dependencies)),
        "risk_level": "HIGH" if high else "LOW",
        "commercial_use_requires_review": high,
        "findings": findings,
        "unresolved_dependencies": max(0, len(set(dependencies)) - len([item for item in findings if item["package"] != "Manifest declaration"])),
    }
