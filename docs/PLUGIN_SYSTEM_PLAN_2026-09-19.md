# Kế hoạch xây dựng hệ thống Plugin cho Prompt và Skill

Ngày chốt: 2026-09-19

## 1. Bối cảnh và phạm vi

Định hướng sản phẩm gồm ba hạng mục:

1. Triển khai hệ thống lên AWS.
2. Bổ sung prompt riêng cho từng khách hàng (công ty A, công ty B) để người dùng cuối dùng dễ hơn.
3. Đóng gói và phân phối prompt, skill dưới dạng plugin.

Kế hoạch này chỉ bao phủ hạng mục 2 và 3, phát triển hoàn toàn ở môi trường local. Hạng mục 1 tạm hoãn; phần hiện trạng hạ tầng được ghi lại ở mục 8 để không mất thông tin đã khảo sát.

Thứ tự thực hiện được đảo so với danh sách ban đầu: hạng mục 3 về bản chất là cơ chế đóng gói và phân phối cho hạng mục 2. Xây plugin trước khi prompt được nối vào runtime sẽ tạo ra một cơ chế phân phối thứ không có tác dụng.

## 2. Hiện trạng: ba nơi chứa prompt, hai nơi đã chết

Đây là phát hiện quyết định hình dạng của plugin. Hệ thống hiện có ba vị trí chứa prompt, nhưng chỉ một vị trí thực sự ảnh hưởng tới câu trả lời của agent.

| Nơi | Vị trí | Có UI/API sửa | Runtime có đọc |
|---|---|---|---|
| 1 | Cột `ai_agents.system_prompt` trong PostgreSQL | Có | **Không** |
| 2 | `apps/ai-service/app/prompts/system/*.txt` | Không | **Không** |
| 3 | Ba hằng số Python trong `backend/app/services/agents/hr_llm_flow.py` | Không | Có |

### 2.1 Nơi 1 — cột `system_prompt` trong cơ sở dữ liệu

Bảng `ai_agents` đã tách theo tenant và có đủ trường để cá biệt hoá: `system_prompt`, `tools_access`, `knowledge_access`, `allowed_actions`, `disallowed_actions` (`backend/app/models/models.py:328-355`).

API cập nhật có ở `backend/app/api/v1/agents.py:52-62`. Giao diện sửa prompt có ở `frontend/app/agents/[role]/page.tsx` và `frontend/app/ai-editor/page.tsx`.

Nhưng trong `backend/app/services/agents/agent_executor.py` (2436 dòng), object `AIAgent` chỉ được truy vấn ra để kiểm tra quyền sử dụng tool qua `_can_use_tool` và `_require_tool` (dòng 144-161). Trường `system_prompt` không bao giờ được đưa vào lời gọi LLM.

Hệ quả hiện tại: quản trị viên của một tenant sửa prompt trên giao diện, hệ thống báo lưu thành công, dữ liệu ghi xuống cơ sở dữ liệu đúng, nhưng agent trả lời không thay đổi.

### 2.2 Nơi 2 — thư mục prompt của ai-service

Hàm `load_prompt()` định nghĩa tại `apps/ai-service/app/prompts/loader.py:9`. Tìm kiếm chuỗi `load_prompt(` trên toàn bộ mã nguồn chỉ trả về đúng dòng định nghĩa đó, không có nơi nào gọi.

Kéo theo, trường `system_prompt_key` khai báo trên `apps/ai-service/app/agents/base/agent.py:10` và khai báo cụ thể ở năm agent module cũng không có nơi đọc. Ví dụ `apps/ai-service/app/agents/human_resources/agent.py:26` ghi `system_prompt_key="system/human_resources"`.

Năm file `.txt` trong `apps/ai-service/app/prompts/system/` chưa từng đi vào một lời gọi LLM nào. Toàn bộ nhánh này là mã chết.

### 2.3 Nơi 3 — ba hằng số trong `hr_llm_flow.py`

Đây là prompt thật sự chạy:

| Hằng số | Dòng định nghĩa | Dòng sử dụng | Nhiệm vụ |
|---|---|---|---|
| `_CLASSIFIER_SYSTEM_PROMPT` | `hr_llm_flow.py:88` | `:189` | Phân loại ý định, quyết định nhánh xử lý |
| `_ANSWER_SYSTEM_PROMPT` | `hr_llm_flow.py:124` | `:399` | Sinh câu trả lời cuối từ bằng chứng đã lọc quyền |
| `_LEAVE_SLOT_SYSTEM_PROMPT` | `hr_llm_flow.py:130` | `:249` | Bóc tham số cho đơn xin nghỉ |

Cả ba là hằng số ở cấp module: không nhận tham số tenant, không đọc cơ sở dữ liệu, không đọc file cấu hình.

### 2.4 Hệ quả bắt buộc đối với thiết kế plugin

Nếu plugin ghi prompt vào nơi 1 hoặc nơi 2, thao tác cài plugin sẽ báo thành công và không thay đổi bất cứ điều gì trong hành vi của agent. Lỗi này hoàn toàn im lặng: không sinh exception, không ghi log cảnh báo, không có dấu hiệu nào trên giao diện.

Plugin bắt buộc phải nhắm vào nơi 3. Nơi 3 hiện chưa có chiều tenant, nên phải tạo chiều đó trước.

## 3. Nguyên tắc thiết kế

1. **Prompt tách theo slot, không gộp thành một khối.** Ba prompt ở mục 2.3 làm ba việc khác hẳn nhau. Gộp chúng thành một ô nhập liệu duy nhất sẽ khiến người chỉnh sửa vô tình phá hỏng bộ định tuyến khi họ chỉ định đổi giọng văn.
2. **Mặc định phải giữ nguyên hành vi hiện tại.** Tenant không cài plugin nào phải nhận đúng chuỗi prompt đang chạy hôm nay, không sai lệch một ký tự.
3. **Plugin chỉ được thu hẹp quyền, không bao giờ được mở rộng.** Xem mục 7.
4. **Cài đặt và gỡ bỏ phải đối xứng.** Gỡ plugin đưa tenant trở về đúng trạng thái mặc định.

## 4. Mô hình plugin

Một plugin là một thư mục `plugins/<tên>/` chứa file `plugin.yaml`. Cấu trúc đề xuất:

```yaml
name: company-a-hr
version: 1.0.0
display_name: "Cấu hình HR cho Công ty A"
target_role: HR
min_platform_version: 5

prompts:
  classifier:
    mode: append
    text: |
      <phần bổ sung cho _CLASSIFIER_SYSTEM_PROMPT>
  answer:
    mode: append
    text: |
      <phần bổ sung cho _ANSWER_SYSTEM_PROMPT>
  leave_slot:
    mode: replace
    text: |
      <thay thế _LEAVE_SLOT_SYSTEM_PROMPT>

skills:
  tools_access: [hybrid_rag_search, query_leave_balance]
  disallowed_actions: [export_hr_directory]

knowledge:
  - "collection:company_a_policies"
```

Mỗi mục prompt hỗ trợ hai chế độ:

- `append` — nối thêm vào prompt gốc. An toàn hơn, dùng cho thuật ngữ nội bộ, quy tắc xưng hô, quy tắc bổ sung. Đây là mặc định.
- `replace` — thay hẳn prompt gốc. Dùng khi khách hàng cần hành vi khác biệt lớn. Rủi ro cao hơn vì mất các ràng buộc có sẵn trong prompt gốc.

Phần `knowledge` dùng lại cơ chế selector đã có, với logic kiểm tra tính hợp lệ nằm sẵn ở `backend/app/api/v1/agents.py:87-117`.

## 5. Kế hoạch bốn giai đoạn

### P1 — Prompt registry có chiều tenant

| Hạng mục | Nội dung |
|---|---|
| Mục tiêu | Tạo điểm cắm cho plugin; chưa có plugin nào ở bước này |
| File chạm | `backend/app/services/agents/hr_llm_flow.py`, thêm một module registry mới |
| Nội dung | Gom ba hằng số thành `get_prompt(tenant_id, role, slot)`; khi không tìm thấy override thì trả về đúng chuỗi mặc định hiện tại |
| Tiêu chí hoàn thành | Toàn bộ test hiện có chạy qua mà không sửa test để lách; hành vi agent không đổi |
| Rủi ro | Thấp — thuần tuý nối dây, không đổi logic nghiệp vụ |

Giai đoạn này không thể bỏ qua. Không có ổ cắm thì plugin không có chỗ cắm vào.

### P2 — Manifest, loader và CLI ở local

| Hạng mục | Nội dung |
|---|---|
| Mục tiêu | Tạo và cài plugin thủ công trên máy lập trình viên |
| Nội dung | Đọc và kiểm tra `plugin.yaml`; lệnh `install` và `uninstall` cho một tenant qua dòng lệnh |
| Tiêu chí hoàn thành | Tạo plugin cho công ty A, cài vào tenant A, chat thử và thấy câu trả lời khác tenant B trên cùng một máy |
| Rủi ro | Thấp — chưa chạm giao diện, chưa chạm phân quyền |

Kết thúc P2 là đã chứng minh được toàn bộ ý tưởng sản phẩm, đủ để demo nội bộ.

### P3 — Lưu vào cơ sở dữ liệu và bổ sung giao diện

| Hạng mục | Nội dung |
|---|---|
| Mục tiêu | Người vận hành cài plugin mà không cần lập trình viên |
| Nội dung | Bảng `tenant_plugin_installs` kèm migration Alembic; API cài, gỡ, liệt kê; màn hình quản lý |
| Tiêu chí hoàn thành | Cài và gỡ plugin hoàn toàn trên giao diện; mỗi thao tác có ghi audit log |
| Rủi ro | Trung bình — cần quyết định ai được phép cài plugin. Đề xuất giới hạn trong `AGENT_CONFIG_ROLES` đã có sẵn |

Trong giai đoạn này cần xử lý luôn nợ kỹ thuật ở mục 2.1: hoặc nối cột `ai_agents.system_prompt` vào registry của P1, hoặc gỡ ô nhập liệu đó khỏi giao diện. Để nguyên trạng thái "sửa được mà không có tác dụng" là không chấp nhận được khi sản phẩm bán ra ngoài.

### P4 — Skill qua plugin

| Hạng mục | Nội dung |
|---|---|
| Mục tiêu | Plugin điều chỉnh được bộ công cụ mà agent dùng |
| File chạm | `backend/app/tools/registry.py`, lớp guardrails |
| Tiêu chí hoàn thành | Có kiểm thử chứng minh plugin không thể cấp thêm bất kỳ quyền nào so với role gốc |
| Rủi ro | **Cao** — đây là phần chạm trực tiếp vào phân quyền |

Để cuối cùng vì đây là phần rủi ro nhất.

## 6. Thứ tự và điểm dừng

P1 và P2 nên làm liền mạch, vì P1 một mình chưa tạo ra giá trị nhìn thấy được. Sau P2 là một điểm dừng an toàn: sản phẩm đã chứng minh được năng lực cá biệt hoá theo khách hàng, và có thể tạm dừng để quay lại hạng mục AWS nếu ưu tiên thay đổi.

P4 nên đứng riêng và có một đợt rà soát bảo mật độc lập trước khi nhập vào nhánh chính.

## 7. Ràng buộc bảo mật

Luật cứng, áp dụng từ P1 và được kiểm thử từ P4:

> Plugin chỉ được thu hẹp quyền so với role gốc của người dùng. Plugin không bao giờ được mở rộng quyền.

Lý do: nếu plugin có thể thêm tool hoặc thêm `allowed_actions`, thì việc cài một plugin trở thành một con đường nâng quyền. Khi đó ranh giới bảo mật của hệ thống không còn nằm ở tầng phân quyền nữa mà nằm ở nội dung một file YAML, và file đó có thể do khách hàng hoặc bên thứ ba cung cấp.

Cách triển khai: quyền hiệu lực luôn được tính bằng phép giao giữa quyền của role và quyền plugin khai báo, không bao giờ bằng phép hợp.

Điểm cần lưu ý khi làm P4: vùng mã phân quyền này đã từng có lỗ hổng trong đợt rà soát trước, nên mọi thay đổi ở `backend/app/tools/registry.py` và lớp guardrails cần có kiểm thử riêng cho trường hợp plugin cố tình khai báo quyền vượt quá role.

Ràng buộc bổ sung: nội dung prompt trong plugin là dữ liệu không đáng tin. Prompt do khách hàng viết không được phép ghi đè các chỉ dẫn bảo mật đang được chèn ở `apps/ai-service/app/middleware/tenant_acl.py:45-54`. Khối chỉ dẫn bảo mật phải luôn đứng sau nội dung plugin trong chuỗi system message.

## 8. Giới hạn đã biết

### 8.1 Prompt không đổi được luồng nghiệp vụ

HR agent định tuyến bằng chuỗi lệnh `if` cứng sau khi LLM chỉ trả về một nhãn ý định. Vì vậy prompt riêng cho từng công ty sẽ đổi được giọng văn, phạm vi trả lời, thuật ngữ nội bộ và cách từ chối, nhưng không đổi được các bước nghiệp vụ.

Ví dụ cụ thể: một khách hàng muốn quy trình duyệt nghỉ phép hai cấp thay vì một cấp thì plugin không đáp ứng được, phải sửa mã nguồn. Cần thống nhất giới hạn này với khách hàng trước khi cam kết.

### 8.2 Hiện trạng hạ tầng, ghi lại cho hạng mục AWS về sau

- Quy trình CD tại `.github/workflows/cd.yml:36` chỉ build và đẩy image cho `backend` và `frontend`. Image `ai-service`, nơi chứa toàn bộ phần LLM và RAG, chưa từng được xuất bản.
- `docker-compose.yml` hiện là bản dành cho phát triển: có bind mount mã nguồn, đặt `container_name` cố định nên không scale được, và mở cổng `5432` cùng `6379` ra ngoài host.
- Khối `ai-service` khai báo cần GPU NVIDIA (`docker-compose.yml:96-102`), trong khi embedding đi qua Gemini API và rerank đi qua Jina API. Cần xác nhận lại nhu cầu GPU thật sự trước khi chọn loại máy chủ, vì đây là yếu tố quyết định chi phí hạ tầng.
- Cả ba service dùng chung một file `backend/.env` chứa `GOOGLE_AI_API_KEY`, `JINA_API_KEY` và `JWT_SECRET`.
- Chưa có mã hạ tầng dạng khai báo, chưa có load balancer kèm TLS, chưa có phương án sao lưu cho PostgreSQL.

Điểm thuận lợi: `backend/Dockerfile:28` đã chạy `alembic upgrade head` khi khởi động container, nên migration tự động đã sẵn sàng cho triển khai.
