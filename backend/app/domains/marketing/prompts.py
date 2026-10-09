"""The campaign pipeline's prompts, and how retrieved documents are fenced in them.

The four prompts are market-agent's own (prompts/*.md). Documents and files go to the
model inside <external_context>, which the prompts say is data, never instructions; a
forged tag inside a document is removed so it cannot close the fence early.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.domains.legal.contract_privacy import STAND_IN_INSTRUCTION

_DIR = Path(__file__).parent / "prompts"
_TAG_RE = re.compile(r"<\s*/?\s*external_context\s*>", re.IGNORECASE)


@lru_cache
def load_prompt(name: str) -> str:
    """One of outline, generator, fact_checker, refine, with the stand-in rule appended."""
    return (_DIR / f"{name}.md").read_text(encoding="utf-8").strip() + "\n\n" + STAND_IN_INSTRUCTION


def external_context(chunks: list[str]) -> str:
    body = "\n\n".join(_TAG_RE.sub("", chunk) for chunk in chunks) or "(Không có tài liệu tham chiếu)"
    return f"<external_context>\n{body}\n</external_context>"
