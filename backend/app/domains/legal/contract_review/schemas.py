"""Review checklists and source registry for supported contract types."""

from __future__ import annotations

from typing import Any


LAW_SOURCES: dict[str, dict[str, str]] = {
    "COMMERCIAL_LAW_ARTICLE_301": {
        "id": "COMMERCIAL_LAW_ARTICLE_301",
        "source_type": "LAW_REFERENCE",
        "title": "Luật Thương mại 36/2005/QH11 - Điều 301",
        "url": "https://vbpl.vn/bocongthuong/Pages/vbpq-toanvan.aspx?ItemID=26117&dvid=218",
        "note": "Mức phạt trong hợp đồng thương mại cần được Legal xác nhận theo phạm vi áp dụng và ngoại lệ.",
    },
    "PERSONAL_DATA_PROTECTION_2025": {
        "id": "PERSONAL_DATA_PROTECTION_2025",
        "source_type": "LAW_REFERENCE",
        "title": "Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15",
        "url": "https://vbpl.vn/tw/Pages/vbpq-thuoctinh.aspx?ItemID=179252",
        "note": "Có hiệu lực từ 01/01/2026; cần đối chiếu thêm Nghị định 356/2025/NĐ-CP và tình huống xử lý cụ thể.",
    },
    "PERSONAL_DATA_PROTECTION_DECREE_356_2025": {
        "id": "PERSONAL_DATA_PROTECTION_DECREE_356_2025",
        "source_type": "LAW_REFERENCE",
        "title": "Nghị định 356/2025/NĐ-CP hướng dẫn Luật Bảo vệ dữ liệu cá nhân",
        "url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=187276",
        "note": "Có hiệu lực từ 01/01/2026 và thay thế Nghị định 13/2023/NĐ-CP; Legal cần xác nhận nghĩa vụ áp dụng cho từng hoạt động xử lý.",
    },
    "LABOR_CODE_2019": {
        "id": "LABOR_CODE_2019",
        "source_type": "LAW_REFERENCE",
        "title": "Bộ luật Lao động 45/2019/QH14",
        "url": "https://vbpl.vn/bolaodong/Pages/vbpq-thuoctinh.aspx?ItemID=139264",
        "note": "Nguồn tham chiếu cho hợp đồng lao động; một phần văn bản có thể đã được sửa đổi và phải được Legal xác nhận.",
    },
}


def _item(
    category: str,
    label: str,
    patterns: list[str],
    *,
    missing_severity: str = "MEDIUM",
    reason: str,
    recommendation: str,
    suggested_revision: str,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "label": label,
        "patterns": patterns,
        "missing_severity": missing_severity,
        "reason": reason,
        "recommendation": recommendation,
        "suggested_revision": suggested_revision,
        "source_ids": source_ids or [],
    }


COMMON = {
    "PAYMENT": _item("PAYMENT", "Thanh toán", [r"thanh toán", r"payment", r"hóa đơn", r"invoice"], reason="Thiếu cơ chế thanh toán làm tăng tranh chấp về thời điểm và điều kiện đến hạn.", recommendation="Bổ sung giá trị, lịch thanh toán, hóa đơn, thuế, chậm trả và quyền tạm ngừng.", suggested_revision="Khoản thanh toán đến hạn trong [30] ngày kể từ khi Bên thanh toán nhận được hóa đơn hợp lệ và hồ sơ nghiệm thu tương ứng."),
    "TERMINATION": _item("TERMINATION", "Chấm dứt", [r"chấm dứt", r"thanh lý", r"terminat"], reason="Thiếu điều khoản chấm dứt khiến quyền thoát khỏi hợp đồng và nghĩa vụ bàn giao không rõ.", recommendation="Bổ sung chấm dứt do vi phạm, thời gian khắc phục, chấm dứt thuận tiện và hậu quả sau chấm dứt.", suggested_revision="Mỗi Bên có quyền chấm dứt khi Bên kia vi phạm trọng yếu và không khắc phục trong 15 ngày kể từ thông báo; chấm dứt thuận tiện cần báo trước 30 ngày."),
    "CONFIDENTIALITY": _item("CONFIDENTIALITY", "Bảo mật", [r"bảo mật", r"thông tin mật", r"confidential", r"non.disclosure"], reason="Thiếu bảo mật có thể làm lộ dữ liệu, mã nguồn hoặc bí mật kinh doanh.", recommendation="Xác định thông tin mật, ngoại lệ, mục đích sử dụng, người được phép nhận và thời hạn tiếp tục.", suggested_revision="Bên nhận chỉ sử dụng Thông tin mật để thực hiện Hợp đồng, giới hạn truy cập theo nhu cầu biết và áp dụng biện pháp bảo vệ phù hợp."),
    "LIABILITY": _item("LIABILITY", "Giới hạn trách nhiệm", [r"giới hạn trách nhiệm", r"trách nhiệm tối đa", r"liability cap", r"aggregate liability", r"không vượt quá"], missing_severity="HIGH", reason="Không có mức trần trách nhiệm có thể tạo nghĩa vụ tài chính không dự đoán được.", recommendation="Bổ sung mức trần và ngoại lệ hẹp cho gian lận, cố ý vi phạm hoặc nghĩa vụ không thể giới hạn.", suggested_revision="Trừ gian lận, cố ý vi phạm và nghĩa vụ không thể giới hạn theo luật, tổng trách nhiệm của mỗi Bên không vượt quá tổng phí đã thanh toán trong 12 tháng trước sự kiện."),
    "INTELLECTUAL_PROPERTY": _item("INTELLECTUAL_PROPERTY", "Sở hữu trí tuệ", [r"sở hữu trí tuệ", r"mã nguồn", r"intellectual property", r"source code", r"ownership"], missing_severity="HIGH", reason="Thiếu cơ chế sở hữu làm phát sinh tranh chấp đối với deliverable, mã nguồn và tài sản có trước.", recommendation="Tách background IP, deliverable, thành phần bên thứ ba và điều kiện chuyển giao sau thanh toán.", suggested_revision="Background IP vẫn thuộc chủ sở hữu ban đầu. Quyền đối với Deliverable chỉ chuyển giao sau khi Bên nhận hoàn thành toàn bộ nghĩa vụ thanh toán."),
    "DATA_PROTECTION": _item("DATA_PROTECTION", "Bảo vệ dữ liệu", [r"dữ liệu cá nhân", r"bảo vệ dữ liệu", r"personal data", r"data protection", r"xử lý dữ liệu"], missing_severity="MEDIUM", reason="Hợp đồng có thể thiếu vai trò, mục đích, biện pháp bảo vệ và xử lý sự cố dữ liệu.", recommendation="Xác định vai trò các Bên, mục đích xử lý, bảo mật, nhà thầu phụ, sự cố và hoàn trả/xóa dữ liệu.", suggested_revision="Mỗi Bên chỉ xử lý dữ liệu cá nhân cho mục đích đã thống nhất, áp dụng biện pháp bảo vệ phù hợp, kiểm soát bên xử lý phụ và thông báo sự cố không chậm trễ.", source_ids=["PERSONAL_DATA_PROTECTION_2025", "PERSONAL_DATA_PROTECTION_DECREE_356_2025"]),
    "GOVERNING_LAW": _item("GOVERNING_LAW", "Luật áp dụng", [r"luật áp dụng", r"pháp luật", r"governing law", r"laws of"], reason="Thiếu luật áp dụng làm tăng bất định khi giải thích hợp đồng.", recommendation="Chọn luật áp dụng và cơ chế giải quyết tranh chấp phù hợp.", suggested_revision="Hợp đồng này được điều chỉnh bởi pháp luật Việt Nam. Các Bên ưu tiên thương lượng trước khi đưa tranh chấp ra cơ quan có thẩm quyền."),
}


def _pick(*categories: str) -> list[dict[str, Any]]:
    return [dict(COMMON[category]) for category in categories]


CONTRACT_REVIEW_SCHEMAS: dict[str, dict[str, Any]] = {
    "NDA": {
        "label": "Thỏa thuận bảo mật (NDA)",
        "detection_patterns": [r"non.?disclosure", r"nda\b", r"thỏa thuận bảo mật", r"thông tin mật"],
        "checklist": [
            _item("CONFIDENTIAL_INFORMATION", "Định nghĩa thông tin mật", [r"thông tin mật", r"confidential information"], missing_severity="HIGH", reason="Không xác định phạm vi làm nghĩa vụ bảo mật quá rộng hoặc khó thi hành.", recommendation="Định nghĩa rõ hình thức, nội dung và cách đánh dấu thông tin mật.", suggested_revision="Thông tin mật bao gồm thông tin kỹ thuật, thương mại và vận hành được tiết lộ bằng mọi hình thức và được xác định hợp lý là mật."),
            _item("PURPOSE", "Mục đích sử dụng", [r"mục đích", r"purpose", r"permitted use"], reason="Thiếu mục đích khiến Bên nhận không biết giới hạn sử dụng.", recommendation="Giới hạn việc sử dụng theo giao dịch hoặc dự án cụ thể.", suggested_revision="Bên nhận chỉ sử dụng Thông tin mật để đánh giá và thực hiện [Mục đích]."),
            _item("EXCLUSIONS", "Ngoại lệ", [r"ngoại lệ", r"không bao gồm", r"exclusions", r"does not include"], reason="Không có ngoại lệ có thể biến thông tin công khai hoặc phát triển độc lập thành thông tin mật.", recommendation="Bổ sung các ngoại lệ chuẩn và nghĩa vụ chứng minh.", suggested_revision="Thông tin mật không bao gồm thông tin đã công khai hợp pháp, đã có trước, nhận hợp pháp từ bên thứ ba hoặc được phát triển độc lập."),
            _item("PERMITTED_DISCLOSURE", "Tiết lộ được phép", [r"yêu cầu.*pháp luật", r"required by law", r"cơ quan.*thẩm quyền", r"permitted disclosure"], reason="Thiếu ngoại lệ bắt buộc có thể xung đột với nghĩa vụ pháp lý.", recommendation="Cho phép tiết lộ tối thiểu khi pháp luật yêu cầu và thông báo trước nếu được phép.", suggested_revision="Khi pháp luật yêu cầu, Bên nhận chỉ tiết lộ phần tối thiểu cần thiết và thông báo trước cho Bên tiết lộ trong phạm vi được phép."),
            _item("RETURN_DESTRUCTION", "Hoàn trả hoặc tiêu hủy", [r"hoàn trả", r"tiêu hủy", r"return", r"destroy"], reason="Không có cơ chế kết thúc làm dữ liệu tiếp tục tồn tại ngoài nhu cầu.", recommendation="Bổ sung hoàn trả/xóa và ngoại lệ sao lưu bắt buộc.", suggested_revision="Theo yêu cầu, Bên nhận hoàn trả hoặc tiêu hủy Thông tin mật, trừ bản lưu bắt buộc theo luật hoặc chính sách sao lưu hợp lệ."),
            _item("TERM_SURVIVAL", "Thời hạn và survival", [r"thời hạn", r"có hiệu lực", r"surviv", r"term"], reason="Thiếu thời hạn làm nghĩa vụ không xác định điểm kết thúc.", recommendation="Tách thời hạn NDA và thời gian nghĩa vụ bảo mật tiếp tục.", suggested_revision="NDA có hiệu lực trong 2 năm; nghĩa vụ bảo mật tiếp tục 3 năm sau chấm dứt, riêng bí mật kinh doanh theo thời gian được pháp luật bảo vệ."),
            COMMON["GOVERNING_LAW"],
        ],
    },
    "EMPLOYMENT_CONTRACT": {
        "label": "Hợp đồng lao động",
        "detection_patterns": [r"hợp đồng lao động", r"employment contract", r"người lao động", r"người sử dụng lao động"],
        "checklist": [
            _item("POSITION_DUTIES", "Vị trí và công việc", [r"chức danh", r"vị trí", r"nhiệm vụ", r"position", r"duties"], missing_severity="HIGH", reason="Thiếu mô tả công việc làm phạm vi nghĩa vụ không rõ.", recommendation="Nêu chức danh, phòng ban, quản lý trực tiếp và nhiệm vụ chính.", suggested_revision="Người lao động đảm nhiệm vị trí [Chức danh] và thực hiện nhiệm vụ theo mô tả công việc đính kèm."),
            _item("SALARY_BENEFITS", "Lương và phúc lợi", [r"tiền lương", r"mức lương", r"salary", r"benefit"], missing_severity="HIGH", reason="Lương là nội dung trọng yếu của quan hệ lao động.", recommendation="Nêu mức lương, kỳ trả, phụ cấp, thưởng, khấu trừ và phương thức thanh toán.", suggested_revision="Mức lương trước thuế là [Số tiền]/tháng, thanh toán vào [Ngày] qua tài khoản ngân hàng.", source_ids=["LABOR_CODE_2019"]),
            _item("WORKING_TIME", "Thời giờ làm việc", [r"thời giờ làm việc", r"giờ làm", r"working hours"], reason="Thiếu lịch làm việc làm tăng rủi ro tranh chấp thời gian và làm thêm.", recommendation="Nêu giờ làm, nghỉ, làm thêm và cơ chế phê duyệt.", suggested_revision="Thời giờ làm việc là [Lịch]; làm thêm chỉ thực hiện khi được phê duyệt và theo giới hạn pháp luật.", source_ids=["LABOR_CODE_2019"]),
            _item("LEAVE_INSURANCE", "Nghỉ phép và bảo hiểm", [r"nghỉ phép", r"nghỉ hằng năm", r"bảo hiểm xã hội", r"annual leave", r"social insurance"], reason="Thiếu quyền lợi bắt buộc làm tăng rủi ro tuân thủ.", recommendation="Nêu nghỉ phép, BHXH/BHYT/BHTN và phúc lợi áp dụng.", suggested_revision="Người lao động được hưởng nghỉ hằng năm và tham gia các chế độ bảo hiểm theo pháp luật và chính sách công ty.", source_ids=["LABOR_CODE_2019"]),
            COMMON["CONFIDENTIALITY"], COMMON["INTELLECTUAL_PROPERTY"], COMMON["TERMINATION"],
        ],
    },
    "FREELANCER_CONTRACT": {
        "label": "Hợp đồng Freelancer",
        "detection_patterns": [r"freelancer", r"independent contractor", r"nhà thầu độc lập", r"cộng tác viên"],
        "checklist": [
            _item("SCOPE", "Phạm vi công việc", [r"phạm vi", r"scope", r"statement of work", r"công việc"], missing_severity="HIGH", reason="Scope không rõ dẫn đến feature creep và tranh chấp deliverable.", recommendation="Liệt kê hạng mục trong/ngoài phạm vi.", suggested_revision="Phạm vi chỉ bao gồm các hạng mục tại Phụ lục SOW; mọi yêu cầu ngoài phạm vi phải qua Change Request."),
            _item("DELIVERABLES", "Sản phẩm bàn giao", [r"bàn giao", r"deliverable", r"sản phẩm"], missing_severity="HIGH", reason="Không xác định đầu ra khiến nghiệm thu thiếu căn cứ.", recommendation="Nêu deliverable, định dạng và tài liệu đi kèm.", suggested_revision="Freelancer bàn giao [Danh sách deliverable] theo định dạng và repository được chỉ định."),
            _item("ACCEPTANCE", "Nghiệm thu", [r"nghiệm thu", r"acceptance", r"uat"], missing_severity="HIGH", reason="Thiếu nghiệm thu làm thanh toán và hoàn thành không rõ.", recommendation="Bổ sung test, thời hạn phản hồi và cơ chế mặc nhiên nghiệm thu.", suggested_revision="Khách hàng có 10 ngày làm việc để nghiệm thu hoặc từ chối bằng văn bản kèm lỗi có thể tái hiện."),
            COMMON["PAYMENT"], COMMON["INTELLECTUAL_PROPERTY"], COMMON["CONFIDENTIALITY"], COMMON["TERMINATION"],
        ],
    },
    "INTERNSHIP_CONTRACT": {
        "label": "Thỏa thuận thực tập",
        "detection_patterns": [r"thực tập", r"internship", r"thực tập sinh", r"intern\b"],
        "checklist": [
            _item("INTERNSHIP_PURPOSE", "Mục tiêu thực tập", [r"mục tiêu", r"đào tạo", r"internship purpose"], reason="Thiếu mục tiêu làm chương trình dễ bị hiểu sai thành quan hệ lao động không được thiết kế đúng.", recommendation="Nêu mục tiêu đào tạo, mentor và kết quả học tập.", suggested_revision="Chương trình nhằm cung cấp trải nghiệm đào tạo thực tế dưới sự hướng dẫn của [Mentor]."),
            _item("RESPONSIBILITIES", "Nhiệm vụ", [r"nhiệm vụ", r"trách nhiệm", r"responsibilities"], reason="Thiếu nhiệm vụ làm phạm vi truy cập và đánh giá không rõ.", recommendation="Liệt kê nhiệm vụ và giới hạn quyền truy cập.", suggested_revision="Thực tập sinh chỉ thực hiện các nhiệm vụ tại Phụ lục và truy cập hệ thống theo nguyên tắc tối thiểu."),
            _item("ALLOWANCE", "Trợ cấp", [r"trợ cấp", r"allowance", r"hỗ trợ"], reason="Thiếu thông tin trợ cấp gây kỳ vọng khác nhau.", recommendation="Nêu số tiền, kỳ trả và tính chất khoản hỗ trợ.", suggested_revision="Trợ cấp thực tập là [Số tiền]/tháng, thanh toán theo số ngày tham gia thực tế."),
            COMMON["CONFIDENTIALITY"], COMMON["INTELLECTUAL_PROPERTY"], COMMON["TERMINATION"],
        ],
    },
    "SERVICE_AGREEMENT": {
        "label": "Hợp đồng dịch vụ",
        "detection_patterns": [r"hợp đồng dịch vụ", r"service agreement", r"cung cấp dịch vụ", r"scope of services"],
        "checklist": [
            _item("SCOPE", "Phạm vi dịch vụ", [r"phạm vi", r"scope of services", r"mô tả dịch vụ"], missing_severity="HIGH", reason="Thiếu scope khiến nghĩa vụ và giá không thể kiểm soát.", recommendation="Nêu dịch vụ, deliverable, giả định và ngoại lệ.", suggested_revision="Nhà cung cấp chỉ thực hiện các dịch vụ mô tả tại SOW; công việc ngoài phạm vi cần Change Request."),
            _item("SLA", "Mức dịch vụ", [r"sla", r"mức dịch vụ", r"service level", r"thời gian phản hồi"], reason="Thiếu SLA làm chất lượng và khắc phục dịch vụ không đo được.", recommendation="Nêu availability, response, resolution và service credit.", suggested_revision="Nhà cung cấp đáp ứng SLA tại Phụ lục; vi phạm lặp lại cho phép áp dụng service credit và quyền chấm dứt."),
            COMMON["PAYMENT"], COMMON["CONFIDENTIALITY"], COMMON["DATA_PROTECTION"], COMMON["INTELLECTUAL_PROPERTY"], COMMON["LIABILITY"], COMMON["TERMINATION"], COMMON["GOVERNING_LAW"],
        ],
    },
    "SOFTWARE_DEVELOPMENT_CONTRACT": {
        "label": "Hợp đồng phát triển phần mềm",
        "detection_patterns": [r"phát triển phần mềm", r"software development", r"source code", r"mã nguồn", r"repository", r"uat"],
        "checklist": [
            _item("SCOPE", "Scope of Work", [r"scope of work", r"sow", r"phạm vi", r"yêu cầu chức năng"], missing_severity="HIGH", reason="Scope thiếu làm tăng feature creep và tranh chấp tiến độ.", recommendation="Liệt kê module, vai trò, tích hợp, phi chức năng và nội dung ngoài phạm vi.", suggested_revision="Phạm vi dự án chỉ bao gồm các module và yêu cầu tại SOW; mọi nội dung khác phải qua Change Request."),
            COMMON["PAYMENT"],
            _item("DELIVERY", "Bàn giao", [r"bàn giao", r"delivery", r"deliverable", r"triển khai"], missing_severity="HIGH", reason="Thiếu đầu ra và mốc bàn giao làm tiến độ không kiểm chứng được.", recommendation="Nêu milestone, deliverable, repository và tài liệu bàn giao.", suggested_revision="Bên phát triển bàn giao mã nguồn, build, tài liệu và quyền truy cập tương ứng với từng milestone."),
            _item("CHANGE_REQUEST", "Change Request", [r"change request", r"yêu cầu thay đổi", r"thay đổi phạm vi"], reason="Không có Change Request làm thay đổi scope mà không điều chỉnh phí/timeline.", recommendation="Bắt buộc đánh giá tác động và phê duyệt trước khi thực hiện.", suggested_revision="Mọi thay đổi ngoài SOW phải được lập thành Change Request, nêu tác động chi phí và timeline, và chỉ có hiệu lực sau khi hai Bên phê duyệt."),
            _item("ACCEPTANCE", "Nghiệm thu", [r"nghiệm thu", r"acceptance", r"uat", r"test case"], missing_severity="HIGH", reason="Thiếu tiêu chí nghiệm thu khiến hoàn thành và thanh toán không rõ.", recommendation="Bổ sung test case, thời hạn UAT, mức lỗi và cơ chế mặc nhiên nghiệm thu.", suggested_revision="Khách hàng có 10 ngày làm việc để UAT; từ chối phải nêu lỗi đối chiếu với Acceptance Criteria."),
            COMMON["CONFIDENTIALITY"], COMMON["LIABILITY"], COMMON["INTELLECTUAL_PROPERTY"], COMMON["DATA_PROTECTION"], COMMON["TERMINATION"],
            _item("WARRANTY", "Bảo hành lỗi", [r"bảo hành", r"warranty", r"bug fix"], reason="Thiếu bảo hành làm nghĩa vụ sửa lỗi sau nghiệm thu không rõ.", recommendation="Nêu thời hạn, định nghĩa bug và ngoại lệ feature mới.", suggested_revision="Bên phát triển sửa lỗi tái hiện được so với Acceptance Criteria trong 60 ngày; feature mới đi qua Change Request."),
        ],
    },
    "MAINTENANCE_CONTRACT": {
        "label": "Hợp đồng bảo trì phần mềm",
        "detection_patterns": [r"bảo trì phần mềm", r"software maintenance", r"maintenance agreement", r"support hours", r"severity"],
        "checklist": [
            _item("MAINTENANCE_SCOPE", "Phạm vi bảo trì", [r"phạm vi bảo trì", r"included services", r"maintenance scope"], missing_severity="HIGH", reason="Thiếu phạm vi khiến mọi yêu cầu có thể bị coi là bảo trì.", recommendation="Liệt kê dịch vụ bao gồm và loại trừ.", suggested_revision="Phí bảo trì chỉ bao gồm sửa lỗi, giám sát và cập nhật nêu tại Phụ lục; phát triển mới nằm ngoài phạm vi."),
            _item("BUG_FEATURE", "Bug và feature mới", [r"feature mới", r"new feature", r"change request", r"ngoài phạm vi"], missing_severity="HIGH", reason="Không phân biệt bug và feature mới tạo rủi ro scope creep.", recommendation="Định nghĩa bug theo acceptance criteria và feature mới qua Change Request.", suggested_revision="Bug là sai lệch có thể tái hiện so với Acceptance Criteria. Mọi chức năng mới hoặc thay đổi hành vi phải qua Change Request."),
            _item("SLA", "SLA severity", [r"critical", r"severity", r"thời gian phản hồi", r"response time"], missing_severity="HIGH", reason="Thiếu SLA làm ưu tiên và thời gian phản hồi không đo được.", recommendation="Thêm severity matrix, response/resolution và giờ tính SLA.", suggested_revision="Critical: phản hồi 1 giờ; High: 4 giờ; Medium: 1 ngày làm việc; Low: 3 ngày làm việc."),
            COMMON["PAYMENT"], COMMON["DATA_PROTECTION"], COMMON["LIABILITY"], COMMON["TERMINATION"],
        ],
    },
}


def get_review_schema(contract_type: str) -> dict[str, Any]:
    return CONTRACT_REVIEW_SCHEMAS.get(contract_type, CONTRACT_REVIEW_SCHEMAS["SERVICE_AGREEMENT"])
