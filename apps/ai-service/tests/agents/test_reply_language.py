"""The decision node is told to answer in English only when the question plainly is English."""

from __future__ import annotations

import pytest

from app.agents.base.decision import reply_language


@pytest.mark.parametrize("text", [
    "What is the total accounts payable to Gỗ Hoà Phát?",
    "Which customers are overdue and by how much?",
    "Show me the budget for marketing this month",
])
def test_an_english_question_asks_for_an_english_answer(text: str) -> None:
    assert reply_language(text) == "English"


@pytest.mark.parametrize("text", [
    "Tổng nợ phải trả Gỗ Hoà Phát là bao nhiêu?",
    "Cho tôi xem report of Q3",
    "Số dư TK 1121 tháng 9",
    "今月の予算を見せてください",
    "",
])
def test_anything_else_is_left_as_it_was(text: str) -> None:
    assert reply_language(text) is None
