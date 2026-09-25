# Kế hoạch đưa MVP thành sản phẩm vận hành được

Ngày chốt: 2026-07-31

## 1. Quyết định ưu tiên

Không mở rộng thêm AI Agent trong giai đoạn hiện tại. Lát cắt chuẩn để kiểm chứng nền tảng là Customer Support:

`Email → Task → phân loại → RAG → draft → human approval → gửi email → audit → operations dashboard`

Luồng dùng delivery mode `outbox` theo mặc định để không vô tình gửi email thật. SMTP chỉ hoạt động khi operator chủ động cấu hình biến môi trường. API trả `202` sau khi ghi dữ liệu và enqueue; worker xử lý bất đồng bộ.

## 2. Những gì đã triển khai trong lát cắt đầu tiên

| Năng lực | Hiện trạng | Bằng chứng/tiêu chí |
|---|---|---|
| Durable state | Hoàn thành nền tảng | Mỗi step có status, attempt, input/output, error và timestamps trong PostgreSQL |
| Queue/background worker | Hoàn thành nền tảng | Redis at-least-once queue, processing list, recovery khi worker restart |
| Chống chạy trùng | Hoàn thành nền tảng | Idempotency-Key ở API, unique constraint ở DB, outbound message key |
| Retry | Hoàn thành cho send step | Tối đa 3 lần, lưu attempt trước side effect |
| Human approval | Hoàn thành | Approve, reject, edit-and-approve; chỉ gửi sau duyệt |
| RAG không có kết quả | Hoàn thành safe fallback | Draft ghi rõ chưa tìm thấy chính sách và buộc người duyệt kiểm tra |
| Email | Outbox + SMTP | Outbox an toàn mặc định; SMTP là adapter thật có timeout/TLS |
| Audit | Hoàn thành theo step | Mỗi step thành công tạo audit record gắn workflow/case |
| Observability | Hoàn thành nền tảng | Queue depth, processing, dead-letter, heartbeat, retry, SLA overdue, success rate, latency |
| UI | Hoàn thành | Trang Customer Support Operations và tiến độ từng step ở sidebar |
| CI/CD | Hoàn thành nền tảng | Migration, test, lint/build, container build; API và worker entrypoint được kiểm tra |

## 3. Case vận hành và cách xử lý

| Case | Hành vi mong đợi |
|---|---|
| Không tìm thấy tài liệu | Không bịa nguồn; draft gắn cờ cần người kiểm tra |
| Confidence thấp | Step output ghi `needs_review`; không bỏ qua approval |
| Manager từ chối | Case `REJECTED`, workflow `FAILED`, task `CANCELLED`, không tạo outbound |
| SMTP lỗi tạm thời | Step `RETRY_PENDING`, retry tối đa 3 lần |
| SMTP lỗi liên tục | Case/workflow/task `FAILED`, lỗi hiển thị trên UI |
| Worker chết giữa job | Job trong processing list được trả về queue khi worker khởi động lại |
| Job sai định dạng | Chuyển dead-letter, không tạo vòng lặp retry vô hạn |
| Redis mất kết nối lúc tạo case | Case vẫn được lưu `QUEUE_FAILED`, API trả 503 kèm case id |
| Redis mất sau khi approval | Quyết định duyệt vẫn được lưu; case chuyển `QUEUE_FAILED` để retry |
| API bị gửi trùng | Trả lại cùng case theo tenant + Idempotency-Key |
| Step chạy lại | Step đã completed được bỏ qua; outbound unique key ngăn gửi hai lần |
| Task quá hạn | Operations API/UI đếm theo due date và trạng thái chưa kết thúc |

Lưu ý kỹ thuật: timeout hiện là giới hạn phát hiện sau khi hàm kết thúc. Với provider/tool có thể treo lâu, adapter bắt buộc phải đặt network timeout; hard cancellation ở cấp process sẽ nằm trong Workflow Engine v2.

## 4. Roadmap 12 giai đoạn và Definition of Done

### Giai đoạn 1 — Một quy trình thực tế

Trạng thái: **đã có vertical slice vận hành**.

Definition of Done còn lại trước pilot: Gmail/Outlook sandbox thay SMTP chung; bộ policy Customer Support thật; test rejection, provider outage, crash recovery và SLA; dashboard chi phí/model của riêng flow.

### Giai đoạn 2 — Workflow Engine v2

Trạng thái: **đã có durable sequential core**.

Tiếp theo:

1. Chuẩn hóa schema node: trigger, condition, parallel, delay, loop, approval, fallback và output.
2. Tạo lease cho step đang chạy, heartbeat và cơ chế reclaim theo lease.
3. Thêm `next_run_at` cho delay/backoff, retry policy có jitter và error class.
4. Hỗ trợ branch/join và giới hạn loop (`max_iterations`).
5. Version workflow definition; run cũ luôn dùng snapshot version cũ.

Definition of Done: tắt worker ở mọi step rồi bật lại không mất trạng thái, không chạy lại side effect đã commit, và có test contract cho từng node.

### Giai đoạn 3 — Queue và background worker

Trạng thái: **đã có Redis worker nền tảng**.

Tiếp theo: nhiều worker đồng thời, visibility timeout/lease, scheduled queue, exponential backoff, dead-letter replay có quyền, SSE progress thay polling và autoscaling theo queue age.

Definition of Done: chịu tải benchmark đã chốt, restart/scale không làm mất job, dashboard cảnh báo oldest-job age và dead-letter.

### Giai đoạn 4 — AI evaluation

Trạng thái: **chưa hoàn thành**.

Tạo dataset có version với input, expected intent, required citations, allowed/forbidden action. Pipeline eval tách retrieval, answer, citation, tool-call và workflow completion. Mỗi prompt/model/chunking change chạy regression trong CI; thay đổi bị chặn nếu vượt ngưỡng suy giảm.

Definition of Done: baseline có retrieval hit rate, answer/citation correctness, tool accuracy, approval rate, latency và cost/task.

### Giai đoạn 5 — RAG nâng cao

Trạng thái: **đã có hybrid search và tenant filter; chưa đủ production**.

Thực hiện theo thứ tự: ACL ở query → document version/tombstone → sync incremental → query rewrite/intent → metadata filter → reranker → context compression → injection scanner → citation feedback.

Definition of Done: test công ty/phòng ban chéo bằng negative cases; xóa tài liệu làm embedding không còn truy xuất; mọi câu trả lời có provenance.

### Giai đoạn 6 — Model router và chi phí

Trạng thái: **đã có cost log/budget dashboard; flow mới chưa gọi model provider**.

Customer Support hiện dùng classifier/draft deterministic để vertical slice có thể kiểm thử không cần secret. Tiếp theo thêm router theo task class, pricing catalog có effective date, token budget, prompt length guard, cache, retry/fallback và hard limit theo tenant/agent/user.

Definition of Done: mọi model call có model, prompt version, tokens, latency, cost và budget decision; vượt 100% bị chặn trước khi gọi provider.

### Giai đoạn 7 — Enterprise security

Trạng thái: **RBAC/multi-tenant/audit đã có nền tảng**.

Ưu tiên: policy test cho mọi endpoint/query, department/document ACL, API key envelope encryption, rate limit, refresh-token revocation, malware scan upload, immutable audit export, retention jobs, backup/restore drill.

Definition of Done: automated tenant-isolation suite không có IDOR; secret không vào log/DB plaintext; restore được đo RPO/RTO.

### Giai đoạn 8 — Observability

Trạng thái: **đã thêm operations metrics/worker health cho vertical slice**.

Tiếp theo: OpenTelemetry trace xuyên API→queue→worker→LLM/tool, structured redacted logs, Prometheus metrics, alert SLO, provider/tool error budget và stuck-step detector.

Definition of Done: từ workflow id truy được toàn bộ timeline nhưng không lộ secret/PII không cần thiết.

### Giai đoạn 9 — Tích hợp thật

Trạng thái: **có registry/permission/test/disconnect/audit; chưa có OAuth provider hoàn chỉnh**.

Thứ tự: Gmail/Outlook → Drive/SharePoint → Slack/Teams → Calendar → CRM → Jira/Trello → webhook. Mỗi connector phải có OAuth state/PKCE, scope tối thiểu, token refresh, reconnect, rate-limit/backoff và audit.

Definition of Done: connector sandbox chạy end-to-end và mất/revoke token tạo cảnh báo có thể xử lý.

### Giai đoạn 10 — Agent Builder

Trạng thái: **có CRUD agent; chưa có publish gate**.

Thêm draft/version/publish, permission policy, KB/tools/approval/budget và eval suite bắt buộc. Published version immutable; rollback một thao tác.

Definition of Done: admin tạo agent không sửa code và chỉ publish khi security/eval gates xanh.

### Giai đoạn 11 — Multi-agent

Trạng thái: **hoãn có chủ đích**.

Chỉ bắt đầu sau khi giai đoạn 1–10 đạt pilot SLO. Mọi delegation có step/token/time budget, tool policy, max depth/iterations, stop condition và approval cho side effect.

Definition of Done: không có conversation vô hạn; toàn bộ delegation truy vết và tính chi phí được.

### Giai đoạn 12 — Production

Trạng thái: **Docker + CI/CD + migrations đã có nền tảng**.

Tiếp theo: dev/staging/prod tách biệt, HTTPS/API gateway, managed secrets, centralized logs/errors, backups, migration rollback runbook, canary/rollback, dependency/container scan, SLO/on-call và disaster-recovery drill.

Definition of Done: deploy staging tự động, production có approval, rollback đã diễn tập và restore đạt RPO/RTO.

## 5. Thứ tự thực thi được khóa

1. Pilot Customer Support bằng outbox, thu eval dataset và baseline.
2. Hoàn thiện lease/scheduler/branching của Workflow Engine.
3. Gmail/Outlook sandbox và Drive/SharePoint ACL.
4. Model router + cost guard gắn với eval regression.
5. Security/observability hardening và staging load test.
6. Agent Builder publish gate.
7. Multi-agent chỉ mở sau khi single-agent SLO ổn định.

Không đánh dấu một giai đoạn “xong” chỉ vì có UI hoặc schema. Mỗi giai đoạn phải có negative tests, telemetry, runbook và tiêu chí rollback.
