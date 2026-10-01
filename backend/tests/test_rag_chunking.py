"""The knowledge index's chunker: token sizing, heading paths and merging small sections."""

from pathlib import Path

import pytest

from app.domains.knowledge.rag_chunking import chunk_text, estimate_text_tokens, estimate_tokens

BACKEND_COPY = Path(__file__).resolve().parents[1] / "app" / "domains" / "knowledge" / "rag_chunking.py"
AI_SERVICE_COPY = (
    Path(__file__).resolve().parents[2] / "apps" / "ai-service" / "app" / "services" / "chunking" / "rag_chunking.py"
)


def _chunks(content: str, **sizes: int) -> list[dict]:
    options = {"chunk_size": 700, "chunk_overlap": 80, "target_size": 450, "min_size": 100, **sizes}
    return chunk_text(content, **options)


@pytest.mark.skipif(not AI_SERVICE_COPY.exists(), reason="ai-service sources are not checked out")
def test_backend_and_ai_service_chunk_with_the_same_code():
    # Production chunks in the AI service, every backend test in the backend: a fix made in
    # one copy only would pass here and change nothing in production.
    assert BACKEND_COPY.read_bytes().replace(b"\r\n", b"\n") == AI_SERVICE_COPY.read_bytes().replace(b"\r\n", b"\n")


def test_tokens_are_estimated_from_word_length_not_counted_as_words():
    assert estimate_tokens("a") == 1
    assert estimate_tokens("Điều") == 2
    assert estimate_tokens("token-123") == 3
    # A Vietnamese sentence costs well over one token per word, as Gemini counts it.
    sentence = "Người lao động được nghỉ phép năm hưởng nguyên lương theo quy định của công ty."
    assert estimate_text_tokens(sentence) > 1.3 * len(sentence.split())


def test_a_window_holds_at_most_chunk_size_estimated_tokens():
    content = " ".join("nghiệp" for _ in range(1000))  # 6 characters: 2 tokens a word

    chunks = _chunks(content, chunk_size=100, chunk_overlap=10, target_size=100)

    assert all(chunk["token_count"] <= 100 for chunk in chunks)
    assert len(chunks[0]["content"].split()) == 50


def test_numbered_points_stay_under_their_article():
    chunks = _chunks(
        "Điều 5. Thanh toán\n"
        "1. Bên A thanh toán trong 05 ngày.\n"
        "2. Bên B xuất hóa đơn.",
        min_size=0,
    )

    assert [chunk["header_path"] for chunk in chunks] == [
        ["Điều 5. Thanh toán"],
        ["Điều 5. Thanh toán", "1. Bên A thanh toán trong 05 ngày."],
        ["Điều 5. Thanh toán", "2. Bên B xuất hóa đơn."],
    ]
    assert chunks[1]["header_level"] == 1


def test_a_bare_article_title_and_its_small_points_make_one_chunk():
    content = (
        "Điều 5. Thanh toán\n"
        "1. Bên A thanh toán trong 05 ngày.\n"
        "2. Bên B xuất hóa đơn.\n"
        "Điều 6. Bảo hành\n"
        "Bảo hành 12 tháng."
    )

    chunks = _chunks(content)

    assert [chunk["section_title"] for chunk in chunks] == ["Điều 5. Thanh toán", "Điều 6. Bảo hành"]
    assert chunks[0]["section_type"] == "article"
    assert "1. Bên A" in chunks[0]["content"] and "2. Bên B" in chunks[0]["content"]
    # Two articles at the top level are never folded together.
    assert "Điều 6" not in chunks[0]["content"]


def test_sibling_points_merge_under_their_parent_heading():
    long_intro = " ".join(["Điều khoản chung áp dụng cho toàn bộ hợp đồng."] * 20)
    content = (
        "Điều 5. Thanh toán\n"
        f"{long_intro}\n"
        "Khoản 1. Tạm ứng\nTạm ứng 70%.\n"
        "Khoản 2. Quyết toán\nQuyết toán phần còn lại."
    )

    chunks = _chunks(content)

    assert len(chunks) == 1  # the small points fold into the article they belong to
    assert chunks[0]["section_title"] == "Điều 5. Thanh toán"
    assert chunks[0]["header_path"] == ["Điều 5. Thanh toán"]

    points_only = _chunks("# Quy chế\nĐiều 5. Thanh toán\nKhoản 1. Tạm ứng\nTạm ứng 70%.\nKhoản 2. Quyết toán\nPhần còn lại.")
    assert len(points_only) == 1
    assert points_only[0]["section_title"] == "Quy chế"


def test_short_sections_pair_up_and_are_named_after_both():
    policy = (
        "# CHÍNH SÁCH NGHỈ PHÉP\n"
        + " ".join(["Mã tài liệu HR-POL-004, hiệu lực từ ngày 01/08/2026."] * 8) + "\n"
        + "\n".join(
            f"{number}. {title}\n" + " ".join([f"Quy định về {title.lower()} áp dụng cho nhân viên."] * 4)
            for number, title in enumerate(["Mục đích", "Nguyên tắc chung", "Nghỉ phép năm", "Thời hạn gửi yêu cầu"], 1)
        )
    )

    chunks = _chunks(policy)

    # Each point is under 100 tokens: two make a chunk, not all four in one of many topics.
    assert [chunk["section_title"] for chunk in chunks] == [
        "CHÍNH SÁCH NGHỈ PHÉP",
        "1. Mục đích; 2. Nguyên tắc chung",
        "3. Nghỉ phép năm; 4. Thời hạn gửi yêu cầu",
    ]
    assert chunks[1]["header_path"] == ["CHÍNH SÁCH NGHỈ PHÉP"]
    assert chunks[1]["section_type"] == "numbered_heading"


def test_sections_large_enough_on_their_own_are_not_merged():
    body = " ".join(["Nhân viên gửi đơn nghỉ phép trước ba ngày làm việc."] * 12)
    content = f"# Quy chế\nĐiều 1. Phạm vi\n{body}\nĐiều 2. Đối tượng\n{body}"

    chunks = _chunks(content)

    assert [chunk["section_title"] for chunk in chunks] == ["Quy chế", "Điều 2. Đối tượng"]
    assert estimate_text_tokens(body) >= 100


def test_merging_stops_at_the_target_size():
    point = " ".join(["Bên B có trách nhiệm bàn giao tài liệu."] * 4)
    content = "Điều 3. Nghĩa vụ\n" + "\n".join(f"{index}. Nghĩa vụ {index}: {point}" for index in range(1, 11))

    chunks = _chunks(content, target_size=200)

    assert len(chunks) > 1
    assert all(chunk["token_count"] <= 200 for chunk in chunks)
    assert "1. Nghĩa vụ 1" in chunks[0]["content"] and "2. Nghĩa vụ 2" in chunks[0]["content"]


def test_the_text_before_the_first_heading_stays_its_own_chunk():
    chunks = _chunks("CÔNG TY ABC\nSố 12/QĐ\nĐiều 1. Phạm vi\nÁp dụng toàn công ty.")

    assert [chunk["section_title"] for chunk in chunks] == ["Mở đầu", "Điều 1. Phạm vi"]


def test_a_merged_chunk_keeps_every_page_it_spans():
    chunks = _chunks(
        "[[PAGE:1]]\nĐiều 5. Thanh toán\n1. Bên A thanh toán.\n"
        "[[PAGE:2]]\n2. Bên B xuất hóa đơn."
    )

    assert len(chunks) == 1
    assert chunks[0]["page"] == 1
    assert chunks[0]["pages"] == [1, 2]
    assert "[[PAGE:" not in chunks[0]["content"]


def test_text_of_only_page_markers_has_nothing_to_chunk():
    assert _chunks("[[PAGE:1]]\n\n[[PAGE:2]]") == []
