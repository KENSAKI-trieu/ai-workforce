from types import SimpleNamespace

import pytest

from app.services.document_parser import _extract_docx


@pytest.mark.parametrize(
    "style",
    [None, SimpleNamespace(name=None)],
)
def test_extract_docx_accepts_paragraph_without_style_name(monkeypatch, style):
    document = SimpleNamespace(
        paragraphs=[SimpleNamespace(text="Paragraph without a style", style=style)],
        tables=[],
    )
    monkeypatch.setattr("docx.Document", lambda _source: document)

    assert _extract_docx(b"fake-docx") == "Paragraph without a style"
