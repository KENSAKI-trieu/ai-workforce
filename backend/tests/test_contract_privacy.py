"""Personal data is swapped for stand-ins before contract text reaches a model provider."""

import pytest

from app.domains.legal.contract_privacy import Pseudonymizer

CONTRACT = """BÊN BÁN: CÔNG TY TNHH THIẾT BỊ CÔNG NGHIỆP HƯNG THỊNH
Địa chỉ: Lô B2, KCN Quang Minh, Mê Linh, Hà Nội. MST: 0108765432
Đại diện: Ông Trần Văn Hưng – Giám đốc
Người lao động: Lê Thị Mai, sinh năm 2002, CCCD số 001202012345, điện thoại 0912 345 678, email mai.le@gmail.com
Thường trú tại: Số 5 ngõ 12 Láng Hạ, Đống Đa, Hà Nội
Họ và tên: NGUYỄN VĂN AN
Số tài khoản: 1903 4567 8912 3456 tại Techcombank
Lê Thị Mai cam kết làm việc đủ 12 tháng, lương 6.500.000 đồng. Giá trị 1.850.000.000 đồng."""


def test_people_are_hidden_and_business_facts_are_not():
    hider = Pseudonymizer()
    hidden = hider.hide(CONTRACT)

    for personal in ("Trần Văn Hưng", "Lê Thị Mai", "001202012345", "0912 345 678", "mai.le@gmail.com",
                     "Láng Hạ", "NGUYỄN VĂN AN", "1903 4567 8912 3456"):
        assert personal not in hidden
    for business in ("CÔNG TY TNHH THIẾT BỊ CÔNG NGHIỆP HƯNG THỊNH", "MST: 0108765432", "KCN Quang Minh",
                     "6.500.000 đồng", "1.850.000.000 đồng", "sinh năm 2002", "Giám đốc"):
        assert business in hidden
    # The same person keeps one stand-in, whether introduced by a label or named bare.
    assert hidden.count("[NGƯỜI_") == 4
    assert hider.reveal(hidden) == CONTRACT


@pytest.mark.parametrize("text", [
    "Người đại diện: Công Ty TNHH ABC",
    "Người đại diện: CÔNG TY TNHH ABC",
    "Bên B: Công ty Khách Hàng Delta",
    "Trụ sở tại Bà Rịa - Vũng Tàu",
    "Luật áp dụng của Vương quốc Anh",
    "đồng ý thanh toán 30% giá trị",
])
def test_companies_places_and_plain_words_are_not_people(text):
    assert Pseudonymizer().hide(text) == text


def test_reveal_restores_stand_ins_inside_nested_results():
    hider = Pseudonymizer()
    hider.hide("Người lao động: Lê Thị Mai")

    revealed = hider.reveal({"findings": [{"reason": "[NGƯỜI_1] bị giữ bằng", "tags": ["[NGƯỜI_1]", "[NGƯỜI_9]"]}]})

    assert revealed == {"findings": [{"reason": "Lê Thị Mai bị giữ bằng", "tags": ["Lê Thị Mai", "[NGƯỜI_9]"]}]}
