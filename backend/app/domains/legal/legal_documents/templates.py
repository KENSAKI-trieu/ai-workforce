"""Type-specific legal draft structures for the seven supported templates."""

from __future__ import annotations

from typing import Any, Callable


def _value(fields: dict[str, str], name: str) -> str:
    return fields[name].strip()


def _lines(value: str) -> list[str]:
    return [line.strip(" -\t") for line in value.splitlines() if line.strip(" -\t")]


def _section(
    title: str,
    *paragraphs: str,
    bullets: list[str] | None = None,
    table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "title": title,
        "paragraphs": [paragraph for paragraph in paragraphs if paragraph],
    }
    if bullets:
        result["bullets"] = bullets
    if table:
        result["table"] = table
    return result


def _draft(
    *,
    title: str,
    subtitle: str,
    party_a: str,
    party_b: str,
    metadata: list[tuple[str, str]],
    sections: list[dict[str, Any]],
    warnings: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "title": title,
        "subtitle": subtitle,
        "party_a": party_a,
        "party_b": party_b,
        "metadata": metadata,
        "sections": sections,
        "warnings": warnings,
    }


def _nda(fields: dict[str, str], warnings: list[dict[str, str]]) -> dict[str, Any]:
    party_a, party_b = _value(fields, "party_a"), _value(fields, "party_b")
    mutual = _value(fields, "nda_type") == "mutual"
    direction = "hai chiều" if mutual else f"một chiều, trong đó {party_a} là Bên tiết lộ"
    sections = [
        _section("Mục đích và phạm vi sử dụng", f"Các Bên trao đổi thông tin chỉ nhằm mục đích: {_value(fields, 'purpose')}. Bên nhận chỉ được sử dụng thông tin cho mục đích này và không được khai thác cho lợi ích ngoài phạm vi thỏa thuận."),
        _section("Thông tin mật", f"Thông tin mật bao gồm: {_value(fields, 'confidential_information')}. Thông tin có thể được cung cấp bằng văn bản, lời nói, hình ảnh, dữ liệu điện tử hoặc quyền truy cập hệ thống."),
        _section("Ngoại lệ", "Thông tin không được coi là mật nếu Bên nhận chứng minh được rằng thông tin đã công khai hợp pháp; đã có trước khi nhận; được bên thứ ba cung cấp hợp pháp; hoặc được phát triển độc lập mà không sử dụng Thông tin mật."),
        _section("Nghĩa vụ của Bên nhận", "Bên nhận áp dụng mức bảo vệ không thấp hơn mức dùng cho thông tin mật của mình; chỉ tiết lộ cho người có nhu cầu biết và bị ràng buộc nghĩa vụ tương đương; đồng thời thông báo kịp thời khi phát hiện truy cập hoặc tiết lộ trái phép."),
        _section("Công bố theo yêu cầu pháp luật", "Nếu buộc phải công bố theo pháp luật hoặc quyết định của cơ quan có thẩm quyền, Bên nhận phải thông báo trước trong phạm vi pháp luật cho phép và chỉ công bố phần tối thiểu cần thiết."),
        _section("Hoàn trả hoặc tiêu hủy", "Theo yêu cầu của Bên tiết lộ hoặc khi chấm dứt, Bên nhận phải hoàn trả hoặc tiêu hủy Thông tin mật và xác nhận bằng văn bản, trừ bản lưu bắt buộc theo luật hoặc chính sách sao lưu hợp lệ."),
        _section("Sở hữu trí tuệ", "Thỏa thuận không chuyển giao quyền sở hữu trí tuệ. Mọi quyền vẫn thuộc chủ sở hữu ban đầu; không có giấy phép ngầm ngoài quyền sử dụng hạn chế cho Mục đích."),
        _section("Thời hạn và hiệu lực tiếp tục", f"Thỏa thuận có hiệu lực {_value(fields, 'duration')} kể từ {_value(fields, 'effective_date')}. Nghĩa vụ bảo mật tiếp tục trong {_value(fields, 'confidentiality_duration')}."),
        _section("Vi phạm và biện pháp khắc phục", "Bên vi phạm phải chấm dứt hành vi, hạn chế thiệt hại, phối hợp điều tra và bồi thường thiệt hại trực tiếp được chứng minh. Bên bị vi phạm có quyền yêu cầu biện pháp khẩn cấp khi bồi thường bằng tiền không đủ khắc phục."),
        _section("Luật áp dụng và tranh chấp", f"Thỏa thuận chịu sự điều chỉnh của pháp luật {_value(fields, 'governing_law')}. Tranh chấp được xử lý theo cơ chế: {_value(fields, 'dispute_resolution')}."),
    ]
    return _draft(
        title="THỎA THUẬN BẢO MẬT THÔNG TIN",
        subtitle=f"NDA {direction}", party_a=party_a, party_b=party_b,
        metadata=[("Ngày hiệu lực", _value(fields, "effective_date")), ("Thời hạn", _value(fields, "duration")), ("Luật áp dụng", _value(fields, "governing_law"))],
        sections=sections, warnings=warnings,
    )


def _employment(fields: dict[str, str], warnings: list[dict[str, str]]) -> dict[str, Any]:
    company, employee = _value(fields, "company"), _value(fields, "employee")
    sections = [
        _section("Công việc và nhiệm vụ", f"Người lao động làm việc tại vị trí {_value(fields, 'position')}, thuộc {_value(fields, 'department')}. Nhiệm vụ chính:", bullets=_lines(_value(fields, "duties"))),
        _section("Thời hạn và thử việc", f"Loại hợp đồng: {_value(fields, 'contract_type')}; ngày bắt đầu: {_value(fields, 'start_date')}. Thỏa thuận thử việc: {_value(fields, 'probation')}. Việc thử việc phải tuân thủ giới hạn và mức lương tối thiểu theo pháp luật áp dụng."),
        _section("Địa điểm và thời giờ làm việc", f"Địa điểm: {_value(fields, 'working_location')}. Thời giờ làm việc: {_value(fields, 'working_hours')}. Làm thêm giờ, nghỉ giữa ca và nghỉ hàng tuần thực hiện theo phê duyệt và pháp luật."),
        _section("Tiền lương, phúc lợi và bảo hiểm", f"Mức lương: {_value(fields, 'salary')}. Nghỉ phép năm: {_value(fields, 'annual_leave')}. Chế độ bảo hiểm: {_value(fields, 'social_insurance')}. Việc khấu trừ, trả lương và điều chỉnh thu nhập phải có căn cứ hợp lệ."),
        _section("Bảo mật và dữ liệu", "Người lao động bảo vệ mã nguồn, dữ liệu khách hàng, tài khoản, bí mật kinh doanh và tài liệu nội bộ; không chuyển dữ liệu sang hệ thống hoặc dịch vụ chưa được phê duyệt."),
        _section("Sở hữu trí tuệ", "Sản phẩm tạo ra trong phạm vi nhiệm vụ và bằng nguồn lực của doanh nghiệp được quản lý theo pháp luật, chính sách nội bộ và phụ lục IP. Quyền đối với tài sản có trước của Người lao động phải được liệt kê riêng."),
        _section("Nội quy và kỷ luật", "Người lao động tuân thủ nội quy, an toàn thông tin, quy tắc ứng xử và quy trình xử lý sự cố. Mọi xử lý kỷ luật phải đúng căn cứ, trình tự và quyền giải trình."),
        _section("Chấm dứt", "Mỗi Bên có quyền chấm dứt theo căn cứ và thời hạn báo trước do pháp luật quy định. Khi chấm dứt, Người lao động bàn giao công việc, tài sản, tài khoản và dữ liệu; Doanh nghiệp thực hiện nghĩa vụ thanh toán còn lại."),
    ]
    return _draft(title="HỢP ĐỒNG LAO ĐỘNG", subtitle=f"{_value(fields, 'position')} - {_value(fields, 'department')}", party_a=company, party_b=employee, metadata=[("Ngày bắt đầu", _value(fields, "start_date")), ("Loại hợp đồng", _value(fields, "contract_type")), ("Địa điểm", _value(fields, "working_location"))], sections=sections, warnings=warnings)


def _freelancer(fields: dict[str, str], warnings: list[dict[str, str]]) -> dict[str, Any]:
    client, freelancer = _value(fields, "client"), _value(fields, "freelancer")
    milestone_rows = [line.split("|", 2) if "|" in line else [str(index), line, ""] for index, line in enumerate(_lines(_value(fields, "milestones")), 1)]
    sections = [
        _section("Dự án và phạm vi", f"Freelancer thực hiện dự án {_value(fields, 'project_name')} với phạm vi sau:", bullets=_lines(_value(fields, "scope"))),
        _section("Sản phẩm bàn giao", "Sản phẩm phải hoàn chỉnh, có thể sử dụng và kèm tài liệu cần thiết:", bullets=_lines(_value(fields, "deliverables"))),
        _section("Milestone và thanh toán", f"Lịch thanh toán tổng quát: {_value(fields, 'payment_schedule')}. Thanh toán chỉ đến hạn sau khi milestone tương ứng được nghiệm thu.", table={"headers": ["Mốc", "Nội dung", "Tỷ lệ/ghi chú"], "rows": milestone_rows}),
        _section("Nghiệm thu", f"Tiêu chí nghiệm thu: {_value(fields, 'acceptance_criteria')}. Khách hàng phải phản hồi bằng văn bản trong thời hạn hợp lý; lỗi phải được mô tả đủ để Freelancer tái hiện và khắc phục."),
        _section("Sở hữu trí tuệ", f"Cơ chế sở hữu: {_value(fields, 'ip_ownership')}. Tài sản có trước, thư viện dùng chung và thành phần bên thứ ba không tự động chuyển giao; giấy phép sử dụng phải được liệt kê."),
        _section("Bảo mật", f"Freelancer chỉ sử dụng thông tin và quyền truy cập cho dự án; nghĩa vụ bảo mật kéo dài {_value(fields, 'confidentiality_duration')} sau khi chấm dứt."),
        _section("Tư cách độc lập", "Freelancer là nhà cung cấp độc lập, tự chịu nghĩa vụ thuế và không có thẩm quyền đại diện hoặc tạo nghĩa vụ thay cho Khách hàng."),
        _section("Thời hạn và chấm dứt", f"Hạn hoàn thành: {_value(fields, 'deadline')}. Mỗi Bên có thể chấm dứt với thông báo trước {_value(fields, 'termination_notice')}; các phần việc đã nghiệm thu phải được thanh toán và bàn giao."),
    ]
    return _draft(title="HỢP ĐỒNG DỊCH VỤ FREELANCER", subtitle=_value(fields, "project_name"), party_a=client, party_b=freelancer, metadata=[("Hạn hoàn thành", _value(fields, "deadline")), ("Thanh toán", _value(fields, "payment_schedule")), ("Sở hữu IP", _value(fields, "ip_ownership"))], sections=sections, warnings=warnings)


def _internship(fields: dict[str, str], warnings: list[dict[str, str]]) -> dict[str, Any]:
    company, intern = _value(fields, "company"), _value(fields, "intern")
    sections = [
        _section("Mục tiêu chương trình", f"{intern}, sinh viên {_value(fields, 'university')}, tham gia chương trình tại {_value(fields, 'department')} dưới sự hướng dẫn của {_value(fields, 'supervisor')}. Chương trình nhằm đào tạo thực tế và không mặc nhiên tạo quan hệ lao động ngoài phạm vi pháp luật."),
        _section("Thời gian và lịch thực tập", f"Từ {_value(fields, 'start_date')} đến {_value(fields, 'end_date')}; lịch làm việc: {_value(fields, 'working_schedule')}."),
        _section("Nhiệm vụ", "Thực tập sinh thực hiện các nhiệm vụ sau dưới sự giám sát:", bullets=_lines(_value(fields, "responsibilities"))),
        _section("Trợ cấp", f"Trợ cấp: {_value(fields, 'allowance')}. Khoản này được xử lý theo chính sách và nghĩa vụ thuế áp dụng."),
        _section("Bảo mật và an toàn thông tin", f"Thực tập sinh bảo vệ mã nguồn, dataset, API key, tài liệu khách hàng và dữ liệu nội bộ. Nghĩa vụ bảo mật kéo dài {_value(fields, 'confidentiality_duration')}."),
        _section("Sở hữu sản phẩm", f"Quyền đối với sản phẩm tạo ra trong chương trình: {_value(fields, 'ip_ownership')}. Tài sản có trước của Thực tập sinh phải được khai báo."),
        _section("Chính sách và chấm dứt", "Thực tập sinh tuân thủ nội quy, quy định truy cập và hướng dẫn của người phụ trách. Mỗi Bên có thể kết thúc chương trình vì vi phạm, lý do an toàn hoặc nhu cầu hợp lý sau khi thông báo."),
        _section("Xác nhận hoàn thành", "Doanh nghiệp có thể cấp xác nhận sau khi Thực tập sinh hoàn thành nhiệm vụ, bàn giao tài sản, tài khoản và báo cáo theo yêu cầu."),
    ]
    return _draft(title="THỎA THUẬN THỰC TẬP", subtitle=f"{_value(fields, 'department')} - {_value(fields, 'university')}", party_a=company, party_b=intern, metadata=[("Thời gian", f"{_value(fields, 'start_date')} - {_value(fields, 'end_date')}"), ("Người hướng dẫn", _value(fields, "supervisor")), ("Trợ cấp", _value(fields, "allowance"))], sections=sections, warnings=warnings)


def _service(fields: dict[str, str], warnings: list[dict[str, str]]) -> dict[str, Any]:
    provider, customer = _value(fields, "provider"), _value(fields, "customer")
    sections = [
        _section("Phạm vi dịch vụ", _value(fields, "service_description")),
        _section("Sản phẩm bàn giao", "Nhà cung cấp bàn giao các hạng mục sau:", bullets=_lines(_value(fields, "deliverables"))),
        _section("Phí và thanh toán", f"Phí dịch vụ: {_value(fields, 'fee')}. Lịch thanh toán: {_value(fields, 'payment_schedule')}. Khoản thanh toán quá hạn có thể bị tạm ngừng dịch vụ sau khi thông báo hợp lý."),
        _section("Nghĩa vụ khách hàng", _value(fields, "customer_obligations")),
        _section("Mức dịch vụ", _value(fields, "sla")),
        _section("Bảo mật và dữ liệu", "Mỗi Bên bảo vệ thông tin mật và chỉ xử lý dữ liệu cho mục đích thực hiện hợp đồng. Việc chuyển giao dữ liệu cho bên thứ ba phải có căn cứ, kiểm soát truy cập và biện pháp bảo mật phù hợp."),
        _section("Sở hữu trí tuệ", "Tài sản có trước thuộc chủ sở hữu ban đầu. Quyền với deliverable chỉ chuyển giao hoặc cấp phép theo phạm vi được thỏa thuận và sau khi hoàn thành nghĩa vụ thanh toán."),
        _section("Trách nhiệm", f"Giới hạn trách nhiệm: {_value(fields, 'liability_cap')}. Ngoại lệ chỉ áp dụng cho gian lận, cố ý vi phạm và nghĩa vụ không thể giới hạn theo luật."),
        _section("Thời hạn và chấm dứt", f"Hợp đồng có hiệu lực từ {_value(fields, 'start_date')} đến {_value(fields, 'end_date')}. Chấm dứt vì thuận tiện cần thông báo trước {_value(fields, 'termination_notice')}; vi phạm trọng yếu phải có thời gian khắc phục hợp lý."),
        _section("Luật áp dụng", f"Hợp đồng chịu sự điều chỉnh của pháp luật {_value(fields, 'governing_law')}; các Bên ưu tiên thương lượng trước khi khởi kiện hoặc trọng tài."),
    ]
    return _draft(title="HỢP ĐỒNG DỊCH VỤ", subtitle=f"{provider} cung cấp dịch vụ cho {customer}", party_a=provider, party_b=customer, metadata=[("Thời hạn", f"{_value(fields, 'start_date')} - {_value(fields, 'end_date')}"), ("Phí", _value(fields, "fee")), ("Thông báo chấm dứt", _value(fields, "termination_notice"))], sections=sections, warnings=warnings)


def _software(fields: dict[str, str], warnings: list[dict[str, str]]) -> dict[str, Any]:
    developer, client = _value(fields, "developer"), _value(fields, "client")
    milestone_rows = [line.split("|", 2) if "|" in line else [str(index), line, ""] for index, line in enumerate(_lines(_value(fields, "milestones")), 1)]
    sections = [
        _section("Phạm vi tổng thể", f"Đơn vị phát triển xây dựng dự án {_value(fields, 'project_name')} theo SOW và đặc tả đính kèm. Tech stack dự kiến: {_value(fields, 'tech_stack')}. Nội dung ngoài SOW không thuộc giá và timeline ban đầu."),
        _section("Phụ lục A - Statement of Work", "Các chức năng và yêu cầu thuộc phạm vi:", bullets=_lines(_value(fields, "requirements"))),
        _section("Phụ lục B - Kế hoạch milestone", f"Giá trị dự án: {_value(fields, 'price')}. Thanh toán gắn với milestone và kết quả nghiệm thu.", table={"headers": ["Mốc", "Nội dung", "Tỷ lệ/ghi chú"], "rows": milestone_rows}),
        _section("Repository và triển khai", f"Repository: {_value(fields, 'repository')}. Triển khai/hosting: {_value(fields, 'deployment')}. Quyền quản trị, tài khoản cloud và bí mật truy cập phải được bàn giao qua kênh được phê duyệt."),
        _section("Nghiệm thu", _value(fields, "acceptance_criteria")),
        _section("Quản lý thay đổi", _value(fields, "change_request_process")),
        _section("Sở hữu trí tuệ và mã nguồn", f"Cơ chế sở hữu: {_value(fields, 'ip_ownership')}. Background IP, công cụ dùng lại, OSS và API bên thứ ba phải được tách khỏi deliverable; nghĩa vụ giấy phép phải được công bố."),
        _section("Bảo hành và bảo trì", f"Bảo hành lỗi: {_value(fields, 'warranty')}. Bảo trì sau bảo hành: {_value(fields, 'maintenance_mode')}. Feature mới không được coi là lỗi bảo hành và phải qua Change Request."),
        _section("Bảo mật, dữ liệu và an toàn", _value(fields, "security_data")),
        _section("Tiến độ và chậm bàn giao", f"Thời gian dự án từ {_value(fields, 'start_date')} đến {_value(fields, 'delivery_date')}. Bên bị chậm phải thông báo nguyên nhân, tác động và kế hoạch khắc phục; thay đổi do Khách hàng hoặc bên thứ ba phải được ghi nhận vào timeline."),
        _section("Chấm dứt và bàn giao", "Vi phạm trọng yếu phải có thời gian khắc phục. Khi chấm dứt, Đơn vị phát triển bàn giao phần đã thanh toán, mã nguồn thuộc phạm vi, tài liệu và thông tin cần thiết để tiếp tục vận hành."),
    ]
    return _draft(title="HỢP ĐỒNG PHÁT TRIỂN PHẦN MỀM", subtitle=_value(fields, "project_name"), party_a=developer, party_b=client, metadata=[("Thời gian", f"{_value(fields, 'start_date')} - {_value(fields, 'delivery_date')}"), ("Giá trị", _value(fields, "price")), ("Sở hữu mã nguồn", _value(fields, "ip_ownership"))], sections=sections, warnings=warnings)


def _maintenance(fields: dict[str, str], warnings: list[dict[str, str]]) -> dict[str, Any]:
    provider, customer = _value(fields, "provider"), _value(fields, "customer")
    sla_rows = [
        ["Critical", "Hệ thống ngừng hoạt động", _value(fields, "sla_critical")],
        ["High", "Chức năng chính bị lỗi", _value(fields, "sla_high")],
        ["Medium", "Lỗi tính năng phụ", _value(fields, "sla_medium")],
        ["Low", "Lỗi nhỏ hoặc giao diện", _value(fields, "sla_low")],
    ]
    sections = [
        _section("Hệ thống và thời hạn", f"Dịch vụ áp dụng cho {_value(fields, 'system_name')} từ {_value(fields, 'start_date')} trong {_value(fields, 'duration')}. Giờ hỗ trợ: {_value(fields, 'support_hours')}."),
        _section("Dịch vụ bao gồm", "Phạm vi phí bảo trì:", bullets=_lines(_value(fields, "included_services"))),
        _section("Dịch vụ loại trừ", "Các hạng mục cần báo giá hoặc Change Request riêng:", bullets=_lines(_value(fields, "excluded_services"))),
        _section("Phân biệt lỗi và tính năng mới", _value(fields, "bug_vs_feature")),
        _section("Phụ lục SLA", "Thời gian phản hồi được tính trong giờ hỗ trợ, trừ khi ghi rõ 24/7. Thời gian xử lý phụ thuộc khả năng tái hiện, quyền truy cập và sự phối hợp của Khách hàng.", table={"headers": ["Mức độ", "Ví dụ", "Cam kết phản hồi"], "rows": sla_rows}),
        _section("Phí và thanh toán", f"Phí bảo trì: {_value(fields, 'monthly_fee')}. Công việc ngoài phạm vi chỉ thực hiện sau khi Khách hàng phê duyệt báo giá hoặc Change Request."),
        _section("Backup và bảo mật", _value(fields, "backup_security")),
        _section("Trách nhiệm khách hàng", "Khách hàng cung cấp quyền truy cập cần thiết, thông tin lỗi, log và đầu mối phối hợp; duy trì bản quyền phần mềm bên thứ ba và không tự ý thay đổi hệ thống làm ảnh hưởng việc hỗ trợ."),
        _section("Chấm dứt", f"Mỗi Bên có thể chấm dứt với thông báo trước {_value(fields, 'termination_notice')}. Khi chấm dứt, Nhà cung cấp bàn giao ticket mở, tài liệu cập nhật và thu hồi quyền truy cập theo quy trình an toàn."),
    ]
    return _draft(title="HỢP ĐỒNG BẢO TRÌ PHẦN MỀM", subtitle=_value(fields, "system_name"), party_a=provider, party_b=customer, metadata=[("Ngày bắt đầu", _value(fields, "start_date")), ("Thời hạn", _value(fields, "duration")), ("Phí", _value(fields, "monthly_fee"))], sections=sections, warnings=warnings)


BUILDERS: dict[str, Callable[[dict[str, str], list[dict[str, str]]], dict[str, Any]]] = {
    "NDA": _nda,
    "EMPLOYMENT_CONTRACT": _employment,
    "FREELANCER_CONTRACT": _freelancer,
    "INTERNSHIP_CONTRACT": _internship,
    "SERVICE_AGREEMENT": _service,
    "SOFTWARE_DEVELOPMENT_CONTRACT": _software,
    "MAINTENANCE_CONTRACT": _maintenance,
}


def build_document_draft(document_type: str, fields: dict[str, str], warnings: list[dict[str, str]]) -> dict[str, Any]:
    return BUILDERS[document_type.upper()](fields, warnings)
