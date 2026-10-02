"""What the graph says to the user when it answers instead of the model.

Every one of these reaches the chat as the agent's reply, so they are written in the
product's language. They used to be English, which left a Vietnamese user reading "The
answer was withheld because its citations could not be verified." with no idea why.
"""

from __future__ import annotations

ITERATION_LIMIT = (
    "Tôi đã thử quá số bước cho phép mà chưa có được câu trả lời an toàn. "
    "Bạn hãy thử hỏi lại ngắn gọn hơn."
)
DECISION_FAILED = (
    "Mô hình AI hiện không đưa ra được kết quả hợp lệ. Bạn hãy thử lại sau ít phút."
)
TOOL_NOT_AVAILABLE = (
    "Yêu cầu này cần một công cụ mà trợ lý không được phép dùng trong phạm vi hiện tại."
)
ACTION_REJECTED = "Hành động đã bị người phê duyệt từ chối, nên tôi không thực hiện."
AUTHORIZATION_EXPIRED = (
    "Hành động chưa được thực hiện vì quyền thực hiện không còn hiệu lực."
)
TOOL_FAILED = "Công cụ gặp lỗi khi thực hiện, nên không có kết quả nào được ghi nhận."
OUTPUT_INVALID = "Tôi không tạo được câu trả lời hợp lệ cho yêu cầu này."
CITATION_UNVERIFIED = (
    "Tôi không đưa ra câu trả lời này vì không xác minh được nguồn trích dẫn của nó "
    "trong tài liệu của công ty."
)
FIGURES_UNVERIFIED = (
    "Tôi không đưa ra câu trả lời vừa soạn vì trong đó có số liệu không lấy từ sổ sách. "
    "Đây là số liệu gốc hệ thống trả về:"
)
FIGURES_WITHOUT_DATA = (
    "Tôi không đưa ra câu trả lời này vì nó có số liệu nhưng không có dữ liệu sổ sách nào "
    "đứng sau. Hãy hỏi cụ thể tài khoản, kỳ hoặc đối tượng để tôi tra cứu."
)
NO_EVIDENCE = "Không có tài liệu hay kết quả công cụ nào để trả lời yêu cầu này."
NOT_IN_DOCUMENTS = (
    "Tài liệu của công ty mà tôi được phép tra cứu chưa đề cập nội dung này, nên tôi "
    "không tự suy diễn. Bạn có thể bổ sung tài liệu hoặc hỏi bộ phận phụ trách."
)


def tool_disabled(label: str) -> str:
    """The reply when a request needs a tool the organisation turned off.

    Said by the graph, not the model: the model only recognises that the request needs the
    tool. Left to answer on its own it tried the task from general knowledge, and the
    citation check then withheld that answer -- the user learned nothing about why.
    """
    return (
        f"Yêu cầu này cần tính năng “{label}”, nhưng tính năng này đã bị tắt theo cấu hình "
        "của công ty cho trợ lý này, nên tôi không thực hiện được. Nếu cần dùng, bạn hãy "
        "liên hệ quản trị viên (Owner/Admin) để bật lại."
    )


def tool_restricted(label: str) -> str:
    """The reply when a request needs a tool the agent has but this user's role may not use.

    Without it the model never saw the tool at all, and explained the gap itself: asked for
    an NDA, it cited the company's legal rules for a drafting approval they never mention.
    """
    return (
        f"Yêu cầu này cần tính năng “{label}”, nhưng vai trò của bạn không được phép dùng "
        "tính năng này, nên tôi không thực hiện được. Bạn hãy nhờ người có thẩm quyền "
        "thực hiện, hoặc liên hệ quản trị viên (Owner/Admin) nếu cần được cấp quyền."
    )
