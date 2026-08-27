"""
Tests for Hybrid RAG Engine, document ingestion, vector search, and Knowledge Agent inline citations.
"""

import sys
from types import SimpleNamespace

import pytest

from app.services.rag_service import (
    CHUNK_OVERLAP_TOKENS,
    CHUNK_SIZE_TOKENS,
    build_configured_chunks,
    chunk_document_content,
    clean_document_text,
    _rank_sparse_bm25,
)
from app.models.models import DocumentChunk, KnowledgeDocument
from app.core.config import settings
from app.services.embedding_service import (
    EmbeddingService,
    build_embedding_text,
    calculate_content_hash,
    get_embedding_service,
    is_embedding_model_unavailable_error,
    is_embedding_resource_error,
    normalize_embedding_device,
)
from app.api.v1.documents import _parse_allowed_roles
from app.services.knowledge_storage import read_original_file


def test_chunking_respects_size_and_overlap():
    """Long sections use the configured token limit and sliding overlap."""
    content = " ".join(f"token-{index}" for index in range(1100))

    chunks = chunk_document_content(content)

    assert len(chunks) >= 2
    assert all(chunk["token_count"] <= CHUNK_SIZE_TOKENS for chunk in chunks)
    assert (
        chunks[0]["content"].split()[-CHUNK_OVERLAP_TOKENS:]
        == chunks[1]["content"].split()[:CHUNK_OVERLAP_TOKENS]
    )
    assert (
        chunks[-2]["content"].split()[-CHUNK_OVERLAP_TOKENS:]
        == chunks[-1]["content"].split()[:CHUNK_OVERLAP_TOKENS]
    )


def test_sparse_bm25_prioritizes_rare_domain_terms_over_common_words():
    generic_chunks = [
        SimpleNamespace(
            document_title="Quy định nội bộ",
            document_name=f"policy-{index}.md",
            section_title="Thiết kế hệ thống",
            content="Hệ thống được thiết kế để phục vụ một quy trình làm việc trên công ty.",
        )
        for index in range(10)
    ]
    technical_chunk = SimpleNamespace(
        document_title="Kiến trúc AI",
        document_name="gpu-serving.md",
        section_title="LLM serving",
        content="Một GPU phục vụ nhiều LLM bằng quantization và dynamic batching.",
    )

    ranked = _rank_sparse_bm25(
        "Hệ thống phục vụ LLM trên một GPU được thiết kế như thế nào?",
        [*generic_chunks, technical_chunk],
    )

    assert ranked[0][0] is technical_chunk
    assert ranked[0][1] > ranked[1][1]


def test_sparse_bm25_prioritizes_the_specific_query_phrase():
    unrelated = SimpleNamespace(
        document_title="Nội quy lao động",
        document_name="rules.md",
        section_title="Quy định sử dụng thẻ nhân viên",
        content="Nhân viên phải tuân thủ quy định ra vào cổng và sử dụng thẻ.",
    )
    leave_policy = SimpleNamespace(
        document_title="Nội quy lao động",
        document_name="rules.md",
        section_title="Nghỉ phép năm",
        content="Nhân viên được nghỉ phép năm và phải gửi yêu cầu đúng quy định.",
    )

    ranked = _rank_sparse_bm25(
        "Quy định nghỉ phép của nhân viên là gì?",
        [unrelated, leave_policy],
    )

    assert ranked[0][0] is leave_policy


def test_configured_standard_chunking_uses_custom_token_limits():
    content = " ".join(f"token-{index}" for index in range(500))

    chunks = build_configured_chunks(
        content,
        mode="standard",
        chunk_size=120,
        chunk_overlap=20,
    )

    assert len(chunks) > 1
    assert all(chunk["token_count"] <= 120 for chunk in chunks)
    assert all(chunk["chunking_mode"] == "standard" for chunk in chunks)


def test_parent_child_chunking_keeps_parent_context_on_children():
    content = " ".join(f"token-{index}" for index in range(900))

    chunks = build_configured_chunks(
        content,
        mode="parent_child",
        chunk_size=120,
        chunk_overlap=20,
        parent_chunk_size=360,
    )

    assert len(chunks) > 3
    assert all(chunk["chunking_mode"] == "parent_child" for chunk in chunks)
    assert all(chunk["parent_content"] for chunk in chunks)
    assert len({chunk["parent_chunk_index"] for chunk in chunks}) > 1


def test_embedding_gpu_alias_uses_pytorch_cuda_device():
    assert normalize_embedding_device("gpu") == "cuda"
    assert normalize_embedding_device("NVIDIA") == "cuda"
    assert normalize_embedding_device("cuda:0") == "cuda:0"


def test_embedding_resource_errors_detect_windows_pagefile_exhaustion():
    assert is_embedding_resource_error(
        OSError("The paging file is too small for this operation to complete. (os error 1455)")
    )
    assert is_embedding_resource_error(RuntimeError("CUDA out of memory"))
    assert not is_embedding_resource_error(RuntimeError("Embedding dimension mismatch"))
    assert is_embedding_model_unavailable_error(
        OSError("Cannot find the requested files in the disk cache and outgoing traffic has been disabled")
    )


def test_embedding_model_uses_deterministic_fallback_when_pagefile_is_exhausted(monkeypatch):
    def fail_model_load(*_args, **_kwargs):
        raise OSError("The paging file is too small for this operation to complete. (os error 1455)")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=fail_model_load),
    )
    monkeypatch.setattr(settings, "EMBEDDING_ALLOW_DETERMINISTIC_FALLBACK", True)
    service = EmbeddingService()
    service.backend = "sentence_transformers"

    assert service._load_model() is None
    assert service.backend == "deterministic"
    assert len(service.embed_texts(["nội dung kiểm thử"])[0]) == service.dimension


def test_chunking_preserves_markdown_header_context():
    """H1-H3 boundaries and heading hierarchy are retained in chunk metadata."""
    content = (
        "# Chính sách nhân sự\n"
        "Nội dung tổng quan.\n"
        "## Nghỉ phép\n"
        "Quy định nghỉ phép.\n"
        "### Phê duyệt\n"
        "Quản lý phê duyệt."
    )

    chunks = chunk_document_content(content)

    assert [chunk["section_title"] for chunk in chunks] == [
        "Chính sách nhân sự",
        "Nghỉ phép",
        "Phê duyệt",
    ]
    assert chunks[2]["header_level"] == 3
    assert chunks[2]["header_path"] == [
        "Chính sách nhân sự",
        "Nghỉ phép",
        "Phê duyệt",
    ]
    assert chunks[1]["content"].startswith("## Nghỉ phép")


def test_chunking_prefers_business_boundaries_before_token_windows():
    content = (
        "# Chính sách nghỉ phép\n"
        "Điều 1. Phạm vi áp dụng\nÁp dụng cho toàn công ty.\n"
        "Khoản 1. Nhân viên chính thức\nNội dung khoản.\n"
        "Mục điều kiện\nNhân viên còn ngày phép.\n"
        "Mục trách nhiệm\nQuản lý phải phản hồi.\n"
        "Bước 1: Gửi yêu cầu\nNhân viên tạo đơn trên hệ thống.\n"
        "Bước 2: Phê duyệt\nQuản lý xem xét yêu cầu."
    )

    chunks = chunk_document_content(content)

    assert [chunk["section_type"] for chunk in chunks] == [
        "heading",
        "article",
        "clause",
        "condition",
        "responsibility",
        "step",
        "step",
    ]
    assert all(chunk["section_chunk_index"] == 0 for chunk in chunks)
    assert chunks[2]["header_path"] == [
        "Chính sách nghỉ phép",
        "Điều 1. Phạm vi áp dụng",
        "Khoản 1. Nhân viên chính thức",
    ]


def test_chunking_tracks_pdf_page_markers_without_leaking_them():
    content = (
        "[[PAGE:4]]\n## Quy trình xin nghỉ\nNhân viên gửi yêu cầu.\n"
        "[[PAGE:5]]\nBước 1: Quản lý phê duyệt\nQuản lý phản hồi."
    )

    chunks = chunk_document_content(content)

    assert [chunk["page"] for chunk in chunks] == [4, 5]
    assert all("[[PAGE:" not in chunk["content"] for chunk in chunks)


def test_chunking_splits_appendix_from_previous_numbered_section():
    chunks = chunk_document_content(
        "[[PAGE:2]]\n10. Truy vấn qua RAG\nNội dung chính.\n"
        "Phụ lục - Trạng thái workflow\nESCALATED_HR\nChuyển HR xử lý"
    )

    assert [chunk["section_title"] for chunk in chunks] == [
        "10. Truy vấn qua RAG",
        "Phụ lục - Trạng thái workflow",
    ]
    assert chunks[1]["section_type"] == "appendix"
    assert chunks[1]["page"] == 2


def test_allowed_roles_parser_tolerates_multipart_bracket_syntax():
    assert _parse_allowed_roles("[employee,manager,admin,owner,ceo]") == [
        "admin",
        "ceo",
        "employee",
        "manager",
        "owner",
    ]


def test_cleaning_and_numbered_headings_preserve_business_structure():
    raw = (
        "Trang 1 / 4\n\n\n"
        "1. Mục đích\nNhân viên gửi yêu cầu nghỉ-\nphép đúng hạn.\n"
        "3.1. Thời gian báo trước\nÍt nhất ba ngày."
    )

    cleaned = clean_document_text(raw)
    chunks = chunk_document_content(raw)

    assert "Trang 1 / 4" not in cleaned
    assert "nghỉphép" in cleaned
    assert [chunk["section_title"] for chunk in chunks] == [
        "1. Mục đích",
        "3.1. Thời gian báo trước",
    ]


def test_embedding_text_hash_and_batch_vectors_are_stable():
    service = get_embedding_service()
    embedding_text = build_embedding_text({
        "department": "HR",
        "document_type": "policy",
        "document_title": "Chính sách nghỉ phép",
        "section_title": "Thời gian báo trước",
        "content": "Nhân viên phải gửi yêu cầu trước ba ngày.",
    })

    vectors = service.embed_texts([embedding_text, embedding_text])

    assert "Tên tài liệu: Chính sách nghỉ phép" in embedding_text
    assert calculate_content_hash("a  b\n c") == calculate_content_hash("a b c")
    assert len(vectors) == 2
    assert len(vectors[0]) == service.dimension
    assert vectors[0] == vectors[1]


def test_list_documents(client, ceo_token_headers):
    """Test listing documents in Knowledge Base."""
    response = client.get("/api/v1/documents/", headers=ceo_token_headers)
    assert response.status_code == 200
    docs = response.json()
    assert isinstance(docs, list)
    assert len(docs) > 0
    doc_names = [d["document_name"] for d in docs]
    assert "Chinh_sach_Nghi_phep_2025.md" in doc_names


def test_ingest_text_document(client, ceo_token_headers):
    """Test ingesting new markdown document text into RAG store."""
    payload = {
        "document_name": "Quy_dinh_Bao_mat_2025.md",
        "content": "# Quy Định Bảo Mật Thông Tin\n1. Tất cả nhân viên không được chia sẻ mật khẩu tài khoản.\n2. Thiết bị công ty phải đặt mật khẩu khóa màn hình sau 5 phút.",
        "department_access": "ALL",
    }
    response = client.post("/api/v1/documents/ingest-text", data=payload, headers=ceo_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["chunks_created"] > 0


def test_document_processing_status(client, ceo_token_headers):
    document_id = "processing-status-policy.md"
    ingested = client.post(
        "/api/v1/documents/ingest-text",
        data={
            "document_name": document_id,
            "content": "# Quy trinh\nTai lieu dung de kiem tra trang thai xu ly.",
            "department_access": "ALL",
        },
        headers=ceo_token_headers,
    )
    assert ingested.status_code == 200

    response = client.get(
        f"/api/v1/documents/processing-status/{document_id}",
        headers=ceo_token_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_id"] == document_id
    assert payload["processing_status"] == "ready"
    assert payload["processing_progress"] == 100
    assert payload["chunk_count"] == ingested.json()["chunks_created"]
    assert payload["error_message"] is None


def test_upload_duplicate_chunks_requires_explicit_replace_or_keep_old(
    client, ceo_token_headers, transactional_db_session
):
    filename = "duplicate-decision-policy.md"
    content = (
        "# Chính sách thử nghiệm\n"
        "Nội dung duy nhất dùng để kiểm tra cảnh báo chunk trùng.\n"
        "## Quy trình\n"
        "Bước 1: Người dùng tải tài liệu lên hệ thống."
    ).encode("utf-8")
    request_data = {
        "department_access": "ALL",
        "collection_name": "Duplicate Tests",
        "document_id": "duplicate-decision-policy",
        "version": "1.0",
    }

    first = client.post(
        "/api/v1/documents/upload",
        data=request_data,
        files={"file": (filename, content, "text/markdown")},
        headers=ceo_token_headers,
    )
    assert first.status_code == 201
    assert first.json()["status"] == "INDEXED"
    transactional_db_session.expire_all()
    original_chunks = transactional_db_session.query(DocumentChunk).filter(
        DocumentChunk.document_id == "duplicate-decision-policy",
        DocumentChunk.version == "1.0",
    ).order_by(DocumentChunk.chunk_index).all()
    original_ids = [chunk.id for chunk in original_chunks]

    conflict = client.post(
        "/api/v1/documents/upload",
        data=request_data,
        files={"file": (filename, content, "text/markdown")},
        headers=ceo_token_headers,
    )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["code"] == "DUPLICATE_CHUNKS"
    assert detail["duplicate_count"] == detail["incoming_chunk_count"] == len(original_chunks)
    assert [item["incoming"]["chunk_index"] for item in detail["duplicates"]] == list(
        range(len(original_chunks))
    )
    assert all(item["content"] for item in detail["duplicates"])

    kept = client.post(
        "/api/v1/documents/upload",
        data={**request_data, "duplicate_strategy": "keep_old"},
        files={"file": (filename, content, "text/markdown")},
        headers=ceo_token_headers,
    )
    assert kept.status_code == 200
    assert kept.json()["status"] == "KEPT_EXISTING"
    transactional_db_session.expire_all()
    kept_ids = [
        chunk.id
        for chunk in transactional_db_session.query(DocumentChunk).filter(
            DocumentChunk.document_id == "duplicate-decision-policy",
            DocumentChunk.version == "1.0",
        ).order_by(DocumentChunk.chunk_index).all()
    ]
    assert kept_ids == original_ids

    replaced = client.post(
        "/api/v1/documents/upload",
        data={**request_data, "duplicate_strategy": "replace"},
        files={"file": (filename, content, "text/markdown")},
        headers=ceo_token_headers,
    )
    assert replaced.status_code == 201
    transactional_db_session.expire_all()
    replacement_chunks = transactional_db_session.query(DocumentChunk).filter(
        DocumentChunk.document_id == "duplicate-decision-policy",
        DocumentChunk.version == "1.0",
    ).order_by(DocumentChunk.chunk_index).all()
    assert len(replacement_chunks) == len(original_chunks)
    assert [chunk.id for chunk in replacement_chunks] != original_ids


def test_delete_document_removes_selected_version_chunks_and_original_file(
    client, ceo_token_headers, transactional_db_session
):
    document_id = "deletion-cascade-policy"
    for version in ("1.0", "2.0"):
        uploaded = client.post(
            "/api/v1/documents/upload",
            data={
                "department_access": "ALL",
                "collection_name": "Deletion Tests",
                "document_id": document_id,
                "version": version,
            },
            files={
                "file": (
                    f"deletion-policy-v{version}.md",
                    f"# Delete test {version}\nUnique content for version {version}.".encode(),
                    "text/markdown",
                )
            },
            headers=ceo_token_headers,
        )
        assert uploaded.status_code == 201

    transactional_db_session.expire_all()
    target = transactional_db_session.query(KnowledgeDocument).filter(
        KnowledgeDocument.document_id == document_id,
        KnowledgeDocument.version == "2.0",
    ).one()
    assert target.storage_key
    storage_key = target.storage_key
    assert read_original_file(storage_key)

    deleted = client.delete(
        f"/api/v1/documents/{document_id}",
        params={"version": "2.0"},
        headers=ceo_token_headers,
    )

    assert deleted.status_code == 200
    payload = deleted.json()
    assert payload["document_id"] == document_id
    assert payload["version"] == "2.0"
    assert payload["chunks_deleted"] > 0
    assert payload["file_deleted"] is True
    transactional_db_session.expire_all()
    assert transactional_db_session.query(KnowledgeDocument).filter(
        KnowledgeDocument.document_id == document_id,
        KnowledgeDocument.version == "2.0",
    ).count() == 0
    assert transactional_db_session.query(DocumentChunk).filter(
        DocumentChunk.document_id == document_id,
        DocumentChunk.version == "2.0",
    ).count() == 0
    assert transactional_db_session.query(KnowledgeDocument).filter(
        KnowledgeDocument.document_id == document_id,
        KnowledgeDocument.version == "1.0",
    ).count() == 1
    assert transactional_db_session.query(DocumentChunk).filter(
        DocumentChunk.document_id == document_id,
        DocumentChunk.version == "1.0",
    ).count() > 0
    with pytest.raises(FileNotFoundError):
        read_original_file(storage_key)


def test_hybrid_rag_search(client, ceo_token_headers):
    """Test Hybrid RAG search returning top scored chunks."""
    payload = {
        "query": "nghỉ phép bao nhiêu ngày",
        "top_k": 3,
    }
    response = client.post("/api/v1/documents/search", json=payload, headers=ceo_token_headers)
    assert response.status_code == 200
    results = response.json()
    assert isinstance(results, list)
    assert len(results) > 0
    assert "citation_tag" in results[0]
    assert results[0]["score"] > 0.0


def test_governed_chunk_metadata_and_role_acl(
    client, ceo_token_headers, employee_token_headers
):
    payload = {
        "document_name": "leave_policy_v2.1.pdf.txt",
        "document_id": "leave-policy-2026",
        "document_title": "Chính sách nghỉ phép",
        "document_type": "policy",
        "content": "## Quy trình xin nghỉ\nGovernanceUnique nhân viên phải gửi yêu cầu.",
        "department_access": "ALL",
        "version": "2.1",
        "effective_date": "2026-07-01",
        "status": "active",
        "confidentiality": "restricted",
        "allowed_roles": '["manager", "hr"]',
    }
    ingested = client.post(
        "/api/v1/documents/ingest-text",
        data=payload,
        headers=ceo_token_headers,
    )
    assert ingested.status_code == 200

    admin_search = client.post(
        "/api/v1/documents/search",
        json={"query": "GovernanceUnique", "top_k": 20},
        headers=ceo_token_headers,
    )
    governed = next(
        item
        for item in admin_search.json()
        if item["document_id"] == "leave-policy-2026"
    )
    assert governed["document_title"] == "Chính sách nghỉ phép"
    assert governed["document_type"] == "policy"
    assert governed["version"] == "2.1"
    assert governed["effective_date"] == "2026-07-01"
    assert governed["confidentiality"] == "restricted"
    assert governed["allowed_roles"] == ["hr", "manager"]
    assert governed["source_file"] == "leave_policy_v2.1.pdf.txt"
    assert "chunk=" in governed["citation_tag"]

    employee_search = client.post(
        "/api/v1/documents/search",
        json={"query": "GovernanceUnique", "top_k": 20},
        headers=employee_token_headers,
    )
    assert all(
        item["document_id"] != "leave-policy-2026"
        for item in employee_search.json()
    )


def test_future_document_is_not_retrieved(client, ceo_token_headers):
    ingested = client.post(
        "/api/v1/documents/ingest-text",
        data={
            "document_name": "future-policy.md",
            "document_id": "future-policy",
            "content": "# FutureOnlyToken\nChính sách chưa có hiệu lực.",
            "effective_date": "2099-01-01",
            "status": "active",
        },
        headers=ceo_token_headers,
    )
    assert ingested.status_code == 200

    response = client.post(
        "/api/v1/documents/search",
        json={"query": "FutureOnlyToken", "top_k": 20},
        headers=ceo_token_headers,
    )
    assert all(item["document_id"] != "future-policy" for item in response.json())


def test_document_lifecycle_hash_dedup_and_version_reuse(
    client, ceo_token_headers, transactional_db_session
):
    base_payload = {
        "document_name": "versioned-policy.md",
        "document_id": "versioned-policy",
        "document_title": "Versioned policy",
        "document_type": "policy",
        "department_access": "HR",
        "content": (
            "# Phạm vi\nNội dung không thay đổi UniqueStableSection.\n"
            "## Quy trình\nNội dung phiên bản một."
        ),
        "version": "1.0",
    }
    first = client.post(
        "/api/v1/documents/ingest-text",
        data=base_payload,
        headers=ceo_token_headers,
    )
    assert first.status_code == 200
    first_chunks = transactional_db_session.query(DocumentChunk).filter(
        DocumentChunk.document_id == "versioned-policy",
        DocumentChunk.version == "1.0",
    ).all()
    stable_hash = calculate_content_hash(first_chunks[0].content)
    stable_vector = list(first_chunks[0].embedding)

    reindexed = client.post(
        "/api/v1/documents/ingest-text",
        data=base_payload,
        headers=ceo_token_headers,
    )
    assert reindexed.status_code == 200
    transactional_db_session.expire_all()
    same_version_chunks = transactional_db_session.query(DocumentChunk).filter(
        DocumentChunk.document_id == "versioned-policy",
        DocumentChunk.version == "1.0",
    ).all()
    assert len(same_version_chunks) == reindexed.json()["chunks_created"] == 2
    assert sorted(chunk.chunk_index for chunk in same_version_chunks) == [0, 1]

    second_payload = dict(base_payload)
    second_payload.update({
        "version": "2.0",
        "content": (
            "# Phạm vi\nNội dung không thay đổi UniqueStableSection.\n"
            "## Quy trình\nNội dung phiên bản hai đã cập nhật."
        ),
    })
    second = client.post(
        "/api/v1/documents/ingest-text",
        data=second_payload,
        headers=ceo_token_headers,
    )
    assert second.status_code == 200
    transactional_db_session.expire_all()

    record = transactional_db_session.query(KnowledgeDocument).filter(
        KnowledgeDocument.document_id == "versioned-policy",
        KnowledgeDocument.version == "2.0",
    ).one()
    reused_chunk = transactional_db_session.query(DocumentChunk).filter(
        DocumentChunk.document_id == "versioned-policy",
        DocumentChunk.version == "2.0",
        DocumentChunk.content_hash == stable_hash,
    ).one()
    old_chunks = transactional_db_session.query(DocumentChunk).filter(
        DocumentChunk.document_id == "versioned-policy",
        DocumentChunk.version == "1.0",
    ).all()

    assert record.processing_status == "ready"
    assert record.chunk_count == 2
    assert record.embedding_model == get_embedding_service().model_name
    assert reused_chunk.embedding_status == "embedded"
    assert list(reused_chunk.embedding) == stable_vector
    assert all(chunk.status == "inactive" for chunk in old_chunks)


def test_knowledge_agent_chat_citations(client, employee_token_headers):
    """Test Knowledge Agent generating response with inline citation tags."""
    payload = {
        "agent_role": "KNOWLEDGE",
        "message": "Chi phí di chuyển công tác phí tối đa là bao nhiêu?",
    }
    response = client.post("/api/v1/agent/chat", json=payload, headers=employee_token_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["agent_role"] == "KNOWLEDGE"
    assert len(data["citations"]) > 0
    assert "[Citation:" in data["reply"]
