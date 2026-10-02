"""The two Vietnamese charts of accounts a tenant can start from.

TT200/2014/TT-BTC is the regime for most enterprises, TT133/2016/TT-BTC the simplified
one for small and medium ones. Only accounts a company actually posts to day to day are
listed below level 1; anything else arrives with the company's own chart import, which
adds to or replaces these rows.
"""

from __future__ import annotations

# (code, name). A level-2 account's parent is its first three digits.
_TT200: tuple[tuple[str, str], ...] = (
    ("111", "Tiền mặt"),
    ("1111", "Tiền Việt Nam"),
    ("1112", "Ngoại tệ"),
    ("112", "Tiền gửi ngân hàng"),
    ("1121", "Tiền Việt Nam"),
    ("1122", "Ngoại tệ"),
    ("113", "Tiền đang chuyển"),
    ("121", "Chứng khoán kinh doanh"),
    ("128", "Đầu tư nắm giữ đến ngày đáo hạn"),
    ("131", "Phải thu của khách hàng"),
    ("133", "Thuế GTGT được khấu trừ"),
    ("1331", "Thuế GTGT được khấu trừ của hàng hóa, dịch vụ"),
    ("1332", "Thuế GTGT được khấu trừ của TSCĐ"),
    ("136", "Phải thu nội bộ"),
    ("138", "Phải thu khác"),
    ("141", "Tạm ứng"),
    ("151", "Hàng mua đang đi đường"),
    ("152", "Nguyên liệu, vật liệu"),
    ("153", "Công cụ, dụng cụ"),
    ("154", "Chi phí sản xuất, kinh doanh dở dang"),
    ("155", "Thành phẩm"),
    ("156", "Hàng hóa"),
    ("157", "Hàng gửi đi bán"),
    ("211", "Tài sản cố định hữu hình"),
    ("212", "Tài sản cố định thuê tài chính"),
    ("213", "Tài sản cố định vô hình"),
    ("214", "Hao mòn tài sản cố định"),
    ("217", "Bất động sản đầu tư"),
    ("221", "Đầu tư vào công ty con"),
    ("222", "Đầu tư vào công ty liên doanh, liên kết"),
    ("228", "Đầu tư khác"),
    ("229", "Dự phòng tổn thất tài sản"),
    ("241", "Xây dựng cơ bản dở dang"),
    ("242", "Chi phí trả trước"),
    ("243", "Tài sản thuế thu nhập hoãn lại"),
    ("244", "Cầm cố, thế chấp, ký quỹ, ký cược"),
    ("331", "Phải trả cho người bán"),
    ("333", "Thuế và các khoản phải nộp Nhà nước"),
    ("3331", "Thuế giá trị gia tăng phải nộp"),
    ("33311", "Thuế GTGT đầu ra"),
    ("3334", "Thuế thu nhập doanh nghiệp"),
    ("3335", "Thuế thu nhập cá nhân"),
    ("3338", "Thuế bảo vệ môi trường và các loại thuế khác"),
    ("3339", "Phí, lệ phí và các khoản phải nộp khác"),
    ("334", "Phải trả người lao động"),
    ("335", "Chi phí phải trả"),
    ("336", "Phải trả nội bộ"),
    ("338", "Phải trả, phải nộp khác"),
    ("3382", "Kinh phí công đoàn"),
    ("3383", "Bảo hiểm xã hội"),
    ("3384", "Bảo hiểm y tế"),
    ("3386", "Bảo hiểm thất nghiệp"),
    ("3387", "Doanh thu chưa thực hiện"),
    ("3388", "Phải trả, phải nộp khác"),
    ("341", "Vay và nợ thuê tài chính"),
    ("344", "Nhận ký quỹ, ký cược"),
    ("347", "Thuế thu nhập hoãn lại phải trả"),
    ("352", "Dự phòng phải trả"),
    ("353", "Quỹ khen thưởng, phúc lợi"),
    ("411", "Vốn đầu tư của chủ sở hữu"),
    ("412", "Chênh lệch đánh giá lại tài sản"),
    ("413", "Chênh lệch tỷ giá hối đoái"),
    ("414", "Quỹ đầu tư phát triển"),
    ("418", "Các quỹ khác thuộc vốn chủ sở hữu"),
    ("419", "Cổ phiếu quỹ"),
    ("421", "Lợi nhuận sau thuế chưa phân phối"),
    ("511", "Doanh thu bán hàng và cung cấp dịch vụ"),
    ("5111", "Doanh thu bán hàng hóa"),
    ("5112", "Doanh thu bán các thành phẩm"),
    ("5113", "Doanh thu cung cấp dịch vụ"),
    ("5118", "Doanh thu khác"),
    ("515", "Doanh thu hoạt động tài chính"),
    ("521", "Các khoản giảm trừ doanh thu"),
    ("611", "Mua hàng"),
    ("621", "Chi phí nguyên liệu, vật liệu trực tiếp"),
    ("622", "Chi phí nhân công trực tiếp"),
    ("623", "Chi phí sử dụng máy thi công"),
    ("627", "Chi phí sản xuất chung"),
    ("631", "Giá thành sản xuất"),
    ("632", "Giá vốn hàng bán"),
    ("635", "Chi phí tài chính"),
    ("641", "Chi phí bán hàng"),
    ("6411", "Chi phí nhân viên"),
    ("6412", "Chi phí nguyên vật liệu, bao bì"),
    ("6413", "Chi phí dụng cụ, đồ dùng"),
    ("6414", "Chi phí khấu hao TSCĐ"),
    ("6415", "Chi phí bảo hành"),
    ("6417", "Chi phí dịch vụ mua ngoài"),
    ("6418", "Chi phí bằng tiền khác"),
    ("642", "Chi phí quản lý doanh nghiệp"),
    ("6421", "Chi phí nhân viên quản lý"),
    ("6422", "Chi phí vật liệu quản lý"),
    ("6423", "Chi phí đồ dùng văn phòng"),
    ("6424", "Chi phí khấu hao TSCĐ"),
    ("6425", "Thuế, phí và lệ phí"),
    ("6426", "Chi phí dự phòng"),
    ("6427", "Chi phí dịch vụ mua ngoài"),
    ("6428", "Chi phí bằng tiền khác"),
    ("711", "Thu nhập khác"),
    ("811", "Chi phí khác"),
    ("821", "Chi phí thuế thu nhập doanh nghiệp"),
    ("911", "Xác định kết quả kinh doanh"),
)

# TT133 has no 641 and no 521: selling and administrative expenses are both 642, and
# revenue deductions are posted against 511 directly.
_TT133: tuple[tuple[str, str], ...] = (
    ("111", "Tiền mặt"),
    ("1111", "Tiền Việt Nam"),
    ("1112", "Ngoại tệ"),
    ("112", "Tiền gửi ngân hàng"),
    ("1121", "Tiền Việt Nam"),
    ("1122", "Ngoại tệ"),
    ("121", "Chứng khoán kinh doanh"),
    ("128", "Đầu tư nắm giữ đến ngày đáo hạn"),
    ("131", "Phải thu của khách hàng"),
    ("133", "Thuế GTGT được khấu trừ"),
    ("1331", "Thuế GTGT được khấu trừ của hàng hóa, dịch vụ"),
    ("1332", "Thuế GTGT được khấu trừ của TSCĐ"),
    ("136", "Phải thu nội bộ"),
    ("138", "Phải thu khác"),
    ("141", "Tạm ứng"),
    ("151", "Hàng mua đang đi đường"),
    ("152", "Nguyên liệu, vật liệu"),
    ("153", "Công cụ, dụng cụ"),
    ("154", "Chi phí sản xuất, kinh doanh dở dang"),
    ("155", "Thành phẩm"),
    ("156", "Hàng hóa"),
    ("157", "Hàng gửi đi bán"),
    ("211", "Tài sản cố định"),
    ("214", "Hao mòn tài sản cố định"),
    ("217", "Bất động sản đầu tư"),
    ("228", "Đầu tư góp vốn vào đơn vị khác"),
    ("229", "Dự phòng tổn thất tài sản"),
    ("241", "Xây dựng cơ bản dở dang"),
    ("242", "Chi phí trả trước"),
    ("331", "Phải trả cho người bán"),
    ("333", "Thuế và các khoản phải nộp Nhà nước"),
    ("3331", "Thuế giá trị gia tăng phải nộp"),
    ("33311", "Thuế GTGT đầu ra"),
    ("3334", "Thuế thu nhập doanh nghiệp"),
    ("3335", "Thuế thu nhập cá nhân"),
    ("3338", "Thuế bảo vệ môi trường và các loại thuế khác"),
    ("3339", "Phí, lệ phí và các khoản phải nộp khác"),
    ("334", "Phải trả người lao động"),
    ("335", "Chi phí phải trả"),
    ("336", "Phải trả nội bộ"),
    ("338", "Phải trả, phải nộp khác"),
    ("3382", "Kinh phí công đoàn"),
    ("3383", "Bảo hiểm xã hội"),
    ("3384", "Bảo hiểm y tế"),
    ("3385", "Bảo hiểm thất nghiệp"),
    ("3386", "Nhận ký quỹ, ký cược"),
    ("3387", "Doanh thu chưa thực hiện"),
    ("3388", "Phải trả, phải nộp khác"),
    ("341", "Vay và nợ thuê tài chính"),
    ("352", "Dự phòng phải trả"),
    ("353", "Quỹ khen thưởng, phúc lợi"),
    ("411", "Vốn đầu tư của chủ sở hữu"),
    ("413", "Chênh lệch tỷ giá hối đoái"),
    ("418", "Các quỹ thuộc vốn chủ sở hữu"),
    ("419", "Cổ phiếu quỹ"),
    ("421", "Lợi nhuận sau thuế chưa phân phối"),
    ("511", "Doanh thu bán hàng và cung cấp dịch vụ"),
    ("5111", "Doanh thu bán hàng hóa"),
    ("5112", "Doanh thu bán thành phẩm"),
    ("5113", "Doanh thu cung cấp dịch vụ"),
    ("5118", "Doanh thu khác"),
    ("515", "Doanh thu hoạt động tài chính"),
    ("611", "Mua hàng"),
    ("631", "Giá thành sản xuất"),
    ("632", "Giá vốn hàng bán"),
    ("635", "Chi phí tài chính"),
    ("642", "Chi phí quản lý kinh doanh"),
    ("6421", "Chi phí bán hàng"),
    ("6422", "Chi phí quản lý doanh nghiệp"),
    ("711", "Thu nhập khác"),
    ("811", "Chi phí khác"),
    ("821", "Chi phí thuế thu nhập doanh nghiệp"),
    ("911", "Xác định kết quả kinh doanh"),
)

CHARTS: dict[str, tuple[tuple[str, str], ...]] = {"TT200": _TT200, "TT133": _TT133}

# Accounts whose balance can legitimately sit on either side (receivables and payables
# carry both advances and debts; tax and profit accounts swing).
_BOTH_SIDES = {"131", "331", "333", "338", "412", "413", "421", "911"}
# Contra accounts: inside an asset or revenue class, but carried on the other side.
_CONTRA_CREDIT = {"214", "229"}
_CONTRA_DEBIT = {"419", "521"}


def normal_balance(code: str) -> str:
    """DEBIT, CREDIT or BOTH -- which side a positive balance of this account sits on."""
    head = code[:3]
    if head in _BOTH_SIDES:
        return "BOTH"
    if head in _CONTRA_CREDIT:
        return "CREDIT"
    if head in _CONTRA_DEBIT:
        return "DEBIT"
    return "CREDIT" if code[:1] in {"3", "4", "5", "7"} else "DEBIT"


def parent_code(code: str) -> str | None:
    """The account one level up: 3 digits is level 1, each further digit a level below."""
    return code[:-1] if len(code) > 3 else None


def chart_rows(chart: str) -> list[dict[str, str | None]]:
    return [
        {
            "code": code,
            "name": name,
            "parent_code": parent_code(code),
            "normal_balance": normal_balance(code),
        }
        for code, name in CHARTS[chart]
    ]
