# Giai đoạn 5: LangGraph orchestration

## Phạm vi

AI service chạy một `StateGraph` thật với state dùng chung `WorkforceAgentState` và sáu subgraph nghiệp vụ:

- `LEGAL`
- `HR`
- `FINANCE`
- `CUSTOMER_SUPPORT`
- `KNOWLEDGE`
- `CEO`

Luồng chính:

```text
input_guard -> intent_router -> agent_selector -> domain_subgraph
            -> retrieve_context -> model_decision
               -> execute_read_tool -> model_decision
               -> approval_interrupt
               -> output_validation
            -> citation_verification -> response
```

Mỗi subgraph bổ sung policy prompt và thu hẹp danh sách tool theo nghiệp vụ. Danh sách này tiếp tục bị giới hạn bởi ACL do backend cấp; subgraph không thể mở rộng quyền.

## Ranh giới tin cậy

Backend tạo payload orchestration từ user và agent đã tải từ database. AI service nhận hai credential tách biệt:

- `X-AI-Service-Key`: xác thực service-to-service.
- `X-Internal-Tool-Authorization`: JWT nội bộ đại diện user để tool gateway xác thực lại tenant và ACL.

JWT không được ghi vào graph state hoặc checkpoint. Trước mọi tool call, graph ghi đè `tenant_id` và `audit` bằng runtime context đáng tin cậy. Khi resume, graph đối chiếu tenant, user và conversation với checkpoint, sau đó kiểm tra lại ACL hiện tại ngay trước khi thực thi action.

Input, retrieved context, tool result và final output đều được redaction trước khi lưu hoặc chuyển tiếp trong graph. Một thread đang suspended không thể bị ghi đè bằng lượt chạy mới; nó phải được resume hoặc xử lý dứt điểm trước.

## Human approval

Tool `READ_ONLY` chạy trực tiếp và quay lại node quyết định. Tool `WRITE` hoặc `EXTERNAL_ACTION` gọi `interrupt()` trước khi có side effect. Resume dùng cùng `thread_id` và một giá trị như:

```json
{
  "approved": true,
  "approval_id": "approval-uuid"
}
```

Action chỉ chạy sau resume được duyệt. Idempotency key được tạo từ correlation ID và tool call ID để việc chạy lại node không nhân đôi side effect.

Thiết kế này tuân theo contract chính thức của [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts): interrupt cần checkpointer và `thread_id`, resume bằng `Command(resume=...)`, và node bắt đầu lại từ đầu khi resume. Các subgraph dùng chung state schema được gắn trực tiếp làm node theo hướng dẫn [LangGraph subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs).

## API nội bộ

```text
POST /v1/orchestration/run
POST /v1/orchestration/resume
```

`run` trả `COMPLETED` hoặc `AWAITING_APPROVAL`. `resume` chỉ nhận checkpoint đúng tenant, user và conversation. Backend gọi hai endpoint qua `AIServiceClient` và không chuyển quyền do model tạo ra.

## Rollout

Backend mặc định giữ đường chạy cũ:

```dotenv
LANGGRAPH_ENABLED=false
LANGGRAPH_LEGACY_FALLBACK=true
```

Bật theo môi trường sau khi AI service và tool gateway sẵn sàng. Khi fallback bật, lỗi kết nối hoặc lỗi AI service 5xx quay về executor cũ. Lỗi 4xx về identity, tenant, input hoặc ACL không fallback và vẫn fail closed.

`LangGraphEngine` cho phép inject checkpointer. Giai đoạn 6 đã cấu hình `PostgresSaver` cho Docker/production; `InMemorySaver` chỉ còn dành cho local/test.

## Kiểm thử

`apps/ai-service/tests/test_langgraph_phase5.py` bao phủ:

- route và policy của Knowledge subgraph;
- vòng lặp read-only tool;
- interrupt, approve và reject action;
- tenant/user binding;
- thu hồi ACL giữa interrupt và resume;
- contract HTTP và tool JWT bắt buộc.
