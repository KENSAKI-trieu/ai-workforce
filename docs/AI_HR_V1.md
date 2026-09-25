# AI HR V1 — phạm vi đã triển khai

AI HR V1 là một lớp vận hành nhân sự có dữ liệu và workflow bền vững, được tích hợp trực tiếp vào hội thoại tại `/agents/HR`. Không có trang menu HR riêng: hồ sơ, quỹ phép, đơn nghỉ, hợp đồng, onboarding và phê duyệt đều xuất hiện dưới dạng action/card ngay trong chatbot.

## Năng lực sẵn sàng

1. Hỏi đáp chính sách HR bằng RAG, có citation và thông tin hiệu lực khi tài liệu cung cấp metadata.
2. Tra cứu hồ sơ nhân viên theo phạm vi được phép và quỹ phép có cấu trúc.
3. Tạo đơn nghỉ phép, giữ quỹ trong lúc chờ, duyệt/từ chối, cập nhật ledger và lịch nội bộ.
4. Tạo workflow onboarding mặc định gồm tám task liên phòng ban.
5. Theo dõi hợp đồng, ngày hết hạn và ngày kết thúc thử việc; tạo reminder/task có chống trùng.
6. Tạo task, notification và audit log cho mọi thay đổi quan trọng.

## Mô hình phân quyền

| Người dùng | Phạm vi hồ sơ HR |
|---|---|
| Employee | Chỉ hồ sơ và dữ liệu của mình; có thể hỏi chính sách, xem phép, hợp đồng cá nhân và gửi đơn nghỉ |
| Manager | Bản thân và toàn bộ cây báo cáo trực tiếp/gián tiếp bên dưới theo `manager_id` |
| Admin | Bản thân và toàn bộ cây báo cáo bên dưới; không được quét toàn công ty |
| HR Manager | Đọc theo cây báo cáo và nhóm dữ liệu nghiệp vụ được cấp |
| HR Admin | Bản thân và cây báo cáo bên dưới; có nhiều section nghiệp vụ hơn nhưng không tự động có scope toàn công ty |
| CEO | Toàn workspace, bao gồm tìm kiếm bất kỳ nhân viên nào qua AI HR |
| Owner | Toàn workspace để quản trị tenant |
| Guest | Chỉ các chính sách được phép công khai; không có quyền đọc hồ sơ nhân viên |

Hệ thống luôn lọc theo `tenant_id`. Phạm vi `REPORTING_TREE` được tính đệ quy từ `users.manager_id`; việc gửi một ID, email, tên hoặc tự nhận vai trò khác qua chatbot không thể mở rộng phạm vi. System Admin ở phòng ban kỹ thuật không kế thừa quyền đọc dữ liệu HR nhạy cảm. Đơn nghỉ có người duyệt cụ thể; CEO/Owner có thể xử lý toàn công ty, còn Admin/Manager chỉ xử lý yêu cầu thuộc cây quản lý hoặc được gán trực tiếp.

## Policy Engine và field-level access

AI HR không có tài khoản database và không nhận hồ sơ đầy đủ trước khi lọc. Mọi tool hồ sơ đi qua `hr_access_policy.py` theo thứ tự:

```text
Actor từ JWT/session
→ tenant check
→ RBAC
→ SELF/REPORTING_TREE/COMPANY scope
→ section permission
→ business purpose
→ truy vấn đúng cột được phép
→ masking
→ dữ liệu đã lọc mới được đưa vào context
→ audit ALLOWED hoặc DENIED
```

Các section V1: `BASIC`, `PRIVATE`, `CONTRACT`, `COMPENSATION`, `LEAVE`, `PERFORMANCE`, `DISCIPLINE`, `HR_NOTES`, `DOCUMENTS`. Dữ liệu chưa có bảng nghiệp vụ trong V1 được trả trạng thái `NOT_AVAILABLE_IN_MVP`, không được AI tự suy diễn.

Purpose hợp lệ gồm `SELF_SERVICE`, `DIRECTORY_LOOKUP`, `LEAVE_MANAGEMENT`, `CONTRACT_RENEWAL`, `CONTRACT_STATUS_MONITORING`, `ONBOARDING`, `PERFORMANCE_REVIEW`, `PAYROLL_PROCESSING`, `HR_OPERATIONS`, `LEGAL_REVIEW`, `EMPLOYEE_SUPPORT` và `EXECUTIVE_REVIEW`.

Ví dụ yêu cầu gia hạn hợp đồng có thể nhận `BASIC + CONTRACT`, nhưng `COMPENSATION` bị loại trước truy vấn vì không liên quan. Điện thoại và liên hệ khẩn cấp được mask cho người xem không phải chính nhân viên; địa chỉ đầy đủ không được đưa vào context. Contract summary không trả `document_url` hoặc số hợp đồng.

## Dữ liệu nghiệp vụ

- `users.manager_id`: quan hệ quản lý trực tiếp.
- `user_profiles`: loại và trạng thái làm việc, kỹ năng, chứng chỉ, kinh nghiệm và lịch sử công việc.
- `leave_balances`: số ngày cấp, chuyển tiếp, đã dùng và đang giữ theo nhân viên/năm.
- `leave_requests`: đơn nghỉ có ngày, loại, buổi nghỉ, trạng thái và quyết định.
- `leave_ledger`: sổ bất biến cho thao tác giữ, sử dụng hoặc trả lại ngày phép.
- `hr_calendar_events`: sự kiện HR nội bộ được Calendar hiển thị.
- `employment_contracts`: hợp đồng và mốc thử việc.
- `onboarding_cases`, `onboarding_steps`: tiến trình onboarding liên kết với task thật.

`UserMemory.leave_balance` chỉ còn được đồng bộ để tương thích ngược. Nguồn dữ liệu chính là `leave_balances` và `leave_ledger`.

## Luồng nghỉ phép

```text
Nhân viên hỏi chính sách
→ RAG trả lời kèm nguồn
→ nhân viên tạo đơn
→ kiểm tra ngày làm việc, đơn trùng và quỹ khả dụng
→ giữ số ngày phép
→ tạo WorkflowApproval và thông báo quản lý
→ quản lý phê duyệt hoặc từ chối
→ ghi usage/release vào ledger trong cùng transaction
→ tạo sự kiện lịch khi được duyệt
→ thông báo nhân viên
→ ghi audit log
```

Các bất biến chính:

- Không tạo đơn có ngày trong quá khứ, khoảng ngày ngược hoặc chỉ gồm cuối tuần.
- Nghỉ nửa ngày chỉ áp dụng cho một ngày.
- Không cho phép hai đơn đang chờ/đã duyệt chồng ngày.
- Số ngày đang chờ được giữ để tránh gửi nhiều đơn vượt quỹ.
- Approval chỉ được xử lý một lần; duyệt lặp trả `409` và không trừ phép lần hai.
- Không cho `EDIT_AND_APPROVE` trên đơn nghỉ có cấu trúc vì có thể làm sai ledger.

## API

Các endpoint đều nằm dưới `/api/v1/hr`:

- `GET /overview`
- `GET /employees`
- `GET /employees/{employee_id}`
- `PATCH /employees/{employee_id}/employment`
- `GET /leave-balance`
- `POST /leave-requests`
- `GET /leave-requests`
- `GET /calendar-events`
- `POST /contracts`
- `GET /contracts`
- `POST /onboarding`
- `GET /onboarding`
- `POST /reminders/scan`

## AI HR tools

- `rag_search`
- `get_employee_basic_profile`
- `get_employee_private_profile`
- `get_employee_contract_summary`
- `get_employee_compensation_summary`
- `get_employee_leave_summary`
- `get_employee_full_profile` — bắt buộc có `requested_sections` và `purpose`
- `query_company_users_sql` — truy vấn SQL cố định/parameterized, tự chèn tenant và scope; chỉ trả dữ liệu BASIC
- `query_leave_balance`
- `request_leave` / `create_leave_request`
- `create_onboarding_workflow`
- `get_contract_expiry`
- `list_pending_hr_approvals`
- `create_hr_task`
- `send_hr_notification`

Tool rộng cũ `get_employee_profile` đã bị loại khỏi cấu hình và bị API từ chối nếu Admin cố lưu lại. Các tool mới vẫn đi qua backend để xác thực người dùng, tenant, phạm vi dữ liệu, purpose, field permission, masking, yêu cầu phê duyệt và audit. AI service không nhận credential database trực tiếp.

`query_company_users_sql` không nhận raw SQL từ model. Input chỉ gồm từ khóa, phòng ban, vai trò, trạng thái và giới hạn kết quả; tên bảng, cột, `tenant_id` và tập employee ID hợp lệ đều do backend cố định. Các chuỗi tìm kiếm được bind thành tham số nên không thể biến thành câu SQL do người dùng điều khiển.

## Cấu hình quyền của AI Employee

Chỉ `Owner` và `Admin` được mở và cập nhật phần cấu hình AI Employee. Đây là quyền quản trị AI, tách biệt với quyền đọc dữ liệu HR của CEO. Người dùng khác chỉ nhận metadata công khai của agent; system prompt, tool list và knowledge ACL không được trả về.

- `tools_access`: danh sách tool AI được phép gọi. Khi lưu từ giao diện, `allowed_actions` được đồng bộ theo danh sách này; tool không được chọn sẽ bị chặn ở backend.
- `disallowed_actions`: deny-list có ưu tiên cao nhất, dùng cho policy cấm tuyệt đối.
- `knowledge_access`: ACL nguồn tri thức của AI, hỗ trợ `*`, `none`, `collection:<name>`, `document:<document_id>` và `chunk:<uuid>`.

Kết quả RAG là giao của ba lớp: ACL nguồn của AI Employee → ACL tài liệu/chunk (`tenant`, phòng ban, vai trò, confidentiality, hiệu lực) → quyền của người đang chat. Vì vậy cấp quyền một chunk cho AI không tự động cấp quyền chunk đó cho Employee nếu tài liệu vẫn bị giới hạn.

## Trải nghiệm hội thoại

Thanh hành động trong chatbot cung cấp các lối tắt `Hồ sơ`, `Tìm nhân viên`, `Ngày phép`, `Xin nghỉ`, `Hợp đồng`, `Hỏi chính sách`, `Onboarding` và `Chờ duyệt` theo đúng quyền người dùng. `Tìm nhân viên` chỉ xuất hiện cho Manager trở lên và kết quả vẫn được backend giới hạn theo cây quản lý. Form nghỉ phép, tìm nhân viên và onboarding mở ngay phía trên ô nhập chat; dữ liệu được chuyển thành ý định có cấu trúc để AI HR thực thi.

Kết quả nghiệp vụ được trả về bằng card trong luồng tin nhắn và được lưu vào `chat_messages.attachments`, vì vậy hồ sơ, quỹ phép, hợp đồng, onboarding và approval card vẫn hiển thị khi tải lại lịch sử. Người có quyền có thể duyệt hoặc từ chối yêu cầu trực tiếp trên card chờ duyệt.

## Giới hạn V1 và roadmap

Các phần dưới đây chưa được tuyên bố là hoàn thành:

- Đồng bộ hai chiều Google Calendar/Outlook; V1 ghi lịch nội bộ, không giả lập thành công của connector ngoài.
- Lịch ngày lễ theo quốc gia và lịch họp ngoài hệ thống.
- Trích xuất/chẩn đoán chữ ký hoặc điều khoản hợp đồng từ PDF/DOCX.
- Tuyển dụng và chấm CV.
- Đánh giá hiệu suất/PIP.
- Đào tạo và lộ trình nghề nghiệp.
- Quan hệ nhân viên, phản ánh riêng tư/ẩn danh.
- Offboarding và quản lý tài sản.
- Dashboard phân tích nhân sự nâng cao.

Thứ tự đề xuất tiếp theo: calendar connector và ngày lễ → contract document extraction → recruitment → performance → learning → employee relations → offboarding.

## Xác minh

- Backend: `105 passed`.
- Frontend ESLint: `0 errors` (còn warning cũ ở các trang khác).
- Next.js production build: thành công, trải nghiệm AI HR nằm trong route `/agents/HR`.
- Alembic head: `g42c9d0e6f31`.
