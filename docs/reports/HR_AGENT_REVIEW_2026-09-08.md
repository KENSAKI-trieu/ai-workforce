# Báo cáo rà soát lại HR Agent

> **Ghi chú (2026-09-24):** báo cáo này mô tả code tại thời điểm viết. Các đường dẫn `backend/app/services/...` và `agent_executor.py` đã đổi sau đợt refactor — xem [plans/REFACTOR_PLAN_2026-09-24.md](../plans/REFACTOR_PLAN_2026-09-24.md).

**Ngày:** 2026-09-08
**Phạm vi:** Toàn bộ đường đi của một tin nhắn gửi tới AI Agent `HR` (định tuyến ý định → kiểm soát quyền → nghiệp vụ → sinh câu trả lời), cộng thêm các đường API mà agent này gọi tới.
**Đối chiếu:** [HR_AGENT_FLOW_REPORT_2026-09-04_EN.md](HR_AGENT_FLOW_REPORT_2026-09-04_EN.md) mô tả kiến trúc; báo cáo này chỉ nói về **kết quả kiểm tra lại**.

---

## 1. Kết luận nhanh

Kiến trúc "LLM chỉ chọn nhãn, code quyết định hành động" vẫn đứng vững: không nhánh nào cho phép mô hình tự gọi tool, tự chọn nhân viên hay tự nới quyền. Toàn bộ test đều xanh.

| Hạng mục | Kết quả |
|---|---|
| Test HR (7 file) | **160 pass / 0 fail** |
| Toàn bộ test backend | **481 pass / 0 fail** (81 giây) — sau khi vá mục 2.3: **493 pass / 0 fail** |
| Lỗi có thể gây HTTP 500 | **1** (đã tái hiện) |
| Điểm lệch phân quyền | **1** (đường danh bạ không kiểm tra quyền section) |
| Thiếu đo lường / vận hành | **2** (chi phí LLM, timeout) |
| Điểm cần cải thiện chất lượng trả lời | **8** |

Không tìm thấy lỗ hổng rò rỉ dữ liệu xuyên tenant hay vượt scope trong lần rà soát này.

---

## 2. Vấn đề cần xử lý

### 2.1. [CAO] Hồ sơ nhân viên gây lỗi 500 khi quyền BASIC bị từ chối

**Hiện trạng.** Policy engine cho phép yêu cầu đi tiếp nếu **ít nhất một** section được duyệt. Nếu người hỏi có `hr.contract.view` nhưng **không** có `hr.directory.view`, quyết định trả về `allowed=True`, `allowed_sections=('CONTRACT',)`, `denied=('BASIC',)`. Khi đó [agent_executor.py:185](../../backend/app/services/agents/agent_executor.py#L185) lấy `basic` rỗng, còn [agent_executor.py:236](../../backend/app/services/agents/agent_executor.py#L236) truy cập khóa `name` của thẻ hồ sơ → `KeyError` → HTTP 500 (trên luồng stream hiện ra là "Không thể hoàn tất yêu cầu").

**Đã tái hiện** bằng cách gọi trực tiếp policy engine với một chức vụ tùy biến mang `permissions=['hr.contract.view','hr.scope.company']`:

```
allowed= True   allowed_sections= ('CONTRACT',)   denied= ('BASIC',)   {'BASIC': 'MISSING_PERMISSION'}
→ _employee_profile_reply(...) → CRASH: KeyError 'name'
```

**Vì sao bây giờ mới thành rủi ro.** Trước đây các chức vụ mặc định luôn gói `hr.directory.view` chung với mọi quyền HR khác. Tính năng **chức vụ tùy biến** vừa thêm cho phép Admin tick từng quyền riêng lẻ, nên tổ hợp "xem hợp đồng nhưng không xem danh bạ" là tạo được từ giao diện.

**Đề xuất.** Coi `BASIC` là điều kiện cần của mọi thẻ hồ sơ: nếu `BASIC` bị từ chối thì trả lời từ chối kèm lý do `MISSING_PERMISSION` thay vì dựng thẻ; hoặc để hàm dựng câu trả lời chịu được `employee` rỗng. Kèm một test cho đúng tổ hợp quyền này.

### 2.2. [CAO] Đường danh bạ chỉ kiểm tra scope, không kiểm tra quyền section

**Hiện trạng.** `query_company_users_sql` ([hr_employee_tools.py:82](../../backend/app/services/hr_employee_tools.py#L82)) và `export_company_users_dataset` ([hr_employee_tools.py:203](../../backend/app/services/hr_employee_tools.py#L203)) chỉ lọc theo `authorized_employee_ids` (tức `hr.scope.*`). Chúng **không** đi qua `authorize_employee_access`, nên **không** kiểm tra `hr.directory.view` — trong khi đúng dữ liệu BASIC đó, khi hỏi qua đường hồ sơ, lại bị từ chối `MISSING_PERMISSION` nếu thiếu quyền này.

**Hệ quả.** Một chức vụ tùy biến có `hr.scope.company` nhưng không có `hr.directory.view` vẫn liệt kê và xuất file được toàn bộ danh bạ công ty (tên, email, phòng ban, chức danh, quản lý trực tiếp). Hai đường cùng một loại dữ liệu cho hai kết quả trái ngược — đây là cặp đôi của mục 2.1: một bên quá chặt đến mức vỡ, một bên quá lỏng.

**Đề xuất.** Chốt một quy tắc duy nhất: hoặc bổ sung kiểm tra `hr.directory.view` vào hai hàm trên, hoặc tuyên bố rõ "BASIC = scope, không cần quyền section" và bỏ `BASIC` khỏi `HR_SECTION_PERMISSIONS`. Không nên để hai luật song song như hiện nay.

> Ghi chú kèm theo: endpoint `GET /hr/employees/export` ([hr.py:44](../../backend/app/api/v1/hr.py#L44)) chỉ yêu cầu đăng nhập, quyền được suy ra từ scope bên trong. Việc thu hồi tool `export_hr_directory` của agent do đó chỉ ẩn tính năng khỏi khung chat chứ không chặn endpoint. Điều này **đã được ghi chú trong code** và là chủ ý, nhưng cần nói rõ với người vận hành để tránh hiểu nhầm là đã khóa dữ liệu.

### 2.3. [TRUNG] Chi phí LLM của HR không được ghi nhận — ĐÃ SỬA (2026-09-08)

HR gọi mô hình 1–3 lần mỗi lượt chat (phân loại ý định → trích xuất ngày nghỉ → viết lại câu trả lời) qua `AIServiceClient.generate_text`. AI service **có** trả về khối `usage`, nhưng [hr_llm_flow.py](../../backend/app/services/agents/hr_llm_flow.py) chỉ đọc `content` và `provider` rồi bỏ phần còn lại. `log_llm_cost` khi đó chỉ được gọi từ [tool_gateway.py:319](../../backend/app/api/v1/tool_gateway.py#L319) (đường orchestration), nên toàn bộ token của HR agent không xuất hiện trong dashboard chi phí và không tính vào ngân sách tenant.

**Đã xử lý.** Ba lời gọi mô hình của HR nay báo `usage` về cho `_hr_llm_usage_recorder` trong [agent_executor.py](../../backend/app/services/agents/agent_executor.py), hàm này ghi một dòng `LLMCostLog` với `agent_role="HR"`, `usage_source="PROVIDER"`, kèm `user_id` và phòng ban của người hỏi — đủ cho cả bốn cách bóc tách của dashboard (theo agent, theo nhân viên, theo phòng ban, theo tháng). Việc đo không bao giờ được phép làm hỏng câu trả lời: mô hình chưa có bảng giá, payload token sai, hay lỗi database đều chỉ ghi cảnh báo. Provider `local` không tính tiền nên không ghi dòng nào.

Kèm theo đó, `gpt-4o-mini` — mô hình mặc định của AI service — được bổ sung bảng giá riêng. Trước đây nó rơi vào tiền tố `gpt-4o-` và **bị tính giá gấp khoảng 16 lần** giá thật.

`gemini-3.6-flash` (mô hình Gemini mặc định) được tạm tính theo đơn giá của `gemini-2.5-flash` — 0,30 USD vào / 2,50 USD ra mỗi 1 triệu token — để chi phí Gemini có mặt trên dashboard. **Đây là ước lượng, không phải giá công bố**, cần thay bằng giá thật; các dòng đã ghi đều mang `pricing_version` nên phân biệt được về sau.

### 2.4. [TRUNG] Rủi ro treo lâu: 3 lần gọi LLM tuần tự × timeout 120 giây

`AI_SERVICE_TIMEOUT_SECONDS` mặc định **120s** ([config.py:87](../../backend/app/core/config.py#L87)) và áp cho mọi lời gọi. Một lượt xin nghỉ phép gọi 2 lần, một câu hỏi chính sách gọi 2 lần → trường hợp xấu nhất người dùng chờ **4–6 phút** rồi mới nhận fallback.

**Đề xuất.** Đặt timeout riêng, ngắn (5–10s) cho phân loại ý định và trích xuất slot — hai việc này vốn đã có nhánh fallback an toàn, chờ lâu không đem lại lợi ích gì.

### 2.5. [TRUNG] Lỗi nghiệp vụ nghỉ phép trả về tiếng Anh thô

`request_leave` chỉ Việt hóa trường hợp hết quỹ phép; ba trường hợp còn lại trả nguyên `detail` tiếng Anh vào khung chat:

- `Leave cannot start in the past` ([hr_service.py:281](../../backend/app/services/hr_service.py#L281))
- `This leave period overlaps an existing request` ([hr_service.py:291](../../backend/app/services/hr_service.py#L291))
- `No eligible manager is configured for this employee` ([hr_service.py:295](../../backend/app/services/hr_service.py#L295))

### 2.6. [TRUNG] Lớp fallback từ khóa bỏ sót nhiều câu phổ biến

Khi AI service tắt hoặc lỗi, bộ luật từ khóa là lớp duy nhất còn lại. Kết quả đo trực tiếp trên `_classify_hr_intent`:

| Câu hỏi | Nhãn nhận được | Đúng ra phải là |
|---|---|---|
| "Tôi muốn nghỉ từ 20/09 đến 22/09 vì đi du lịch" | `UNKNOWN` | `ACTION_LEAVE_REQUEST` |
| "Nhân viên nào sắp hết hạn hợp đồng?" | `UNKNOWN` | `CONTRACT_EXPIRY` |
| "Công ty có bao nhiêu người?" | `POLICY_QUERY` | `EMPLOYEE_DIRECTORY` |
| "Ai là quản lý của tôi?" | `UNKNOWN` | `SELF_PROFILE` |

Trường hợp thứ ba đáng lưu ý nhất: nó **không** rơi vào nhánh "không hiểu" mà đi tra tài liệu HR, tức người dùng nhận một câu trả lời trông hợp lệ cho câu hỏi đếm nhân sự. Ba trường hợp còn lại chỉ mất tính năng, không sai dữ liệu.

### 2.7. [TRUNG] Chỉ quỹ phép được chặn "hỏi hộ người khác"

`_leave_balance_names_another_person` ([agent_executor.py:487](../../backend/app/services/agents/agent_executor.py#L487)) chặn câu "An còn bao nhiêu ngày phép" để khỏi trả số liệu của chính người hỏi dưới tên người khác. Các nhánh `SELF_COMPENSATION`, `SELF_PRIVATE_PROFILE`, `SELF_CONTRACT` **không có** lớp bảo vệ tương đương: nếu router LLM gán nhầm "lương của An là bao nhiêu" thành `SELF_COMPENSATION`, hệ thống trả lương **của người hỏi**. Không rò rỉ dữ liệu người khác (câu trả lời có ghi tên chủ hồ sơ), nhưng là câu trả lời sai một cách âm thầm — đúng loại lỗi mà guard của quỹ phép sinh ra để chặn.

### 2.8. [THẤP] Các điểm nhỏ còn lại

| # | Vấn đề | Vị trí |
|---|---|---|
| 1 | `FULL_PROFILE` chỉ nhận mục đích qua cụm tiếng Việt (cộng thêm `contract renewal`, `payroll`, `onboarding`); "performance review" tiếng Anh bị từ chối | agent_executor.py, nhánh FULL_PROFILE |
| 2 | Lọc phòng ban chỉ kích hoạt khi câu có "phòng/bộ phận/department" — "team kế toán" sẽ liệt kê **toàn công ty** mà không cảnh báo lệch phạm vi | `_resolve_requested_departments` |
| 3 | `SECTION_PERMISSIONS` trong [hr_access_policy.py:26](../../backend/app/services/hr_access_policy.py#L26) trỏ tới mã quyền cũ `employee.*.read` không còn trong catalog; chỉ dùng cho `decision.permissions` mà không nơi nào đọc → mã chết dễ gây nhầm khi audit quyền | hr_access_policy.py |
| 4 | Chưa có năng lực: đếm người nghỉ theo ngày (đang chuyển sang tra tài liệu), duyệt đơn qua chat, người dùng tự cập nhật thông tin cá nhân | — |
| 5 | Câu trả lời cứng bằng tiếng Việt; người hỏi tiếng Anh vẫn nhận tiếng Việt, trừ 5 intent được LLM viết lại | toàn bộ nhánh |
| 6 | `create_leave_request` chặn "ngày trong quá khứ" theo `date.today()` của máy chủ, trong khi ngày tham chiếu lại lấy theo timezone của tenant | hr_service.py:281 |

---

## 3. Những gì đã kiểm tra và thấy đúng

- **Cách ly tenant:** mọi truy vấn nhân viên đều kèm `tenant_id` của người gọi; `_scope_for_employee` chặn `CROSS_TENANT` trước mọi bước khác.
- **Không có SQL tự do:** `query_company_users_sql` là câu lệnh cố định, tham số hóa; mô hình không đưa được tên bảng, cột hay điều kiện nào vào.
- **Fail-closed khi định tuyến:** nhãn ngoài danh sách bị loại; `ACTION` không khớp 3 nhãn hành động → `UNKNOWN`; câu hỏi mang nhãn hành động → `POLICY_QUERY`. Đã có test bao phủ.
- **Vết kiểm toán:** mọi lần đọc hồ sơ, tra danh bạ, xuất file, tạo đơn nghỉ đều ghi `AuditLog` kèm section được duyệt/bị từ chối và `request_id`.
- **Nghiệp vụ nghỉ phép:** kiểm tra trùng kỳ nghỉ, khóa bản ghi quỹ phép, giữ chỗ (`reserved_days`) kèm bút toán `LeaveLedger`, thông báo cho người duyệt — đầy đủ và nhất quán.
- **Bảo vệ số liệu khi LLM viết lại:** `_preserves_card_numbers` loại bỏ bản viết lại làm mất hoặc sai con số chính; chỉ 5 intent không nhạy cảm được đưa qua LLM.
- **Đồng bộ danh sách năng lực:** `HR_CORE_TOOLS` / `HR_RETIRED_TOOLS` / phiên bản cấu hình 8 khớp giữa executor, seeding, `init_db` và API cấu hình.

---

## 4. Thứ tự đề xuất xử lý

1. **2.1** — lỗi 500, sửa nhanh, có thể chạm phải ngay khi khách dùng chức vụ tùy biến.
2. **2.2** — chốt lại một quy tắc quyền duy nhất cho dữ liệu BASIC.
3. ~~**2.3**~~ đã xong; còn **2.4** (timeout) là việc vận hành vài dòng code.
4. **2.5 + 2.6 + 2.7** — chất lượng trả lời và độ bền khi LLM chết.
5. **2.8** — dọn dần.
