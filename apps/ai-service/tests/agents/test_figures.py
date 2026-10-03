"""A Finance reply's figures must be the tools' figures."""

from __future__ import annotations

from app.agents.base.figures import figures_in, format_money, render_results, unsupported_figures
from app.agents.base.nodes import citation_verification
from app.agents.finance.agent import POLICY as FINANCE_POLICY

BALANCE = {
    "account": "331",
    "period": "2026-09",
    "opening": {"debit": "0.00", "credit": "114000000.00"},
    "period_debit": "0.00",
    "period_credit": "11000000.00",
    "closing": {"debit": "0.00", "credit": "125000000.00"},
}


def test_figures_quoted_from_the_tool_pass_whatever_their_format():
    for answer in (
        "Số dư cuối kỳ TK 331 tháng 09/2026 là 125.000.000 ₫, dư Có.",
        "Phát sinh Có 11,000,000 VND; cuối kỳ khoảng 125 triệu.",
        "Dư Có 0,125 tỷ.",
    ):
        assert unsupported_figures(answer, [BALANCE]) == [], answer


def test_a_figure_the_model_worked_out_itself_is_caught():
    assert unsupported_figures("Tổng phải trả là 239.000.000 ₫", [BALANCE]) == ["239.000.000 ₫"]
    assert unsupported_figures("Tăng 9,6% so với đầu kỳ", [BALANCE]) == ["9,6%"]


def test_accounts_years_and_dates_are_not_figures():
    assert figures_in("TK 33311, TK 1331, năm 2026, ngày 15/09/2026, 30 ngày") == []


def test_the_users_own_figures_may_be_repeated():
    assert unsupported_figures("Có, chi phí đã vượt 50 triệu.", [BALANCE], ["Chi phí có vượt 50 triệu không?"]) == []


def test_the_withheld_answer_shows_the_tools_own_figures():
    rendered = render_results([BALANCE])
    assert "closing.credit: 125000000.00" in rendered


def _state(answer: str, results):
    return {
        "final_answer": answer,
        "citation_required": True,
        "numbers_from_tools": True,
        "messages": [{"role": "user", "content": "Số dư 331 tháng 9?"}],
        "tool_calls": [
            {"name": "get_account_balance", "status": "SUCCESS", "action": "READ_ONLY", "result": result}
            for result in results
        ],
        "errors": [],
        "execution_trace": [],
    }


def test_the_graph_withholds_a_finance_reply_with_an_invented_figure():
    assert FINANCE_POLICY.numbers_from_tools
    passed = citation_verification(_state("Số dư cuối kỳ là 125.000.000 ₫.", [BALANCE]))
    assert "final_answer" not in passed
    withheld = citation_verification(_state("Số dư cuối kỳ là 152.000.000 ₫.", [BALANCE]))
    assert "125000000.00" in withheld["final_answer"]
    assert withheld["errors"][-1]["error"] == "UNSUPPORTED_FIGURES"
    no_data = citation_verification(_state("Số dư cuối kỳ là 152.000.000 ₫.", []))
    assert "không có dữ liệu sổ sách" in no_data["final_answer"]


def test_a_reply_citing_the_tool_it_read_is_not_withheld():
    # The domain prompt asks for sources; a Finance reply cites the ledger the tool read.
    state = _state("Số dư cuối kỳ TK 331 là 125.000.000 ₫ (dư Có).", [{**BALANCE, "source": "fin_ledger_lines account 331* through 2026-09"}])
    for citation in ("get_account_balance", "fin_ledger_lines account 331* through 2026-09"):
        assert "final_answer" not in citation_verification({**state, "citations": [{"source": citation}]})
    invented = citation_verification({**state, "citations": [{"source": "Quy_che_tai_chinh.pdf"}]})
    assert invented["errors"][-1]["error"] == "UNVERIFIED_CITATION"


def test_amounts_are_written_the_vietnamese_way_without_changing_them():
    assert format_money("Còn phải trả 198000000.00 ₫, đã lên lịch 0.00 ₫.") == "Còn phải trả 198.000.000 ₫, đã lên lịch 0 ₫."
    assert format_money("Thực chi 62.000.000,00 ₫ (vượt 24,00%).") == "Thực chi 62.000.000 ₫ (vượt 24,00%)."
    assert format_money("Lãi 1.234.567,50 VND") == "Lãi 1.234.567,50 VND"
    # Not money: accounts, dates, invoice numbers and scaled figures stay as written.
    untouched = "TK 331, hoá đơn 8812 ngày 08/10/2026, khoảng 1,23 tỷ."
    assert format_money(untouched) == untouched


def test_the_graph_reformats_a_verified_reply_only():
    passed = citation_verification(_state("Số dư cuối kỳ là 125000000.00 ₫.", [BALANCE]))
    assert passed["final_answer"] == "Số dư cuối kỳ là 125.000.000 ₫."
    withheld = citation_verification(_state("Số dư cuối kỳ là 152000000.00 ₫.", [BALANCE]))
    assert withheld["errors"][-1]["error"] == "UNSUPPORTED_FIGURES"
