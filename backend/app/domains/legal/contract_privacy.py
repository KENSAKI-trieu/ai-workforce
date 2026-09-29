"""Stand-ins for personal data before contract text goes to a model provider.

The review and the translation send the whole contract to the provider, which had the
employee's name, ID card number, phone and home address along with everything else. A
review does not need to know who a person is, only that "[NGƯỜI_1]" is the employee, so
personal data is swapped for numbered stand-ins before the call and swapped back in what
comes back.

Only data about people is replaced. Company names, tax codes, amounts, dates and
salaries stay: the review turns on them (a probation wage of 70%, a 1.850.000.000 đồng
contract signed by the wrong officer), and they are not personal data. A birth date
stays too -- it decides whether a worker is a minor -- and without the name next to it
it does not say who the worker is.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


def _latin(test) -> str:
    return "".join(
        char for char in map(chr, range(0x41, 0x1F00))
        if test(char) and "LATIN" in unicodedata.name(char, "")
    )


# Latin letters with Vietnamese marks. A range such as À-Ỹ also takes in lower-case "đ",
# which made "đồng" look like the start of a name.
_UPPER, _LOWER = _latin(str.isupper), _latin(str.islower)
_NAME_WORD = rf"[{_UPPER}][{_LOWER}]+"
_CAPS_WORD = rf"[{_UPPER}]{{1,7}}"
_PERSON_NAME = rf"{_NAME_WORD}(?:[ \t]+{_NAME_WORD}){{1,4}}"
# After a label that introduces a person, a name may be written in capitals.
_LABELLED_NAME = rf"(?:{_PERSON_NAME}|(?!CÔNG\b){_CAPS_WORD}(?:[ \t]+{_CAPS_WORD}){{1,4}})"
_LABEL_END = r"\s*[:：]\s*"

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    # Labelled identity numbers: CCCD/CMND (9 or 12 digits) and passports.
    ("CCCD", re.compile(
        r"(?i)(?:(?:số\s*)?(?:cccd|cmnd|căn\s*cước(?:\s*công\s*dân)?|chứng\s*minh\s*(?:nhân\s*dân|thư)|số\s*định\s*danh(?:\s*cá\s*nhân)?)"
        r"\s*(?:số)?\s*[:：]?\s*)(?P<value>\d{9}(?:\d{3})?)(?!\d)"
    )),
    ("HỘ_CHIẾU", re.compile(r"(?i)(?:hộ\s*chiếu|passport)(?:\s*(?:số|no\.?))?\s*[:：]?\s*(?P<value>[A-Z]\d{7,8})\b")),
    ("STK", re.compile(
        r"(?i)(?:số\s*tài\s*khoản|stk|tài\s*khoản\s*số|account\s*(?:no\.?|number))\s*[:：]?\s*(?P<value>\d[\d .-]{6,22}\d)"
    )),
    ("SĐT", re.compile(
        r"(?<![\d+])(?:(?:\+84|84|0)[ .-]?(?:3[2-9]|5[2689]|7[06-9]|8[1-9]|9\d)(?:[ .-]?\d){7}"
        r"|0[ .-]?2(?:[ .-]?\d){9})(?!\d)"
    )),
    # A person's home, not a company's office: only the labels that mean residence.
    ("ĐỊA_CHỈ", re.compile(
        r"(?i)(?:địa\s*chỉ\s*thường\s*trú|thường\s*trú(?:\s*tại)?|hộ\s*khẩu(?:\s*thường\s*trú)?|nơi\s*(?:cư\s*trú|ở\s*hiện\s*(?:tại|nay))"
        r"|chỗ\s*ở\s*hiện\s*(?:tại|nay)|địa\s*chỉ\s*liên\s*hệ|home\s*address)"
        rf"{_LABEL_END}(?P<value>[^\n;]{{4,160}})"
    )),
    # A person: after an honorific or a label that introduces an individual.
    ("NGƯỜI", re.compile(
        r"(?:\b(?:Ông|Bà|Anh|Chị|Ông/Bà|Mr\.?|Mrs\.?|Ms\.?)[ \t]+)(?P<value>" + _PERSON_NAME + r")"
    )),
    ("NGƯỜI", re.compile(
        r"(?i:người\s*lao\s*động|họ\s*(?:và\s*)?tên|người\s*đại\s*diện|đại\s*diện(?:\s*bởi)?|người\s*nhận"
        r"|người\s*được\s*ủy\s*quyền|thực\s*tập\s*sinh|freelancer|employee(?:\s*name)?|full\s*name)"
        + _LABEL_END + r"(?:(?:Ông|Bà|Mr\.?|Mrs\.?|Ms\.?)[ \t]+)?(?P<value>" + _LABELLED_NAME + r")(?![ \t]*(?:TNHH|CP|JSC|LLC|Ltd))"
    )),
)
_STAND_IN = re.compile(r"\[(?:EMAIL|CCCD|HỘ_CHIẾU|STK|SĐT|ĐỊA_CHỈ|NGƯỜI)_\d+\]")


@dataclass
class Pseudonymizer:
    """Swaps personal data for stand-ins, the same one each time a value recurs."""

    originals: dict[str, str] = field(default_factory=dict)  # stand-in -> original
    _stand_ins: dict[tuple[str, str], str] = field(default_factory=dict)

    def _stand_in(self, kind: str, value: str) -> str:
        key = (kind, re.sub(r"\s+", " ", value).strip().casefold())
        if key not in self._stand_ins:
            count = sum(1 for known_kind, _ in self._stand_ins if known_kind == kind) + 1
            stand_in = f"[{kind}_{count}]"
            self._stand_ins[key] = stand_in
            self.originals[stand_in] = value.strip()
        return self._stand_ins[key]

    def hide(self, text: str) -> str:
        for kind, pattern in _RULES:
            def swap(match: re.Match[str]) -> str:
                if "value" not in pattern.groupindex:
                    return self._stand_in(kind, match.group(0))
                start, end = match.span("value")
                value = match.group("value").rstrip(" .,")
                offset = start - match.start()
                whole = match.group(0)
                return whole[:offset] + self._stand_in(kind, value) + whole[offset + len(value):]
            text = pattern.sub(swap, text)
        # A name introduced once by its label ("Người lao động: Lê Thị Mai") also appears
        # bare elsewhere ("Lê Thị Mai cam kết..."); those are the same person.
        for stand_in, original in list(self.originals.items()):
            if stand_in.startswith("[NGƯỜI_") and len(original) > 4:
                text = re.sub(rf"(?<!\w){re.escape(original)}(?!\w)", stand_in, text)
        return text

    def reveal(self, value: Any) -> Any:
        """The value with every stand-in put back; strings inside lists and dicts too."""
        if isinstance(value, str):
            return _STAND_IN.sub(lambda match: self.originals.get(match.group(0), match.group(0)), value)
        if isinstance(value, list):
            return [self.reveal(item) for item in value]
        if isinstance(value, dict):
            return {key: self.reveal(item) for key, item in value.items()}
        return value


STAND_IN_INSTRUCTION = (
    "Personal data in the text is replaced by stand-ins such as [NGƯỜI_1], [SĐT_1], "
    "[CCCD_1], [ĐỊA_CHỈ_1]. Copy every stand-in exactly as written wherever you refer to it; "
    "never translate, alter, expand or guess what is behind it."
)
