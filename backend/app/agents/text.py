"""Text normalisation shared by the keyword fallbacks of the HR and Legal routers."""

from __future__ import annotations

import unicodedata



def _normalize_intent_text(message: str) -> str:
    normalized = unicodedata.normalize("NFD", message.lower().replace("đ", "d"))
    return " ".join(
        "".join(char for char in normalized if unicodedata.category(char) != "Mn").split()
    )
