import pytest
from langchain.agents.middleware import PIIMiddleware
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from app.governance.middleware.redaction import redact_text
from app.governance.middleware.stack import governed_middleware
from app.governance.middleware.observability import InMemoryTelemetrySink


@pytest.mark.parametrize("text", [
    "Tổng giá trị hợp đồng: 1.850.000.000 đồng",
    "Giá trị 12.500.000.000 VNĐ",
    "Giá 1.200.000.000",  # "000" is not an octet an address would write
    "Giá trị 1.250.100.200 đồng",
    "Vốn điều lệ 1.850.000.000.000",
    "MST: 0108765432",
    "Mã số thuế 0301234567",
    "MST: 0912345678",
    "Số tài khoản 1903 4567 8912 3456 tại Techcombank",
    "Hợp đồng số 15/2026/HĐMB ngày 10.09.2026",
])
def test_contract_figures_are_not_personal_data(text: str) -> None:
    assert redact_text(text) == text


@pytest.mark.parametrize(("text", "expected"), [
    ("Máy chủ 10.0.0.1 bị lỗi.", "Máy chủ [REDACTED_IP] bị lỗi."),
    ("Gọi 0912 345 678", "Gọi [REDACTED_PHONE_NUMBER]"),
    ("Gọi +84 912345678", "Gọi [REDACTED_PHONE_NUMBER]"),
    ("Tổng đài 024 3826 1234", "Tổng đài [REDACTED_PHONE_NUMBER]"),
    ("Thẻ 4111 1111 1111 1111", "Thẻ [REDACTED_CREDIT_CARD]"),
    ("Email lan@minhphat.vn", "Email [REDACTED_EMAIL]"),
])
def test_personal_data_is_still_redacted(text: str, expected: str) -> None:
    assert redact_text(text) == expected


def test_model_middleware_redacts_what_the_graph_redacts() -> None:
    model = FakeListChatModel(responses=["ok"])
    guards = [
        item for item in governed_middleware(simple_model=model, complex_model=model, telemetry_sink=InMemoryTelemetrySink())
        if isinstance(item, PIIMiddleware)
    ]
    text = "HĐ 1.850.000.000 đồng, MST: 0108765432, liên hệ 0912 345 678 hoặc máy 10.0.0.1"
    messages = [HumanMessage(text)]
    for guard in guards:
        update = guard.before_model({"messages": messages}, Runtime())
        if update:
            messages = update["messages"]
    assert messages[0].content == redact_text(text)
    assert "1.850.000.000 đồng" in messages[0].content
    assert "0108765432" in messages[0].content
    assert "0912 345 678" not in messages[0].content
    assert "10.0.0.1" not in messages[0].content
