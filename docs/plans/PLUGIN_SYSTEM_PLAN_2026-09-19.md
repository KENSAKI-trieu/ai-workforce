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
| Nội dung | Đọc và kiểm tra `plugin.yaml`; bảng `tenant_plugin_installs` kèm migration; lệnh `install` và `uninstall` cho một tenant qua dòng lệnh |
| Tiêu chí hoàn thành | Tạo plugin cho công ty A, cài vào tenant A, chat thử và thấy câu trả lời khác tenant B trên cùng một máy |
| Rủi ro | Thấp — chưa chạm giao diện, chưa chạm phân quyền |

Kết thúc P2 là đã chứng minh được toàn bộ ý tưởng sản phẩm, đủ để demo nội bộ.

### P3 — Lưu vào cơ sở dữ liệu và bổ sung giao diện

| Hạng mục | Nội dung |
|---|---|
| Mục tiêu | Người vận hành cài plugin mà không cần lập trình viên |
| Nội dung | API cài, gỡ, liệt kê, xem trước prompt; màn hình quản lý |
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

---

## 9. Trạng thái triển khai

Cập nhật: 2026-09-21. P1 đến P4 đã được hiện thực hoá ở môi trường local. Toàn bộ 527 test của backend chạy qua, không có test nào bị sửa để lách.

### 9.1 Mã nguồn đã thêm

| Thành phần | Vị trí |
|---|---|
| Prompt mặc định và danh sách slot | `backend/app/services/agents/hr_prompts.py` |
| Đọc và kiểm tra manifest | `backend/app/plugins/manifest.py` |
| Quét gói trên đĩa | `backend/app/plugins/loader.py` |
| Phân giải overlay và thu hẹp quyền | `backend/app/plugins/resolver.py` |
| Cài và gỡ | `backend/app/plugins/service.py` |
| Dòng lệnh | `backend/app/plugins/cli.py` |
| API | `backend/app/api/v1/plugins.py` |
| Bảng dữ liệu | `backend/app/models/models.py`, migration `t53a8c0d4e62` |
| Giao diện | `frontend/app/plugins/page.tsx` |
| Gói mẫu | `backend/plugins/company-a-hr/`, `backend/plugins/company-b-hr/` |
| Ô prompt riêng của tenant | cột `ai_agents.prompt_overlay`, migration `u64b9d1e5f73` |
| Kiểm thử | `backend/tests/test_plugins.py` (41 test) |

### 9.2 Ba điểm lệch so với kế hoạch ban đầu, và lý do

**Bảng dữ liệu chuyển từ P3 lên P2.** Lệnh `install` phải ghi trạng thái ở đâu đó thì mới có nghĩa, nên bảng `tenant_plugin_installs` thuộc về P2. P3 còn lại đúng phần API và giao diện.

**Registry không nằm trong `hr_llm_flow.py`.** Kế hoạch ban đầu viết `get_prompt(tenant_id, role, slot)` đặt ngay trong module đó. Không làm vậy được: `hr_llm_flow.py` có ghi chú rõ rằng nó cố tình không import cơ sở dữ liệu, và một hàm nhận `tenant_id` thì buộc phải truy vấn. Thay vào đó lớp gọi — vốn đã giữ session — phân giải overlay rồi truyền chuỗi đã sẵn sàng xuống qua tham số `prompts`. Chiều tenant vẫn có, ranh giới module vẫn giữ.

**Thu hẹp quyền được gắn vào đối tượng agent, không truyền qua tham số.** Có 23 chỗ gọi `_require_tool`/`_can_use_tool` trong `agent_executor.py`. Thêm tham số vào cả 23 chỗ thì chỉ cần bỏ sót một chỗ là quyền không bị thu hẹp ở đúng chỗ đó — đây là kiểu hỏng nguy hiểm nhất với một cơ chế bảo mật. Vì vậy phần thu hẹp được phân giải một lần ngay sau khi nạp hàng `ai_agents`, gắn lên đối tượng dưới dạng thuộc tính thường, và mọi lần kiểm tra đều đọc nó.

Thuộc tính này **không phải cột ánh xạ**. Đây là điểm quan trọng: nếu ghi danh sách đã thu hẹp trở lại bảng `ai_agents`, thì sau khi gỡ plugin tenant sẽ mất vĩnh viễn những công cụ mà gói chỉ định giấu tạm thời. Có một test riêng khoá hành vi này (`test_narrowing_is_not_written_back_to_the_agent_row`).

### 9.3 Hai cửa vào công cụ, cả hai đều phải chặn

Phần thu hẹp quyền được thực thi ở hai nơi, vì có hai đường đi tới công cụ:

1. `agent_executor._require_tool` và `_can_use_tool` — đường chat trực tiếp.
2. `backend/app/api/v1/tool_gateway.py::_agent_permits` — đường AI service gọi ngược lại.

Chặn một nơi mà bỏ nơi còn lại thì coi như không chặn.

### 9.4 Hai lỗi phát hiện khi chạy thật

**Vòng import.** `app/services/agents/__init__.py` nạp sẵn `agent_executor`, nên import một module lá như `hr_prompts` kéo theo cả tầng service, khép thành vòng khi CLI là điểm khởi đầu. Bộ test không phát hiện ra vì pytest nạp các module theo thứ tự khác. Đã gỡ bằng cách cho `manifest.py` và `resolver.py` import muộn, giữ hai module này ở vị trí lá.

**Console Windows.** Gói được viết bằng ngôn ngữ của khách hàng, nên tên và prompt chứa ký tự ngoài ASCII. Console Windows mặc định dùng cp1252 và ném `UnicodeEncodeError` ngay ký tự tiếng Việt đầu tiên. CLI hiện tự chuyển stdout và stderr sang UTF-8.

### 9.5 Lưu ý vận hành: plugin cài cho tenant sẽ ảnh hưởng tới bộ test

Bộ test dùng tenant `acme.com` có sẵn trong cơ sở dữ liệu phát triển, và bản ghi cài plugin là dữ liệu bền, không bị cuốn theo transaction rollback của test. Trong lúc làm, việc cài thử `company-a-hr` cho `acme.com` đã làm hai test export gãy vì gói này cấm `export_hr_directory`.

Đây không phải lỗi mã nguồn — chính xác là tính năng đang hoạt động — nhưng cần nhớ: **sau khi thử plugin trên tenant mà bộ test dùng, phải gỡ ra trước khi chạy test.**

```
cd backend
./.venv/Scripts/python.exe -m app.plugins.cli installed --tenant acme.com
./.venv/Scripts/python.exe -m app.plugins.cli uninstall <tên gói> --tenant acme.com
```

### 9.6 Cách dùng nhanh

```
cd backend
./.venv/Scripts/python.exe -m app.plugins.cli list
./.venv/Scripts/python.exe -m app.plugins.cli validate
./.venv/Scripts/python.exe -m app.plugins.cli install company-a-hr --tenant <domain>
./.venv/Scripts/python.exe -m app.plugins.cli preview --tenant <domain> --role HR --slot answer
```

Trên giao diện: mục **Plugin Prompt & Skill** ở thanh bên trái. Khung bên phải hiển thị đúng chuỗi prompt mà model sẽ nhận, kèm cảnh báo riêng cho hai slot nguy hiểm là `classifier` và `leave_slot`.

### 9.7 Ba việc tồn đọng — đã xử lý xong

Cập nhật 2026-09-21. Cả ba mục đã khép lại. Toàn bộ 534 test backend và 66 test ai-service chạy qua.

#### a) Nợ kỹ thuật mục 2.1 — đã xử lý bằng cột overlay mới

Hướng "nối thẳng cột `system_prompt` vào registry" **không dùng được**. Khảo sát cho thấy mọi tenant đều đã có sẵn giá trị khác rỗng trong cột đó, sinh từ hai công thức khác nhau: `init_db` viết tay riêng cho từng role, còn `auth_service` ghép chuỗi lúc đăng ký. Nối thẳng vào sẽ đổi hành vi của toàn bộ tenant đang chạy, vi phạm nguyên tắc số 2.

Cách làm thay thế: thêm cột mới `ai_agents.prompt_overlay`, **mặc định rỗng** (migration `u64b9d1e5f73`). Rỗng nghĩa là không đổi gì, nên tenant cũ an toàn tuyệt đối. Hai màn hình `/agents/[role]` và `/ai-editor` chuyển sang sửa ô này, nhãn đổi thành "Quy ước riêng của công ty". Trường `system_prompt` bị gỡ khỏi request cập nhật của API; cột cũ giữ nguyên trong cơ sở dữ liệu, không migrate, vì giá trị trong đó là hỗn hợp giữa text seed và những lần sửa đã bị bỏ rơi.

Thứ tự xếp lớp cho slot `answer`: **prompt mặc định → plugin đã cài → text riêng của tenant**. Text của người vận hành đứng cuối để chữ họ vừa gõ thắng được chữ trong gói.

**Ràng buộc quan trọng: ô text này chỉ chạm tới slot `answer`.** Một ô nhập liệu duy nhất không có cách nào diễn đạt nó muốn sửa slot nào trong ba slot; để free text lọt vào `classifier` hoặc `leave_slot` thì người chỉ định sửa cách xưng hô có thể phá hỏng định tuyến ý định hoặc bóc sai ngày nghỉ. Muốn sửa theo từng slot thì dùng gói plugin, nơi mỗi slot được khai báo tường minh. Có test khoá riêng ràng buộc này (`test_the_overlay_never_reaches_the_routing_or_slot_prompts`).

Một lỗi phát hiện nhờ test trong lúc làm việc này: endpoint xem trước trước đó chỉ dựng overlay từ plugin, bỏ qua text của tenant. Tức là khung "prompt thực tế gửi cho model" sẽ hiển thị sai — đúng thứ nó sinh ra để chống. Nay nó gọi chung một hàm `resolve_prompt_overlay` với luồng chat.

#### b) Mã chết mục 2.2 — đã xoá

Đã xoá toàn bộ `apps/ai-service/app/prompts/` gồm `loader.py` và bảy file `.txt`/`__init__.py`, cùng trường `system_prompt_key` trên `BaseAgent` và ở năm agent module khai báo nó. Trước khi xoá đã xác nhận `chains/rag_answer.py` dùng hằng số nội bộ riêng của nó chứ không đọc file nào trong cây này. Các agent module vẫn giữ nguyên vì `agent_registry` còn dùng.

#### c) Chỉ role HR nhận overlay — không phải việc còn lại

Grep toàn bộ backend cho thấy cả hệ thống chỉ có **đúng ba** lời gọi LLM kèm system prompt, cả ba nằm trong `hr_llm_flow` và cả ba đã nhận overlay. LEGAL, IT, FINANCE, SALES, KNOWLEDGE, CEO **không có system prompt nào** trong luồng chạy thật.

Nghĩa là đây không phải plugin thiếu hỗ trợ các role đó, mà các role đó chưa có prompt để override. Mở rộng overlay sang chúng đòi hỏi trước hết phải xây luồng prompt LLM cho chúng — là tính năng mới, không phải việc tồn đọng. Việc manifest từ chối `prompts` cho role khác HR vẫn giữ nguyên và vẫn đúng.

### 9.8 Việc còn lại thật sự

- Các role ngoài HR chưa có luồng prompt LLM nào. Nếu muốn bán tuỳ biến prompt cho Legal hay Sales thì phải xây luồng đó trước, xem mục c ở trên.
- Prompt vẫn không đổi được luồng nghiệp vụ, xem mục 8.1. Giới hạn này không thay đổi.
- Trường `knowledge` trong manifest được đọc và kiểm tra, nhưng chưa có nơi nào áp dụng.

### 9.9 Plugin khi agent chạy bằng LangGraph

Cập nhật 2026-09-25. Đã kiểm tra live với Knowledge và Legal chạy qua graph.

**Những gì đi vào graph:**
- Chỉ phần `append` của slot trả lời, cùng ô "Quy ước riêng của công ty", gửi sang dưới tên `tenant_instructions`.
- Text `replace` và các slot định tuyến bị graph bỏ qua. Chúng vẫn có tác dụng khi lượt chat rơi về luồng thường, lúc model của graph lỗi.
- Công cụ bị thu hẹp được loại khỏi `allowed_tools`, và gateway trả 403 nếu bị gọi thẳng.

**Ba chỗ đã sửa:**
1. **Khung xem trước.**
   - API trả thêm `engine` và khối `graph`: slot này có tác dụng trong graph không, text graph nhận thật sự, và gói nào bị graph bỏ qua.
   - Giao diện chia slot theo từng agent. Trước đây mọi slot đều xem dưới HR, nên slot Legal luôn hiện là "chưa tuỳ biến"; nay endpoint trả 404 cho slot không thuộc role.
2. **`has_tenant_text`** so với slot trả lời của đúng role. Trước đây nó so với `answer` của HR, nên với Legal luôn sai.
3. **Công cụ bị tắt.**
   - Backend gửi `disabled_tools`, gồm tên và nhãn tiếng Việt. Đó là các công cụ role lẽ ra có nhưng công ty đã tắt.
   - Model chỉ cần nhận ra yêu cầu cần công cụ đó. Graph tự trả câu tiếng Việt "tính năng … đã bị tắt theo cấu hình của công ty" và bỏ qua bước kiểm tra trích dẫn.
   - Trước đây model tự làm thay, rồi người dùng nhận câu tiếng Anh "answer was withheld".
   - Các câu báo lỗi khác của graph cũng đã chuyển sang tiếng Việt, gom ở `apps/ai-service/app/agents/base/notices.py`.

**Xoá gói đang cài:** nút xoá không còn bị khoá. Nếu gói đang được áp dụng, hộp thoại báo và cho chọn "Gỡ rồi xoá".

---

## 10. Thêm, sửa, xoá gói ngay trong sản phẩm

Cập nhật 2026-09-21. Trước phần này, gói plugin chỉ có thể do lập trình viên viết thành file trong repo. Nay quản trị viên tự soạn gói riêng cho công ty mình trên giao diện. 545 test backend chạy qua.

### 10.1 Hai loại gói, khác nhau ở nơi lưu

| | Gói dựng sẵn | Gói tự soạn |
|---|---|---|
| Nơi lưu | File YAML trong `backend/plugins/` | Bảng `tenant_plugins` |
| Ai sửa | Lập trình viên, qua release | Quản trị viên, ngay trên giao diện |
| Phạm vi | Mọi tenant đều thấy | Chỉ tenant sở hữu |
| Sửa được trên UI | Không | Có |

Lý do không ghi file YAML từ API: container là ephemeral nên file mất khi khởi động lại, nhiều instance sẽ lệch nhau, và ghi file từ input web là bề mặt tấn công không cần thiết. Gói tự soạn vì vậy nằm trong cơ sở dữ liệu (migration `v75c0e2f6a84`).

### 10.2 Một đường kiểm tra duy nhất

Manifest gõ vào ô text đi qua đúng hàm `parse_manifest_yaml` mà file trên đĩa đi qua. Không có đường kiểm tra lỏng hơn cho gói tự soạn: vẫn từ chối khoá lạ, tên slot lạ, tên công cụ lạ, và vẫn áp luật chỉ-thu-hẹp-quyền ở resolver.

Trường `source_yaml` lưu đúng chuỗi người dùng gõ và là nguồn sự thật duy nhất; manifest được phân giải lại lúc đọc thay vì lưu thành hai dạng có thể lệch nhau.

### 10.3 Bốn ràng buộc và lý do

**Không trùng tên gói dựng sẵn.** Nếu cho trùng thì một tenant có thể chiếm tên của gói chuẩn và đổi hành vi mà nhìn vào danh sách không thấy gì bất thường.

**Không đổi tên khi sửa.** Bản ghi cài trỏ tới gói bằng tên. Cho phép đổi tên sẽ làm bản ghi đó trỏ vào hư không, và tenant lặng lẽ rơi về prompt mặc định.

**Không xoá khi đang cài.** Xoá một gói đang bật sẽ đổi cách agent trả lời ngay lúc đó mà trên màn hình không có gì nối hai việc lại với nhau. Bắt gỡ trước biến việc đó thành một bước nhìn thấy được. API trả 409 kèm lý do.

**Sửa gói đang cài thì có hiệu lực ngay**, không cần cài lại. Bản ghi cài được cập nhật số phiên bản trong cùng transaction nên không báo lệch phiên bản giả.

### 10.4 API

| Phương thức | Đường dẫn | Việc |
|---|---|---|
| GET | `/api/v1/plugins/` | Danh mục cả hai loại, kèm cờ `editable` |
| POST | `/api/v1/plugins/validate` | Kiểm tra manifest mà không lưu |
| GET | `/api/v1/plugins/authored` | Liệt kê gói của workspace |
| GET | `/api/v1/plugins/authored/{name}` | Đọc YAML để sửa |
| POST | `/api/v1/plugins/authored` | Tạo |
| PUT | `/api/v1/plugins/authored/{name}` | Ghi đè |
| DELETE | `/api/v1/plugins/authored/{name}` | Xoá |

Tất cả nằm sau `RoleRequired("Owner", "Admin", "CEO")` và đều ghi audit log. Giới hạn: 64 KB mỗi manifest, 100 gói mỗi workspace.

Endpoint `validate` tách riêng khỏi lưu để người soạn sửa được lỗi gõ nhầm mà không phải tạo rồi xoá gói để biết mình sai chỗ nào.

### 10.5 Giao diện

Nút **Tạo gói** ở đầu cột danh mục. Gói do workspace sở hữu có thêm nút sửa và nút xoá; gói dựng sẵn không có, vì chúng thuộc về repo.

Trình soạn nhận YAML trực tiếp chứ không dựng form. Form sẽ phải phản chiếu lại toàn bộ quy tắc mà parser ở backend đã có, và hai bên chắc chắn sẽ lệch nhau theo thời gian; ở đây nút **Kiểm tra** gọi đúng parser sẽ chạy lúc lưu, nên thứ người soạn thấy chính là thứ sẽ được chấp nhận.
