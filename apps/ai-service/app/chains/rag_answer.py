import json
from collections.abc import Iterable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from app.chains.structured_extraction import invoke_structured_with_fallback
from app.models.factory import configured_chat_models
from app.schemas.citations import Citation, RAGAnswer


SYSTEM_PROMPT = (
    "Answer only from the supplied context. Return grounded=false when the "
    "context is insufficient. Every grounded claim must have a citation whose "
    "quote is copied exactly from the context."
)


def _fallback_answer(chunks: list[dict[str, Any]]) -> RAGAnswer:
    if not chunks or not str(chunks[0].get("content", "")).strip():
        return RAGAnswer(
            answer="Không tìm thấy tài liệu phù hợp để trả lời.",
            citations=[],
            grounded=False,
        )
    best = chunks[0]
    content = str(best["content"]).strip()
    return RAGAnswer(
        answer=content,
        grounded=True,
        citations=[Citation(
            document_id=str(best.get("document_id") or "") or None,
            document_title=str(
                best.get("document_title")
                or best.get("document_name")
                or best.get("source_file")
                or "unknown"
            ),
            section_title=best.get("section_title"),
            page=best.get("page_start") or best.get("page"),
            chunk_id=str(best.get("id") or "") or None,
            quote=content,
        )],
    )


def _validate_citations(answer: RAGAnswer, chunks: list[dict[str, Any]]) -> RAGAnswer:
    if not answer.grounded:
        return answer
    for citation in answer.citations:
        candidates = chunks
        if citation.chunk_id:
            candidates = [
                chunk for chunk in chunks
                if str(chunk.get("id") or "") == citation.chunk_id
            ]
        matches_source = False
        for chunk in candidates:
            titles = {
                str(value)
                for value in (
                    chunk.get("document_title"),
                    chunk.get("document_name"),
                    chunk.get("source_file"),
                )
                if value
            } or {"unknown"}
            document_id_matches = (
                citation.document_id is None
                or str(chunk.get("document_id") or "") == citation.document_id
            )
            if (
                citation.quote in str(chunk.get("content", ""))
                and citation.document_title in titles
                and document_id_matches
            ):
                matches_source = True
                break
        if not matches_source:
            raise ValueError("Citation does not match the supplied source context")
    return answer


def generate_rag_answer(
    question: str,
    chunks: list[dict[str, Any]],
    *,
    models: Iterable[BaseChatModel] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> RAGAnswer:
    selected_models = configured_chat_models(provider, model=model) if models is None else models
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Question:\n{question}\n\nContext:\n{json.dumps(chunks, ensure_ascii=False)}",
        },
    ]
    return invoke_structured_with_fallback(
        selected_models,
        messages,
        RAGAnswer,
        fallback=lambda: _fallback_answer(chunks),
        validator=lambda answer: _validate_citations(answer, chunks),
    )
