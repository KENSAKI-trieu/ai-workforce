import re


def clean_document_text(text: str) -> str:
    """Remove parser noise while preserving headings and business structure."""
    cleaned = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", cleaned)
    cleaned = re.sub(r"(?<=\w)-\n(?=\w)", "", cleaned)
    cleaned = re.sub(
        r"(?im)^[ \t]*(?:trang[ \t]+\d+[ \t]*/[ \t]*\d+|page[ \t]+\d+[ \t]+of[ \t]+\d+)[ \t]*$",
        "",
        cleaned,
    )
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
