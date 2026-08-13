# Giai đoạn 7: Streaming và giao diện

## Giao thức SSE

Luồng chat dùng `POST /api/v1/agent/chat/stream` với `Content-Type: application/json` và trả về `text/event-stream`. `POST` được chọn thay cho `EventSource` để giữ payload chat và Bearer token trong header.

Các sự kiện công khai:

| Event | Payload | Ý nghĩa |
| --- | --- | --- |
| `status` | `{"phase":"ANALYZING"}` | Trạng thái nghiệp vụ đã allow-list |
| `token` | `{"delta":"..."}` | Một phần của câu trả lời đã qua output guard |
| `complete` | Chat response | Tin nhắn đã được persist thành công |
| `error` | Thông báo an toàn | Lỗi tổng quát, không chứa exception/prompt nội bộ |

Các phase hợp lệ là `ANALYZING`, `SEARCHING`, `TOOL_CALLING`, `WAITING_APPROVAL` và `COMPLETED`.

AI service đọc lifecycle thật của LangGraph nhưng chỉ ánh xạ node sang phase công khai. Tên node, debug payload, system prompt, quyết định model và chain-of-thought không đi qua stream. Token được phát từ `final_answer` sau output validation/citation verification; không phát token thô của structured decision model.

## Luồng dữ liệu

```text
LangGraph debug lifecycle (trusted AI service)
  -> public phase/token SSE
  -> backend authenticated proxy
  -> persist workflow, audit trace and assistant message
  -> browser fetch() stream parser
  -> progress UI and incremental answer
```

Backend thêm các header `Cache-Control: no-cache, no-transform` và `X-Accel-Buffering: no`. Reverse proxy production cũng phải tắt response buffering cho endpoint SSE và đặt idle timeout lớn hơn timeout AI service.

## Audit trace đã làm sạch

Mỗi LangGraph run lưu trace allow-list vào:

- `agent_workflows.dag_plan.execution_trace` để gắn với workflow;
- `audit_logs.output_result.trace` với action `orchestration.execution_trace`.

Mỗi entry chỉ có `sequence`, `phase`, `status`. Trace không lưu prompt, message, tool arguments, tool results, model rationale hoặc tên node nội bộ. Tool calls trả về UI cũng chỉ giữ `tool_name`, `action` và `status`.

## UI

Trang `/agents/[role]` hiển thị năm trạng thái bằng icon đã hoàn thành/đang chạy/chưa chạy và render câu trả lời tăng dần. Khi nhận `complete`, UI tải lại conversation từ dữ liệu đã persist; nếu stream lỗi, optimistic message được hoàn tác và người dùng có thể gửi lại.

## Kiểm thử

- AI service kiểm tra phase/token stream không chứa `node` hoặc `prompt`.
- Backend kiểm tra parser SSE và phép biến đổi trace sang schema công khai.
- Frontend được kiểm tra bằng ESLint, TypeScript và production build.
