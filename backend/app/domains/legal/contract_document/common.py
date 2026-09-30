"""What the DOCX and PDF editors share: which revisions to write, and where they go.

A reviewer's accepted revision is written *into the uploaded file*, so it has to land on
the clause it was written for and nowhere else. The review knows its clauses by the text
it read (``result["clauses"]``: number, title and text, in order); the file knows its
blocks (a Word paragraph, a PDF line). A clause is found in the file by its first line --
the article heading -- and runs until the next clause's heading.

Every revision is a whole clause as it should read (the review's REVISE step writes it
that way), so it replaces the clause *body* and keeps the document's own heading, whose
formatting and numbering are the form this module exists to protect.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.domains.legal.contract_redline import ACCEPTED_DECISIONS, applied_revision


class ContractEditError(ValueError):
    """The file cannot be edited at all: unreadable, encrypted, or its form would break."""


# A signature block ends the last article: an inserted article goes before it, and the
# last article's body must not swallow it.
_SIGNATURE = re.compile(
    r"^(?:đại diện|đại điện)\s*(?:bên|cho bên)?|^bên [ab]\s*$|^(?:chữ ký|ký tên|ký,? ghi rõ)"
    r"|^(?:for and on behalf|signed by|in witness)",
    re.IGNORECASE,
)
_ARTICLE_PREFIX = re.compile(r"^\s*(?:điều|article|clause|section)\s+(\d+(?:\.\d+)*)\s*[.:\-)]?\s*", re.IGNORECASE)
# A drafted clause often leaves its number to the drafter: "Điều [Số].", "Điều X.", "Điều ...".
_PLACEHOLDER_ARTICLE = re.compile(
    r"^\s*(?:điều|article|clause|section)\s+(?:\[[^\]]{0,12}\]|x{1,3}|n|\.{2,}|…|_+)\s*[.:\-)]?\s*",
    re.IGNORECASE,
)
# Points written inline -- "... thống nhất. 2. Mọi yêu cầu ..." -- once line breaks are lost.
_INLINE_POINT = re.compile(r"(?<=[.;:])\s+(?=(?:\d{1,2}(?:\.\d{1,2})?|[a-zđ])[.)]\s+\S)")
# A list marker a line brings with it: "a)", "1.", "1.2.", "-", "•".
LIST_MARKER = re.compile(r"^\s*(?:[a-zđ][.)]|\d+(?:\.\d+)*[.)]|[-•+*–])\s+", re.IGNORECASE)


def normalize(text: str) -> str:
    """Text as compared across the review and the file: NFC, casefolded, one space."""
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"^#{1,6}\s+", "", text.strip())
    text = text.replace("**", "").replace("|", " ")
    return re.sub(r"\s+", " ", text).strip().casefold()


def squash(text: str) -> str:
    """Text with no whitespace at all: a PDF may place a space by position, not by glyph."""
    return re.sub(r"\s+", "", normalize(text))


def is_signature(text: str) -> bool:
    return bool(_SIGNATURE.match(normalize(text)))


def article_number(text: str) -> str | None:
    match = _ARTICLE_PREFIX.match(unicodedata.normalize("NFC", text or ""))
    return match.group(1) if match else None


def strip_article_prefix(text: str) -> str:
    return _ARTICLE_PREFIX.sub("", unicodedata.normalize("NFC", text or ""), count=1).strip()


def decisions_fingerprint(decisions: list[dict[str, Any]]) -> str:
    """Identifies the accepted wording a revised file was built from; any change is a new file."""
    material = sorted(
        (str(item.get("finding_key")), str(item.get("decision")), str(item.get("revised_text") or ""))
        for item in decisions
        if item.get("decision") in ACCEPTED_DECISIONS
    )
    return hashlib.sha256(json.dumps(material, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]


# --------------------------------------------------------------------------- planning


@dataclass
class Edit:
    finding_key: str
    kind: str  # "REPLACE" a clause body, or "INSERT" a missing clause
    text: str
    decision: str
    issue: str
    clause_id: str | None = None
    clause_number: str | None = None
    clause_title: str | None = None
    category: str | None = None
    notes: list[str] = field(default_factory=list)


def _checklist_label(result: dict[str, Any], category: str | None) -> str | None:
    for item in result.get("checklist") or []:
        if item.get("category") == category and item.get("label"):
            return str(item["label"])
    return None


def plan_edits(result: dict[str, Any], decisions: list[dict[str, Any]]) -> tuple[list[Edit], list[dict[str, str]]]:
    """The accepted revisions to write, one per clause, and why the others are left out.

    Two accepted findings on one clause are two rewrites of the same text; writing both
    would keep only the last. The reviewer's own wording (EDITED) wins, then the more
    severe finding -- the order the review already sorted them in.
    """
    by_key = {str(item.get("finding_key")): item for item in decisions}
    chosen: dict[str, Edit] = {}
    edits: list[Edit] = []
    skipped: list[dict[str, str]] = []

    candidates = []
    for finding in result.get("findings") or []:
        decision = by_key.get(str(finding.get("finding_key")))
        if not decision or decision.get("decision") not in ACCEPTED_DECISIONS:
            continue
        candidates.append((finding, decision))
    # EDITED first, keeping the review's severity order within each group.
    candidates.sort(key=lambda pair: pair[1].get("decision") != "EDITED")

    for finding, decision in candidates:
        key = str(finding.get("finding_key"))
        clause_label = f"Điều {finding.get('clause')}" if finding.get("clause") not in (None, "MISSING") else "Điều khoản thiếu"
        text = applied_revision(finding, decision)
        if not text:
            skipped.append({"finding_key": key, "clause": clause_label, "reason": "Đề xuất không có nội dung để ghi vào văn bản."})
            continue
        clause_id = finding.get("clause_id")
        if finding.get("clause") == "MISSING" or not clause_id:
            edits.append(Edit(
                finding_key=key, kind="INSERT", text=text, decision=str(decision.get("decision")),
                issue=str(finding.get("issue") or ""), category=finding.get("category"),
                clause_title=_checklist_label(result, finding.get("category")),
            ))
            continue
        if clause_id == "preamble" or finding.get("category") == "PARTIES":
            skipped.append({
                "finding_key": key, "clause": clause_label,
                "reason": "Phần mở đầu và thông tin các bên không được sửa tự động; hãy sửa tay trên file.",
            })
            continue
        if clause_id in chosen:
            skipped.append({
                "finding_key": key, "clause": clause_label,
                "reason": (
                    f"{clause_label} đã được viết lại theo đề xuất \"{chosen[clause_id].issue[:80]}\"; "
                    "hai đề xuất cùng một điều không ghi chồng lên nhau. Hãy gộp nội dung bằng Edit."
                ),
            })
            continue
        edit = Edit(
            finding_key=key, kind="REPLACE", text=text, decision=str(decision.get("decision")),
            issue=str(finding.get("issue") or ""), clause_id=str(clause_id),
            clause_number=str(finding.get("clause") or ""), clause_title=finding.get("clause_title"),
            category=finding.get("category"),
        )
        if finding.get("finding_type") == "INTERNAL_CONFLICT":
            edit.notes.append(f"Mâu thuẫn giữa nhiều điều: chỉ {clause_label} được viết lại; hãy kiểm tra điều còn lại.")
        chosen[str(clause_id)] = edit
        edits.append(edit)
    return edits, skipped


# --------------------------------------------------------------------------- revision text


def _without_number(line: str) -> str:
    return _PLACEHOLDER_ARTICLE.sub("", strip_article_prefix(line), count=1).strip()


def _drop_title(text: str, title: str) -> str:
    """``text`` without ``title`` in front of it, the way a heading leads into its clause."""
    words = text.split()
    count = len(title.split())
    if count and normalize(" ".join(words[:count])).rstrip(" .:-–") == normalize(title).rstrip(" .:-–"):
        return " ".join(words[count:]).lstrip(" .:-–")
    return text


def _split_points(line: str) -> list[str]:
    """One line holding several points ("1. ... 2. ...") as one line per point.

    The review used to flatten a revision's line breaks, so a clause the model wrote as
    points arrives as one run of text; written as one paragraph it read as a wall.
    """
    parts = [part.strip() for part in _INLINE_POINT.split(line)]
    # A bare title in front of the first point ("Phạm vi công việc 1. Bên B ...") is
    # split off; a sentence leading into it stays whole.
    first = re.match(r"^(.{2,80}?)\s+((?:1|a)[.)]\s+\S.*)$", parts[0])
    if first and not re.search(r"[.;:]$", first.group(1)) and len(first.group(1).split()) <= 10:
        parts[0:1] = [first.group(1), first.group(2)]
    return [part for part in parts if part]


def revision_lines(edit: Edit) -> list[str]:
    """The revision as the paragraphs to write, without a heading the document already has."""
    lines = [
        re.sub(r"\s+", " ", unicodedata.normalize("NFC", line).replace("**", "")).strip()
        for line in edit.text.replace("\r\n", "\n").split("\n")
    ]
    lines = [re.sub(r"^#{1,6}\s+", "", line) for line in lines if line]
    if lines and edit.kind == "REPLACE":
        first = lines[0]
        number = article_number(first)
        if (number is not None and number == (edit.clause_number or "").strip()) or _PLACEHOLDER_ARTICLE.match(first):
            first = _without_number(first)
        # The document keeps its own heading, so a title the revision repeats goes --
        # "Điều 3. Phạt vi phạm" alone, or leading into the text on the same line.
        if edit.clause_title:
            first = _drop_title(first, edit.clause_title)
        lines = [first, *lines[1:]] if first else lines[1:]
    out: list[str] = []
    for line in lines:
        out.extend(_split_points(line))
    return out


def insert_heading(edit: Edit) -> tuple[str, list[str]]:
    """Title and body of a missing clause to add."""
    lines = revision_lines(edit)
    title = edit.clause_title or ""
    if lines:
        first = lines[0]
        numbered = article_number(first) is not None or bool(_PLACEHOLDER_ARTICLE.match(first))
        candidate = _without_number(first)
        looks_like_heading = numbered or (
            len(candidate.split()) <= 12 and not candidate.rstrip().endswith((".", ";", ":")) and len(lines) > 1
        )
        if looks_like_heading and candidate and len(candidate.split()) <= 12:
            title, lines = candidate.rstrip(" .:"), lines[1:]
        elif numbered and candidate:
            lines[0] = candidate
    if not title:
        issue = re.sub(
            r"^(?:hợp đồng\s+)?(?:thiếu|không có|chưa có)\s+(?:điều khoản|quy định)?\s*(?:về)?\s*[:\-]?\s*",
            "", edit.issue, flags=re.IGNORECASE,
        )
        title = issue[:80].strip(" .:") or "Điều khoản bổ sung"
    return title[:1].upper() + title[1:], lines


# --------------------------------------------------------------------------- locating


def locate_clauses(
    block_texts: list[str],
    clauses: list[dict[str, Any]],
    *,
    squash_spaces: bool = False,
) -> dict[str, tuple[int, int]]:
    """Each clause's block range [start, end) in the file, found by its first line.

    Clauses are searched in order from where the previous one was found, so a heading
    repeated in a table of contents or a cross-reference cannot pull a clause back up. The
    last clause stops at the signature block.
    """
    norm = squash if squash_spaces else normalize
    blocks = [norm(text) for text in block_texts]
    starts: list[tuple[str, int]] = []
    cursor = 0
    for clause in clauses:
        first_line = next((line for line in str(clause.get("text") or "").split("\n") if line.strip()), "")
        head = norm(first_line)
        if len(head) < 4:
            continue
        probe = head[:80]
        for index in range(cursor, len(blocks)):
            block = blocks[index]
            if not block:
                continue
            if block.startswith(probe) or (len(block) >= 8 and head.startswith(block)):
                starts.append((str(clause.get("id")), index))
                cursor = index + 1
                break
    ranges: dict[str, tuple[int, int]] = {}
    for position, (clause_id, start) in enumerate(starts):
        if position + 1 < len(starts):
            end = starts[position + 1][1]
        else:
            end = next(
                (index for index in range(start + 1, len(block_texts)) if is_signature(block_texts[index])),
                len(block_texts),
            )
        ranges[clause_id] = (start, end)
    return ranges
