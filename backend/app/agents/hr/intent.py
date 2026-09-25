"""Keyword reading of an HR turn: the fallback when the LLM router cannot be used."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session
from app.models.models import User
from app.domains.hr.hr_employee_tools import list_tenant_departments
from app.agents.text import _normalize_intent_text


# Phrases that make a turn a question about how something works, wherever it appears.
# Shared by the intent classifier and by the leave-draft guard so the two cannot drift:
# a phrasing the classifier treats as a policy question must never be recorded as the
# reason on an open leave draft.
_INFORMATIONAL_MARKERS = (
    "bao nhieu",
    "cach ",
    "can gi",
    "can nhung",
    "chinh sach",
    "co can",
    "co duoc",
    "dieu kien",
    "huong dan",
    "la gi",
    "lam sao",
    "may ngay",
    "nhu the nao",
    "quy dinh",
    "quy trinh",
    "ra sao",
    "the nao",
    "thu tuc",
    "yeu cau gi",
)


def _is_informational_message(message: str) -> bool:
    """Return whether the turn reads as a question rather than a slot-filling answer."""
    normalized = _normalize_intent_text(message)
    return "?" in message or any(
        marker in normalized for marker in _INFORMATIONAL_MARKERS
    )


def _classify_hr_intent(message: str) -> str:
    """Classify HR intent from normalized action and entity markers."""
    normalized = _normalize_intent_text(message)
    if any(term in normalized for term in (
        "con bao nhieu ngay phep",
        "so ngay phep",
        "phep con lai",
        "quy phep",
    )):
        return "QUERY_LEAVE_BALANCE"
    if any(term in normalized for term in (
        "xuat file",
        "xuat danh sach",
        "xuat du lieu",
        "trich xuat",
        "export file",
        "export ",
        "tai file",
        "tai xuong",
        "xuat bao cao",
    )):
        return "ACTION_EXPORT"

    if any(marker in normalized for marker in (
        "tao onboarding",
        "khoi tao onboarding",
        "onboard ",
    )):
        return "ACTION_ONBOARDING"

    if any(marker in normalized for marker in (
        "ho so day du",
        "toan bo ho so",
        "full profile",
    )):
        return "FULL_PROFILE"
    if any(marker in normalized for marker in (
        "luong cua toi",
        "muc luong cua toi",
        "thu nhap cua toi",
    )):
        return "SELF_COMPENSATION"
    if any(marker in normalized for marker in (
        "thong tin ca nhan cua toi",
        "ho so rieng tu cua toi",
    )):
        return "SELF_PRIVATE_PROFILE"
    if any(marker in normalized for marker in (
        "ho so cua toi",
        "thong tin nhan su cua toi",
        "thong tin cua toi",
        "thong tin cua minh",
    )):
        return "SELF_PROFILE"
    if any(marker in normalized for marker in (
        "hop dong cua toi",
        "thu viec cua toi",
    )):
        return "SELF_CONTRACT"
    if any(marker in normalized for marker in (
        "hop dong sap het han",
        "hop dong gan het han",
    )):
        return "CONTRACT_EXPIRY"
    if any(marker in normalized for marker in (
        "don cho duyet",
        "yeu cau cho duyet",
        "phe duyet dang cho",
    )):
        return "PENDING_APPROVALS"

    count_markers = (
        "bao nhieu",
        "co may",
        "so luong",
        "tong so",
    )
    directory_markers = count_markers + (
        "danh sach",
        "liet ke",
        "tat ca",
        "tim cac",
        "tim nhung",
        "tim tat ca",
        "xem cac",
    )
    employee_entity = any(marker in normalized for marker in (
        "nhan vien",
        "nhan su",
        "employee",
    ))
    # "Quản lý" is the job the default tree ships with, but a company staffs the same
    # layer with titles of its own, and someone asking for the directors means the people
    # who run the place -- not the one position whose slug happens to be `manager`.
    manager_entity = any(marker in normalized for marker in (
        "quan ly",
        "manager",
        "giam doc",
        "ban giam doc",
        "lanh dao",
        "truong phong",
    ))
    leave_context = "nghi" in normalized and "phep" in normalized
    names_a_department = any(
        marker in f"{normalized} " for marker in _DEPARTMENT_MENTION_MARKERS
    )

    if employee_entity and leave_context and any(marker in normalized for marker in count_markers):
        return "EMPLOYEE_LEAVE_STATUS_COUNT"
    if manager_entity and (
        any(marker in normalized for marker in directory_markers)
        or normalized in {"tim quan ly", "xem quan ly", "quan ly"}
    ):
        return "MANAGER_DIRECTORY"
    if employee_entity and any(marker in normalized for marker in directory_markers):
        return "EMPLOYEE_DIRECTORY"
    # Naming a department asks about a group, however the sentence is phrased: "thông tin
    # nhân viên phòng IT" wants the IT list, not an employee whose name is "phòng IT".
    if employee_entity and names_a_department:
        return "EMPLOYEE_DIRECTORY"
    if any(marker in normalized for marker in _EMPLOYEE_SEARCH_PREFIXES + ("ho so cua ",)):
        return "EMPLOYEE_SEARCH"

    if leave_context and any(marker in normalized for marker in _INFORMATIONAL_MARKERS):
        return "POLICY_QUERY"

    leave_action_markers = (
        "toi muon xin nghi",
        "toi muon nghi phep",
        "toi xin nghi",
        "cho toi xin nghi",
        "xin nghi phep",
        "xin phep nghi",
        "tao don nghi",
        "gui don nghi",
        "nop don nghi",
        "dang ky nghi",
        "cho toi nghi",
    )
    if any(marker in normalized for marker in leave_action_markers):
        return "ACTION_LEAVE_REQUEST"
    if any(marker in normalized for marker in _INFORMATIONAL_MARKERS):
        return "POLICY_QUERY"
    return "UNKNOWN"


_LEAVE_BALANCE_MARKERS = (
    "con bao nhieu ngay phep",
    "so ngay phep",
    "phep con lai",
    "quy phep",
)


# Words that can surround a leave-balance question without naming anyone: polite lead-ins,
# first-person pronouns, role words that only qualify a following name, and question
# tails. Whatever is left after removing them is a subject.
_SUBJECT_NOISE = frozenset({
    # lead-ins
    "a", "ah", "biet", "cho", "hay", "hoi", "kiem", "lam", "long", "oi", "on",
    "tra", "vui", "xem",
    # first person
    "em", "minh", "t", "toi", "tui",
    # role words that qualify a name rather than being one
    "anh", "ba", "bac", "ban", "chi", "chu", "co", "nhan", "ong", "su", "vien",
    # question tails
    "bao", "con", "gi", "ha", "khong", "la", "lai", "nao", "nhieu", "roi", "the", "va",
})


def _has_named_subject(segment: str) -> bool:
    return any(token not in _SUBJECT_NOISE for token in segment.split())


def _leave_balance_names_another_person(message: str) -> bool:
    """Return whether a leave-balance question is aimed at somebody other than the asker.

    ``query_leave_balance`` only ever reads the requester's own quota, so a question
    about a colleague must be refused rather than answered with the requester's figures
    under the colleague's name. Vietnamese puts the subject on either side of the
    phrase — "An còn bao nhiêu ngày phép" and "quỹ phép của An" — so both are checked.
    """
    if re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", message):
        return True
    normalized = _normalize_intent_text(message)
    for marker in _LEAVE_BALANCE_MARKERS:
        head, separator, tail = normalized.partition(marker)
        if not separator:
            continue
        if _has_named_subject(head):
            return True
        # Only a possessive tail names an owner; "... là bao nhiêu" names nobody.
        _before, possessive, owner = tail.partition("cua ")
        return bool(possessive) and _has_named_subject(owner)
    return False


def _parse_hr_export_request(message: str) -> tuple[str | None, str | None]:
    normalized = _normalize_intent_text(message)
    export_format: str | None = None
    if "pdf" in normalized:
        export_format = "pdf"
    elif any(marker in normalized for marker in ("excel", "xlsx")):
        export_format = "xlsx"
    elif "json" in normalized:
        export_format = "json"

    directory_type: str | None = None
    if any(marker in normalized for marker in ("quan ly", "manager")):
        directory_type = "managers"
    elif any(marker in normalized for marker in ("nhan vien", "nhan su", "employee")):
        directory_type = "employees"
    return export_format, directory_type


# Longest first, so "xem thong tin nhan vien" is not truncated by "thong tin nhan vien"
# and left with a stray "nhan vien" in the search term.
_EMPLOYEE_SEARCH_PREFIXES = (
    "xem thong tin nhan vien",
    "xem thong tin cua",
    "tra cuu nhan vien",
    "chi tiet nhan vien",
    "thong tin nhan vien",
    "ho so nhan vien",
    "xem ho so cua",
    "tim nhan vien",
    "thong tin cua",
    "tim ho so",
    "ho so cua",
)


# What sits between "nhân viên" and the identifier: "nhân viên số 40", "nhân viên mã 40".
_EMPLOYEE_IDENTIFIER_LEADINS = ("so", "ma", "id", "#")


# "thông tin của tôi" is a self-service request that the router may still label a search.
# Searching the directory for the word "tôi" would be nonsense, so it names nobody.
_FIRST_PERSON_TERMS = frozenset({"toi", "minh", "em", "tui", "ban than toi"})


def _extract_employee_search_term(message: str) -> str:
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", message)
    if email_match:
        return email_match.group(0)

    normalized = _normalize_intent_text(message)
    original_words = message.strip().split()
    for prefix in _EMPLOYEE_SEARCH_PREFIXES:
        if normalized == prefix:
            return ""
        if not normalized.startswith(f"{prefix} "):
            continue
        remainder = original_words[len(prefix.split()):]
        # "nhân viên số 40" identifies employee 40, not an employee called "số 40".
        while remainder and _normalize_intent_text(remainder[0]).strip("#") in _EMPLOYEE_IDENTIFIER_LEADINS:
            remainder = remainder[1:]
        term = " ".join(remainder).strip(" .?!")
        if _normalize_intent_text(term) in _FIRST_PERSON_TERMS:
            return ""
        return term
    return ""


# Phrasings that announce a department is being named. Used both to spot the department
# in the sentence and to tell "the department I asked for does not exist here" apart from
# "no department was mentioned at all".
_DEPARTMENT_MENTION_MARKERS = ("phong ", "bo phan ", "department ", "phong ban ")


_DEPARTMENT_LEADIN = r"(?:phong ban|phong|bo phan|department)\s+"


def _department_aliases(code: str, name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """How this department can be named: freely, and only after the word "phòng".

    A code ("IT") and a full name ("Phòng tuyển dụng") are specific enough to recognise
    anywhere in a sentence. The name with its leading "Phòng" removed is not: a company
    that calls a department "Phòng Quản lý" would otherwise turn every request for the
    managers into a request for that one department.
    """
    normalized_name = _normalize_intent_text(name)
    anywhere = {_normalize_intent_text(code), normalized_name}
    after_leadin = set(anywhere)
    for marker in _DEPARTMENT_MENTION_MARKERS:
        if normalized_name.startswith(marker):
            after_leadin.add(normalized_name[len(marker):])
    return (
        tuple(alias for alias in anywhere if alias),
        tuple(alias for alias in after_leadin if alias),
    )


def _resolve_requested_departments(
    db: Session, user: User, message: str
) -> tuple[tuple[str, ...], bool]:
    """Department codes named in the question, and whether one was named at all.

    The second value is what separates an unfiltered directory request from a request
    for a department this company does not have. Without it, "nhân viên phòng kế toán"
    would silently fall back to listing the whole company under a heading that says
    otherwise.
    """
    normalized = _normalize_intent_text(message)
    matched: list[str] = []
    for code, name in list_tenant_departments(db, actor=user):
        anywhere, after_leadin = _department_aliases(code, name)
        patterns = [rf"(?<!\w){re.escape(alias)}(?!\w)" for alias in anywhere]
        patterns += [
            rf"(?<!\w){_DEPARTMENT_LEADIN}{re.escape(alias)}(?!\w)"
            for alias in after_leadin
        ]
        if any(re.search(pattern, normalized) for pattern in patterns):
            matched.append(code)
    mentions_department = any(
        marker in f"{normalized} " for marker in _DEPARTMENT_MENTION_MARKERS
    )
    return tuple(dict.fromkeys(matched)), mentions_department


def _unknown_department_reply(db: Session, user: User) -> str:
    known = list_tenant_departments(db, actor=user)
    if not known:
        return "Công ty chưa khai báo phòng ban nào nên tôi không lọc theo phòng ban được."
    listed = ", ".join(f"**{name}** (`{code}`)" for code, name in known)
    return (
        "Tôi không tìm thấy phòng ban bạn hỏi trong công ty. "
        f"Các phòng ban hiện có: {listed}."
    )


def _department_filter_label(db: Session, user: User, codes: tuple[str, ...]) -> str:
    names = dict(list_tenant_departments(db, actor=user))
    return ", ".join(f"**{names.get(code, code)}**" for code in codes)
