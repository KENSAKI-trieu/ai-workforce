"""Field schemas for the seven governed Legal Agent document templates."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _field(
    name: str,
    label: str,
    *,
    field_type: str = "text",
    required: bool = True,
    placeholder: str = "",
    help_text: str = "",
    full_width: bool = False,
    options: list[tuple[str, str]] | None = None,
    default: str = "",
    rows: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "label": label,
        "type": field_type,
        "required": required,
        "placeholder": placeholder,
        "help_text": help_text,
        "full_width": full_width,
        "default": default,
    }
    if options:
        result["options"] = [
            {"value": value, "label": option_label}
            for value, option_label in options
        ]
    if rows:
        result["rows"] = rows
    return result


DOCUMENT_SCHEMAS: dict[str, dict[str, Any]] = {
    "NDA": {
        "id": "NDA",
        "label": "Thỏa thuận bảo mật (NDA)",
        "description": "Bảo vệ thông tin trao đổi giữa hai bên theo mô hình một chiều hoặc hai chiều.",
        "output_description": "NDA hoàn chỉnh với phạm vi sử dụng, ngoại lệ, hoàn trả dữ liệu và cơ chế xử lý vi phạm.",
        "clauses": [
            "Thông tin mật và ngoại lệ",
            "Mục đích sử dụng được phép",
            "Nghĩa vụ bên nhận",
            "Công bố theo yêu cầu pháp luật",
            "Hoàn trả hoặc tiêu hủy",
            "Sở hữu trí tuệ",
            "Thời hạn và hiệu lực sau chấm dứt",
            "Luật áp dụng và giải quyết tranh chấp",
        ],
        "fields": [
            _field("nda_type", "Loại NDA", field_type="select", options=[("mutual", "Hai chiều"), ("unilateral", "Một chiều")], default="mutual"),
            _field("party_a", "Bên A", placeholder="Tên pháp lý của doanh nghiệp"),
            _field("party_b", "Bên B", placeholder="Tên cá nhân hoặc đối tác"),
            _field("purpose", "Mục đích chia sẻ", field_type="textarea", placeholder="Ví dụ: đánh giá cơ hội hợp tác Project X", full_width=True, rows=3),
            _field("confidential_information", "Phạm vi thông tin mật", field_type="textarea", placeholder="Mã nguồn, dữ liệu khách hàng, tài liệu kỹ thuật...", full_width=True, rows=3),
            _field("effective_date", "Ngày hiệu lực", field_type="date"),
            _field("duration", "Thời hạn NDA", placeholder="Ví dụ: 2 năm"),
            _field("confidentiality_duration", "Thời hạn bảo mật", placeholder="Ví dụ: 3 năm sau khi chấm dứt", help_text="Không nên để vô thời hạn, trừ bí mật kinh doanh."),
            _field("governing_law", "Luật áp dụng", field_type="select", options=[("Việt Nam", "Việt Nam"), ("Singapore", "Singapore"), ("Khác", "Khác")], default="Việt Nam"),
            _field("dispute_resolution", "Giải quyết tranh chấp", placeholder="Thương lượng, trọng tài hoặc tòa án", full_width=True),
        ],
    },
    "EMPLOYMENT_CONTRACT": {
        "id": "EMPLOYMENT_CONTRACT",
        "label": "Hợp đồng lao động",
        "description": "Bản nháp theo dữ liệu nhân sự và các điều khoản tuân thủ lao động cốt lõi.",
        "output_description": "Hợp đồng lao động hoàn chỉnh; có thể dùng làm nền cho phụ lục công việc hoặc thu nhập.",
        "clauses": ["Công việc và địa điểm", "Thời hạn và thử việc", "Lương và phúc lợi", "Giờ làm và nghỉ phép", "BHXH", "Bảo mật và IP", "Kỷ luật", "Chấm dứt"],
        "fields": [
            _field("company", "Người sử dụng lao động", placeholder="Tên pháp lý công ty"),
            _field("employee", "Người lao động", placeholder="Họ và tên"),
            _field("position", "Chức danh", placeholder="Ví dụ: AI Engineer"),
            _field("department", "Phòng ban", placeholder="Ví dụ: AI"),
            _field("contract_type", "Loại hợp đồng", field_type="select", options=[("Không xác định thời hạn", "Không xác định thời hạn"), ("12 tháng", "Xác định thời hạn 12 tháng"), ("24 tháng", "Xác định thời hạn 24 tháng")], default="12 tháng"),
            _field("start_date", "Ngày bắt đầu", field_type="date"),
            _field("working_location", "Địa điểm làm việc", placeholder="Văn phòng / hybrid / remote"),
            _field("working_hours", "Thời giờ làm việc", placeholder="Ví dụ: 08:30-17:30, Thứ Hai-Thứ Sáu"),
            _field("salary", "Mức lương", placeholder="Ví dụ: 30.000.000 VNĐ/tháng"),
            _field("probation", "Thử việc", placeholder="Ví dụ: 60 ngày, 85% lương"),
            _field("annual_leave", "Nghỉ phép năm", placeholder="Ví dụ: 12 ngày/năm"),
            _field("social_insurance", "BHXH/BHYT/BHTN", placeholder="Theo quy định pháp luật Việt Nam"),
            _field("duties", "Nhiệm vụ chính", field_type="textarea", placeholder="Mỗi nhiệm vụ trên một dòng", full_width=True, rows=4),
        ],
    },
    "FREELANCER_CONTRACT": {
        "id": "FREELANCER_CONTRACT",
        "label": "Hợp đồng Freelancer",
        "description": "Thỏa thuận dịch vụ độc lập, tách biệt rõ với quan hệ lao động.",
        "output_description": "Freelancer Agreement kèm phạm vi, milestone, lịch thanh toán và tiêu chí nghiệm thu.",
        "clauses": ["Phạm vi", "Deliverables", "Milestones", "Thanh toán", "Nghiệm thu", "IP", "Bảo mật", "Chấm dứt", "Tư cách nhà thầu độc lập"],
        "fields": [
            _field("client", "Khách hàng", placeholder="Tên công ty/khách hàng"),
            _field("freelancer", "Freelancer", placeholder="Họ tên hoặc pháp nhân"),
            _field("project_name", "Tên dự án", placeholder="Ví dụ: E-commerce Website"),
            _field("deadline", "Hạn hoàn thành", field_type="date"),
            _field("scope", "Phạm vi công việc", field_type="textarea", full_width=True, rows=4, placeholder="Mô tả rõ hạng mục nằm trong phạm vi"),
            _field("deliverables", "Sản phẩm bàn giao", field_type="textarea", full_width=True, rows=3, placeholder="Mỗi deliverable trên một dòng"),
            _field("milestones", "Milestone", field_type="textarea", full_width=True, rows=4, placeholder="M1 | UI implementation | 30%\nM2 | Backend APIs | 40%\nM3 | Deployment | 30%"),
            _field("payment_schedule", "Lịch thanh toán", placeholder="Ví dụ: 30% / 40% / 30%"),
            _field("acceptance_criteria", "Tiêu chí nghiệm thu", field_type="textarea", full_width=True, rows=3),
            _field("ip_ownership", "Sở hữu IP/mã nguồn", field_type="select", options=[("Khách hàng sau khi thanh toán đủ", "Khách hàng sau khi thanh toán đủ"), ("Freelancer", "Freelancer"), ("Chia sẻ theo phạm vi cấp phép", "Chia sẻ theo phạm vi cấp phép")], default="Khách hàng sau khi thanh toán đủ"),
            _field("confidentiality_duration", "Thời hạn bảo mật", placeholder="Ví dụ: 3 năm"),
            _field("termination_notice", "Thông báo chấm dứt", placeholder="Ví dụ: 15 ngày"),
        ],
    },
    "INTERNSHIP_CONTRACT": {
        "id": "INTERNSHIP_CONTRACT",
        "label": "Thỏa thuận thực tập",
        "description": "Thỏa thuận riêng cho chương trình thực tập, không dùng thay hợp đồng lao động.",
        "output_description": "Internship Agreement với nhiệm vụ, mentor, lịch làm việc, bảo mật và quyền sở hữu sản phẩm.",
        "clauses": ["Mục tiêu thực tập", "Nhiệm vụ", "Lịch làm việc", "Trợ cấp", "Bảo mật", "IP", "Chính sách công ty", "Chấm dứt", "Xác nhận hoàn thành"],
        "fields": [
            _field("company", "Doanh nghiệp", placeholder="Tên pháp lý công ty"),
            _field("intern", "Thực tập sinh", placeholder="Họ và tên"),
            _field("university", "Trường đại học", placeholder="Tên trường"),
            _field("department", "Phòng ban", placeholder="Ví dụ: Engineering"),
            _field("supervisor", "Người hướng dẫn", placeholder="Họ tên/chức danh"),
            _field("start_date", "Ngày bắt đầu", field_type="date"),
            _field("end_date", "Ngày kết thúc", field_type="date"),
            _field("working_schedule", "Lịch thực tập", placeholder="Ví dụ: 4 buổi/tuần"),
            _field("allowance", "Trợ cấp", placeholder="Ví dụ: 5.000.000 VNĐ/tháng"),
            _field("responsibilities", "Nhiệm vụ", field_type="textarea", full_width=True, rows=4, placeholder="Mỗi nhiệm vụ trên một dòng"),
            _field("confidentiality_duration", "Thời hạn bảo mật", placeholder="Ví dụ: 3 năm"),
            _field("ip_ownership", "Quyền với sản phẩm thực tập", placeholder="Ví dụ: công ty sở hữu sản phẩm tạo ra trong phạm vi nhiệm vụ", full_width=True),
        ],
    },
    "SERVICE_AGREEMENT": {
        "id": "SERVICE_AGREEMENT",
        "label": "Hợp đồng dịch vụ",
        "description": "Hợp đồng cung cấp dịch vụ có SLA, nghĩa vụ khách hàng và giới hạn trách nhiệm.",
        "output_description": "Service Agreement/MSA với phạm vi, phí, SLA, dữ liệu, IP, liability và chấm dứt.",
        "clauses": ["Phạm vi dịch vụ", "Deliverables", "Phí và thanh toán", "Nghĩa vụ khách hàng", "SLA", "Bảo mật", "Dữ liệu", "IP", "Liability cap", "Chấm dứt"],
        "fields": [
            _field("provider", "Nhà cung cấp", placeholder="Tên công ty cung cấp dịch vụ"),
            _field("customer", "Khách hàng", placeholder="Tên khách hàng"),
            _field("start_date", "Ngày bắt đầu", field_type="date"),
            _field("end_date", "Ngày kết thúc", field_type="date"),
            _field("service_description", "Mô tả dịch vụ", field_type="textarea", full_width=True, rows=4),
            _field("deliverables", "Sản phẩm bàn giao", field_type="textarea", full_width=True, rows=3),
            _field("fee", "Phí dịch vụ", placeholder="Giá trị và đơn vị tiền tệ"),
            _field("payment_schedule", "Lịch thanh toán", placeholder="Ví dụ: hàng tháng, Net 30"),
            _field("sla", "Cam kết SLA", field_type="textarea", full_width=True, rows=3, placeholder="Availability, response time, service credits..."),
            _field("customer_obligations", "Nghĩa vụ khách hàng", field_type="textarea", full_width=True, rows=3),
            _field("liability_cap", "Giới hạn trách nhiệm", placeholder="Ví dụ: tổng phí đã trả trong 12 tháng"),
            _field("termination_notice", "Thông báo chấm dứt", placeholder="Ví dụ: 30 ngày"),
            _field("governing_law", "Luật áp dụng", default="Việt Nam"),
        ],
    },
    "SOFTWARE_DEVELOPMENT_CONTRACT": {
        "id": "SOFTWARE_DEVELOPMENT_CONTRACT",
        "label": "Hợp đồng phát triển phần mềm",
        "description": "Gói hợp đồng phần mềm gồm Agreement, SOW, đặc tả, milestone và tiêu chí nghiệm thu.",
        "output_description": "Software Development Agreement + SOW + Technical Scope + Milestone Schedule + Acceptance Criteria.",
        "clauses": ["SOW", "Phạm vi kỹ thuật", "Milestones", "Change Request", "Nghiệm thu", "IP/mã nguồn", "OSS/API bên thứ ba", "Bảo hành", "Bảo mật và dữ liệu", "Chậm bàn giao"],
        "fields": [
            _field("developer", "Đơn vị phát triển", placeholder="Tên công ty phần mềm"),
            _field("client", "Khách hàng", placeholder="Tên khách hàng"),
            _field("project_name", "Tên dự án", placeholder="Ví dụ: E-commerce Platform"),
            _field("price", "Giá trị dự án", placeholder="Ví dụ: 500.000.000 VNĐ"),
            _field("start_date", "Ngày bắt đầu", field_type="date"),
            _field("delivery_date", "Ngày bàn giao", field_type="date"),
            _field("requirements", "Yêu cầu và danh sách tính năng", field_type="textarea", full_width=True, rows=5, placeholder="Authentication\nProduct Management\nCart & Checkout\nAdmin Dashboard"),
            _field("tech_stack", "Tech stack", placeholder="Frontend, backend, database, cloud", full_width=True),
            _field("milestones", "Milestone và thanh toán", field_type="textarea", full_width=True, rows=4, placeholder="M1 | Thiết kế | 20%\nM2 | MVP | 40%\nM3 | UAT & Production | 40%"),
            _field("repository", "Repository", placeholder="Nơi lưu trữ và quyền truy cập"),
            _field("deployment", "Triển khai/hosting", placeholder="Môi trường, tài khoản và trách nhiệm vận hành"),
            _field("acceptance_criteria", "Tiêu chí nghiệm thu", field_type="textarea", full_width=True, rows=4, placeholder="Test case, thời hạn UAT, cách xử lý lỗi và mặc nhiên nghiệm thu"),
            _field("change_request_process", "Quy trình Change Request", field_type="textarea", full_width=True, rows=3, placeholder="Yêu cầu thay đổi, estimate, phê duyệt chi phí và timeline"),
            _field("ip_ownership", "Sở hữu mã nguồn", field_type="select", options=[("Khách hàng sau khi thanh toán đủ", "Khách hàng sau khi thanh toán đủ"), ("Đơn vị phát triển", "Đơn vị phát triển"), ("Phân tách background IP và deliverables", "Phân tách background IP và deliverables")], default="Phân tách background IP và deliverables"),
            _field("warranty", "Bảo hành lỗi", placeholder="Ví dụ: 30 ngày sau nghiệm thu"),
            _field("maintenance_mode", "Bảo trì sau bảo hành", field_type="select", options=[("Hợp đồng riêng", "Hợp đồng bảo trì riêng"), ("Bao gồm trong hợp đồng", "Bao gồm trong hợp đồng"), ("Không bao gồm", "Không bao gồm")], default="Hợp đồng riêng"),
            _field("security_data", "Bảo mật và dữ liệu", field_type="textarea", full_width=True, rows=3, placeholder="Dữ liệu cá nhân, API key, backup, tiêu chuẩn bảo mật"),
        ],
    },
    "MAINTENANCE_CONTRACT": {
        "id": "MAINTENANCE_CONTRACT",
        "label": "Hợp đồng bảo trì phần mềm",
        "description": "Tách rõ sửa lỗi, hỗ trợ vận hành và yêu cầu phát triển tính năng mới.",
        "output_description": "Maintenance Agreement kèm phụ lục SLA và ma trận mức độ sự cố.",
        "clauses": ["Phạm vi bảo trì", "Dịch vụ loại trừ", "Bug vs New Feature", "Giờ hỗ trợ", "SLA severity", "Backup và bảo mật", "Phí", "Chấm dứt"],
        "fields": [
            _field("provider", "Đơn vị bảo trì", placeholder="Tên nhà cung cấp"),
            _field("customer", "Khách hàng", placeholder="Tên khách hàng"),
            _field("system_name", "Hệ thống", placeholder="Tên hệ thống/sản phẩm"),
            _field("start_date", "Ngày bắt đầu", field_type="date"),
            _field("duration", "Thời hạn bảo trì", placeholder="Ví dụ: 12 tháng"),
            _field("monthly_fee", "Phí hàng tháng", placeholder="Ví dụ: 20.000.000 VNĐ/tháng"),
            _field("support_hours", "Giờ hỗ trợ", placeholder="Ví dụ: 08:30-17:30 ngày làm việc"),
            _field("included_services", "Dịch vụ bao gồm", field_type="textarea", full_width=True, rows=4, placeholder="Sửa lỗi, giám sát, cập nhật bảo mật..."),
            _field("excluded_services", "Dịch vụ loại trừ", field_type="textarea", full_width=True, rows=3, placeholder="Feature mới, tích hợp mới, lỗi do bên thứ ba..."),
            _field("bug_vs_feature", "Phân biệt bug và feature mới", field_type="textarea", full_width=True, rows=3, placeholder="Bug là sai lệch so với acceptance criteria; feature mới phải qua Change Request"),
            _field("sla_critical", "Critical - hệ thống down", placeholder="Ví dụ: phản hồi 1 giờ"),
            _field("sla_high", "High - chức năng chính lỗi", placeholder="Ví dụ: phản hồi 4 giờ"),
            _field("sla_medium", "Medium - lỗi tính năng phụ", placeholder="Ví dụ: 1 ngày làm việc"),
            _field("sla_low", "Low - lỗi nhỏ/UI", placeholder="Ví dụ: 3 ngày làm việc"),
            _field("backup_security", "Backup và bảo mật", field_type="textarea", full_width=True, rows=3),
            _field("termination_notice", "Thông báo chấm dứt", placeholder="Ví dụ: 30 ngày"),
        ],
    },
}


def get_document_schema(document_type: str) -> dict[str, Any]:
    normalized = document_type.upper()
    if normalized not in DOCUMENT_SCHEMAS:
        raise ValueError("Loại văn bản không được hỗ trợ")
    schema = deepcopy(DOCUMENT_SCHEMAS[normalized])
    schema["required_fields"] = [
        field["name"] for field in schema["fields"] if field["required"]
    ]
    return schema


def list_document_schemas() -> list[dict[str, Any]]:
    return [get_document_schema(document_type) for document_type in DOCUMENT_SCHEMAS]
