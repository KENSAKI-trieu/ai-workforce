"""Fixed replies of the Legal agent."""

from __future__ import annotations



LEGAL_PERSPECTIVE_QUESTION = (
    "Trước khi rà soát, tôi cần biết bạn đại diện cho bên nào — cùng một điều khoản "
    "có thể đảo chiều mức rủi ro tùy góc nhìn.\n\n"
    "- **Bên A** (công ty / nhà cung cấp / bên bán)\n"
    "- **Bên B** (khách hàng / bên mua)\n"
    "- **Trung lập** (đánh giá khách quan, không thiên vị)\n\n"
    "Bạn trả lời \"Bên A\", \"Bên B\" hoặc \"trung lập\". Gõ \"hủy\" nếu không muốn rà soát."
)


LEGAL_REVIEW_TOOL_OFF_REPLY = (
    "Tôi là Legal Counsel AI và đã nhận nội dung bạn gửi. "
    "Công cụ rà soát rủi ro hợp đồng `audit_contract_risk` hiện chưa "
    "được bật cho AI Employee này, nên tôi không tự ý thực thi công cụ. "
    "Admin hoặc Owner có thể bật công cụ trong phần Cấu hình nếu cần "
    "phân tích điều khoản và tạo thẻ rủi ro."
)


LEGAL_SEARCH_TOOL_OFF_REPLY = (
    "Công cụ tra cứu kho tri thức `rag_search` hiện chưa được bật cho AI Employee "
    "này, nên tôi không tra cứu tài liệu để trả lời câu hỏi. Admin hoặc Owner có thể bật "
    "công cụ trong phần Cấu hình."
)


LEGAL_NO_TOOLS_REPLY = (
    "Tôi là Legal Counsel AI nhưng hiện chưa được bật công cụ nào: cả rà soát hợp đồng "
    "(`audit_contract_risk`) lẫn tra cứu kho tri thức (`rag_search`) đều đang tắt. "
    "Admin hoặc Owner có thể bật trong phần Cấu hình."
)


LEGAL_APPROVAL_REMINDER = (
    "Nếu quyết định này tạo nghĩa vụ pháp lý hoặc chia sẻ dữ liệu nhạy cảm, hãy gửi Legal phê duyệt."
)


LEGAL_NOT_FOUND_REPLY = (
    "Tôi chưa tìm thấy văn bản còn hiệu lực và phù hợp trong phạm vi ACL của bạn. "
    "Tôi sẽ không tự suy diễn quy định; vui lòng bổ sung tài liệu hoặc gửi Legal Team xác nhận."
)


LEGAL_NOT_REVIEWED_NOTICE = (
    "\n\n**Lưu ý: tôi chưa rà soát rủi ro nội dung này.** Nếu bạn muốn tôi rà soát hợp "
    "đồng, hãy nhắn \"rà soát hợp đồng\" kèm nội dung."
)
