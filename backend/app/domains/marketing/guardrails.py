"""What the campaign pipeline refuses on the way in and cleans on the way out.

In: instructions aimed at the model rather than at a campaign -- "ignore the previous
instructions", "show me your system prompt", forged <external_context> tags -- in the
brief, an edited outline or post, or the reason an outline was rejected. Out: secrets that
leaked into a post, and the stock phrases that make a post read as machine-written, which
go back to the writer as fact-check issues. Ported from market-agent.
"""

from __future__ import annotations

import re
import unicodedata

_JAILBREAK_PATTERNS = {
    "ignore_instructions": r"\b(ignore|disregard|forget)\s+(all\s+|any\s+)?(the\s+|your\s+)?"
    r"(previous|prior|above|earlier|system)\s+(instructions?|prompts?|rules?|messages?)",
    "reveal_prompt": r"\b(reveal|show|print|repeat|leak|output)\s+(me\s+)?(your|the)\s+"
    r"(system\s+prompt|hidden\s+prompt|initial\s+instructions?|instructions?)",
    "role_override": r"\b(you\s+are\s+now\s+dan|do\s+anything\s+now|developer\s+mode|jailbreak)\b",
    "vi_ignore_instructions": r"\b(bỏ\s+qua|phớt\s+lờ|quên)\s+(hết\s+|tất\s+cả\s+|mọi\s+|các\s+)?(những\s+)?"
    r"(hướng\s+dẫn|chỉ\s+dẫn|chỉ\s+thị|quy\s+tắc|lệnh)\s*(trước|phía\s+trên|ở\s+trên|hệ\s+thống)?",
    "vi_reveal_prompt": r"\b(tiết\s+lộ|in\s+ra|cho\s+(tôi|tao|mình)\s+xem)\s+"
    r"(system\s+prompt|prompt\s+hệ\s+thống|chỉ\s+thị\s+hệ\s+thống)",
    "tag_injection": r"<\s*/?\s*(system|external_context|assistant)\s*>",
}
_JAILBREAK_RES = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in _JAILBREAK_PATTERNS.items()}


def detect_jailbreak(text: str | None) -> str | None:
    """The name of the rule the text breaks, or None."""
    normalized = unicodedata.normalize("NFC", text or "")
    return next((name for name, pattern in _JAILBREAK_RES.items() if pattern.search(normalized)), None)


_SECRET_PATTERNS = {
    "private_key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    "anthropic_key": r"\bsk-ant-[A-Za-z0-9_-]{20,}",
    "openai_key": r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}",
    "google_key": r"\bAIza[0-9A-Za-z_-]{35}\b",
    "aws_access_key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "github_token": r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
    "slack_token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    "jwt": r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "credential_assignment": r"(?i)\b(?:password|passwd|mật khẩu|api[_ -]?key|secret)\s*[:=]\s*\S{6,}",
}
_SECRET_RES = {name: re.compile(pattern) for name, pattern in _SECRET_PATTERNS.items()}


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """The text with every secret replaced by [REDACTED], and the kinds found."""
    found: list[str] = []
    for name, pattern in _SECRET_RES.items():
        text, count = pattern.subn("[REDACTED]", text)
        if count:
            found.append(name)
    return text, found


CLICHES = (
    "trong thời đại số",
    "trong thời đại công nghệ",
    "trong bối cảnh hiện nay",
    "hơn bao giờ hết",
    "không thể phủ nhận",
    "nâng tầm",
    "kỷ nguyên mới",
    "cách mạng hóa",
    "khai phá tiềm năng",
    "giải pháp toàn diện",
    "hãy cùng khám phá",
    "đừng bỏ lỡ cơ hội",
    "game-changer",
    "game changer",
    "unlock the power",
    "unleash",
    "in today's fast-paced world",
    "in the ever-evolving",
    "delve into",
    "a testament to",
    "revolutionize",
    "cutting-edge",
    "seamless",
    "elevate your",
)
_CLICHE_RE = re.compile("|".join(re.escape(phrase) for phrase in CLICHES), re.IGNORECASE)


def find_cliches(text: str) -> list[str]:
    """The stock phrases in the text, lower-cased, each once, in order of appearance."""
    normalized = unicodedata.normalize("NFC", text or "")
    return list(dict.fromkeys(match.group().lower() for match in _CLICHE_RE.finditer(normalized)))
