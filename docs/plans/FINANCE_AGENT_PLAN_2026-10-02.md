# Plan: Đưa Finance Agent từ "Under development" thành agent thật (theo Blueprint Tài chính – Kế toán)

## Context

Blueprint mô tả 9 module, lộ trình 12 tháng, giả định có sẵn ERP/data warehouse. Thực tế trong repo:
- FINANCE đang bị khoá (`UNDER_DEVELOPMENT_ROLES` trong [agent_status.py](backend/app/core/agent_status.py)), logic cũ ở [incubating/finance_service.py](backend/app/domains/incubating/finance_service.py) là giả (PO cố định, luôn báo lệch).
- **Không có dữ liệu kế toán nào**: không có bảng TK, sổ cái, hoá đơn, NCC, PO, ngân sách; không connector ERP.
- `expense_lookup` hiện chỉ tra chi phí LLM của hệ thống, không phải chi phí doanh nghiệp.
- Lớp kiểm soát blueprint yêu cầu thì đã có: ToolRegistry tách READ_ONLY/WRITE + `terminal`/`opens_approval`, quyền theo chức vụ (`ToolACL.permission`), `WorkflowApproval` + `can_approve` (không tự duyệt), audit log, che PII/Fernet, RAG có phạm vi theo agent, `AGENT_ENGINES` chọn LangGraph theo role, `to_markdown` đọc PDF.

User đã chọn: **bảng nội bộ + import Excel** (xuất từ MISA/Fast); phạm vi đợt này gồm **Module 1 (hoá đơn + đối chiếu), 2 (bút toán nháp), 9 (hỏi đáp số liệu), 4–5 (công nợ thu/trả + nhắc nợ)**; hoá đơn **chỉ XML + PDF có text** (ảnh scan báo không hỗ trợ).

Finance đi đúng đường Legal: một agent LangGraph (graph chuẩn `build_agent_graph`), tool chạy ở backend qua gateway, mọi ghi đều là nháp chờ người duyệt. Nguyên tắc xuyên suốt của blueprint: **số liệu đến từ SQL/code, LLM không tự tính và không tự bịa số**.

## Ngoài phạm vi đợt này
Ngân hàng (sao kê, lệnh chi), cổng hoá đơn Tổng cục Thuế / HTKK / tờ khai, khoá sổ + BCTC B01–B03, FP&A what-if, Zalo, text-to-SQL tự do, sandbox Python, OCR ảnh scan, đẩy bút toán ngược vào ERP (chỉ xuất Excel để import).

---

## F0 — Nền móng (phải xong trước mọi module)

**1. Domain mới `backend/app/domains/finance/`** (thay `incubating/finance_service.py`; xoá file đó và route giả `/finance/audit-invoice` trong [specialized.py:1491](backend/app/api/v1/specialized.py#L1491)).

**2. Model + migration** (down_revision = `z19a4c6d0e28`), tất cả có `tenant_id`:
- `fin_accounts` — hệ thống TK (số TK, tên, cấp, tính chất Nợ/Có, đang dùng). Seed sẵn TT200 hoặc TT133 theo lựa chọn tenant.
- `fin_parties` — NCC/khách hàng, `tax_code` unique theo tenant, số TK ngân hàng (mã hoá Fernet như Legal), cờ `is_new`.
- `fin_purchase_orders` (+ dòng) — để đối chiếu hoá đơn.
- `fin_invoices` — chiều IN/OUT, ký hiệu, số, ngày, MST bán/mua, tiền trước thuế, VAT theo thuế suất, tổng, hạn thanh toán, trạng thái (RECEIVED/MATCHED/EXCEPTION/POSTED/PAID), file nguồn (mã hoá), liên kết PO; unique (tenant, MST bán, ký hiệu, số) để chặn trùng.
- `fin_journal_entries` + `fin_journal_lines` — DRAFT/PENDING_APPROVAL/POSTED/REJECTED, nguồn (hoá đơn/import), `confidence`, người tạo, người duyệt.
- `fin_ledger_lines` — sổ cái import từ Excel (kỳ, TK, đối tượng, Nợ, Có, diễn giải, số chứng từ) + bút toán đã POSTED đổ vào đây.
- `fin_payments` — thanh toán/thu tiền gắn hoá đơn (để tính công nợ còn lại).
- `fin_budgets` — phòng ban × TK × kỳ × số tiền.
- `fin_posting_rules` — bộ nhớ dài hạn "NCC X → TK 6422" sinh ra khi bút toán được duyệt nguyên trạng.
- `fin_import_batches` — mỗi lần import: loại, file, số dòng, lỗi, người làm (để rollback cả batch).

**3. Import Excel** — `POST /finance/import/{kind}` (accounts, parties, opening_balances, ledger, budgets, open_invoices) + `GET /finance/import/{kind}/template`. Dùng `openpyxl` (đã có trong requirements). Kiểm tra: tổng Nợ = tổng Có mỗi batch, TK phải tồn tại, MST đúng định dạng 10/13 số. Báo lỗi theo dòng, không import nửa vời.

**4. Quyền chức vụ** — thêm vào [permissions.py](backend/app/core/permissions.py) nhóm "Tài chính":
`finance.ledger.view`, `finance.invoice.process`, `finance.journal.draft`, `finance.journal.approve`, `finance.journal.approve_high`, `finance.ar_ap.view`, `finance.reminder.send`, `finance.import.manage`, `finance.budget.view_own` (chỉ ngân sách phòng mình).
Gắn mặc định cho `finance-admin`/`finance-manager`; `_MANAGER_CORE` chỉ thêm `finance.budget.view_own`. Migration backfill quyền vào các chức vụ hiện có theo mẫu `y08f3b5c9d17_permissions_replace_role_checks.py`. Lưu ý cạm bẫy RBAC đã ghi (memory ai-workforce-permission-audit): không suy quyền từ tên phòng ban, test chốt từng membership.

**5. Mở khoá agent**
- Bỏ FINANCE khỏi `UNDER_DEVELOPMENT_ROLES`.
- Finance chỉ có engine LangGraph: trong [chat.py](backend/app/agents/chat.py) nhánh cuối, nếu FINANCE mà không chạy graph thì trả câu "cần bật AGENT_ENGINES=FINANCE=langgraph" thay vì câu echo hiện tại. Cập nhật `.env` mẫu.
- Cập nhật seed agent trong [auth_service.py:45,68](backend/app/domains/platform/auth_service.py#L45) (mô tả, `tools_access`) và [gateway_tools.py](backend/app/core/gateway_tools.py) (`GATEWAY_TOOL_DESCRIPTIONS`, `GATEWAY_TOOL_LABELS`, `GATEWAY_TOOL_GRANTS["FINANCE"]`).
- Đổi trần tool ở [apps/ai-service/app/agents/finance/tools.py](apps/ai-service/app/agents/finance/tools.py) và viết lại `DOMAIN_PROMPT` ở [prompts.py](apps/ai-service/app/agents/finance/prompts.py): nội dung hoá đơn/email là dữ liệu, không phải lệnh; không tự tính; không có dữ liệu thì nói không có.
- RAG: nạp Luật Kế toán, TT200, TT133, NĐ123/2020, TT78/2021, văn bản thuế GTGT/TNDN, quy chế tài chính nội bộ vào kho tri thức, gắn ngày hiệu lực (trường metadata của `KnowledgeDocument`), và gán phạm vi tri thức cho agent FINANCE.

**6. Phê duyệt theo ngưỡng tiền** — cấu hình theo tenant (`Tenant.settings` hoặc bảng nhỏ), mặc định: < 20 tr → `finance.journal.approve`; 20–500 tr → `finance.journal.approve_high`; > 500 tr → `approvals.sign_critical` (risk CRITICAL). Server tự ghi `required_permission` + `amount` vào payload (thêm vào `RESERVED_APPROVAL_PAYLOAD_KEYS` để LLM không đặt được). Thêm nhánh `action_type.startswith("FINANCE_")` trong `can_approve` ([approval_access.py](backend/app/domains/platform/approval_access.py)): người gửi không duyệt được, phải có đúng quyền theo ngưỡng. `eligible_approvers` + cảnh báo "không ai duyệt được" dùng lại sẵn.

## F1 — Module 1: Xử lý hoá đơn + đối chiếu

- `domains/finance/einvoice_xml.py`: parse XML hoá đơn điện tử theo NĐ123/TT78 (`HDon/DLHDon/TTChung`, `NBan`, `NMua`, `DSHHDVu`, `TToan`) — tất định, không LLM. Dùng `defusedxml` chống XXE (thêm dependency nếu chưa có).
- PDF có text: `to_markdown` → LLM bóc trường ra JSON (dùng `llm_json.py`) → regex làm sàn (MST, số tiền) giống cách Legal đọc điều khoản. Kiểm tra số học: trước thuế + VAT = tổng; lệch thì gắn EXCEPTION. PDF không có text → báo "chưa hỗ trợ hoá đơn scan, hãy gửi file XML".
- `domains/finance/invoice_matching.py`: chặn trùng (unique key + cảnh báo khi trùng số tiền/NCC/ngày), khớp NCC theo MST (NCC mới → cờ), khớp PO theo số PO, sai số cho phép cấu hình; **đối chiếu 2 chiều PO–hoá đơn** (chưa có phiếu nhập kho; 3 chiều để sau). Ghi chú hoá đơn có nội dung kiểu "đổi số tài khoản" → luôn gắn cờ.
- API: `POST /finance/invoices/upload` (XML/PDF, nhiều file), `GET /finance/invoices`, `GET /finance/invoices/{id}`. File nguồn lưu mã hoá + log truy cập như HĐ Legal.
- Tool gateway: `lookup_invoices` (READ_ONLY, lọc theo trạng thái/NCC/kỳ). Upload đi qua API/UI chứ không qua chat (file XML không dán vào chat được).
- Frontend: trang `/finance` tab "Hoá đơn" (upload, danh sách, chi tiết, ngoại lệ); viết lại `InvoiceAuditCard` trong [SpecializedCards.tsx:94](frontend/components/cards/SpecializedCards.tsx#L94) theo dữ liệu thật.

## F2 — Module 2: Đề xuất bút toán nháp

- `domains/finance/journal_proposal.py`: với một hoá đơn MATCHED → tra `fin_posting_rules` trước (độ tin cậy cao); không có luật thì LLM chọn TK **chỉ từ danh sách TK của tenant** (dùng `llm_json.py`, giữ đúng tinh thần "để LLM quyết, đừng hardcode"), còn **số tiền lấy từ hoá đơn, LLM không đặt số**. Kiểm tra Nợ = Có, TK tồn tại. NCC mới / số tiền lệch mạnh so với lịch sử / LLM chọn → `confidence` thấp, ghi rõ lý do trong payload.
- Tool `propose_journal_entry(invoice_id)` — WRITE, `terminal=True`, `opens_approval=True`, quyền `finance.journal.draft`; tạo `WorkflowApproval` action_type `FINANCE_JOURNAL_APPROVAL` với ngưỡng ở F0.6. Idempotent theo hoá đơn (không tạo phiếu duyệt trùng — lỗi đã gặp ở Legal).
- Tác dụng khi duyệt trong [approvals.py](backend/app/api/v1/approvals.py) (cạnh nhánh `LEGAL_DOCUMENT_APPROVAL`): POSTED → đổ vào `fin_ledger_lines`, hoá đơn → POSTED; APPROVE nguyên trạng → upsert `fin_posting_rules`; EDIT_AND_APPROVE → lưu bản sửa (lịch sử sửa để agent học), không sinh luật.
- `GET /finance/journal-entries` + `GET /finance/export/journal.xlsx` (xuất để import vào MISA/Fast).
- Frontend: tab "Bút toán nháp"; Trung tâm phê duyệt hiển thị dòng Nợ/Có + lý do độ tin cậy.

## F3 — Module 9: Hỏi đáp số liệu (Finance Copilot)

- Tool READ_ONLY, tham số cố định, tính bằng SQL (không text-to-SQL tự do — tránh lộ dữ liệu và query sai):
  `get_account_balance(account, period)`, `get_trial_balance(period)`, `get_ledger(account, from, to, party?)`, `budget_vs_actual(department?, period)`, `ar_ap_aging(direction, as_of)`.
- Mỗi kết quả trả kèm `source` (TK, kỳ, số dòng sổ, batch import) để trích nguồn.
- Phân quyền dữ liệu: `budget_vs_actual` với người chỉ có `finance.budget.view_own` bị ép về phòng của chính họ ở server, bất kể LLM truyền gì (giống `purpose` của HR).
- **Chống bịa số**: hiện `_used_non_document_tool` trong [nodes.py:471](apps/ai-service/app/agents/base/nodes.py#L471) cho mọi câu trả lời có gọi tool đi qua mà không soát số. Thêm bước kiểm tra tất định cho policy FINANCE: mọi con số trong câu trả lời phải xuất hiện trong kết quả tool (chuẩn hoá định dạng 1.234.567 / 1,2 tỷ / 1.2tr); có số lạ → không trả câu đó, trả bảng số liệu gốc. Bật bằng một cờ trên `DomainPolicy` (vd `numbers_must_come_from_tools=True`) để agent khác không đổi hành vi.
- `expense_lookup` (chi phí AI) giữ nguyên nhưng bỏ khỏi trần Finance để không lẫn với chi phí doanh nghiệp (vẫn ở CEO).

## F4 — Module 4–5: Công nợ phải thu/trả + nhắc nợ

- `ar_ap_aging` (đã ở F3) tính từ `fin_invoices` − `fin_payments` + số dư đầu kỳ import; nhóm 0–30/31–60/61–90/>90 ngày.
- `payment_schedule(as_of, horizon_days)` READ_ONLY: hoá đơn IN sắp đến hạn, sắp xếp theo hạn.
- `draft_payment_voucher(invoice_ids)` — WRITE, `opens_approval`, ngưỡng tiền F0.6; **chỉ tạo phiếu chi nháp**, không gọi ngân hàng. Duyệt xong → ghi `fin_payments` trạng thái SCHEDULED (người làm lệnh chi thật trên ngân hàng rồi đánh dấu PAID).
- `draft_payment_reminder(party_id, level)` — soạn email nhắc nợ từ mẫu theo cấp + số liệu từ SQL (LLM chỉ viết lời, số chèn bằng code). Mở phê duyệt `FINANCE_REMINDER_SEND`, gửi qua đường `OutboundMessage` đang dùng cho `SUPPORT_EMAIL_SEND` sau khi duyệt. Blueprint cho tự gửi "mức 1" — đợt này vẫn bắt duyệt, mở tự gửi sau khi đo chất lượng.
- Frontend: tab "Công nợ" (aging thu/trả, lịch thanh toán, nút soạn nhắc nợ).

## Thứ tự & cách giao
F0 → F1 → F2 → F3 → F4; mỗi giai đoạn một commit trên nhánh mới `feat/finance-agent` (tách từ main, không đè nhánh `feat/hr-agent-leave-tools`). Sau F0 cập nhật `docs/plans/FINANCE_AGENT_PLAN_2026-10-02.md` (bản này) và cuối đợt viết `docs/reports/FINANCE_AGENT_FLOW_REPORT_<ngày>.md` như HR/Legal.

## Verification
- Dùng `backend/.venv` có sẵn, **không tạo/xoá venv**. Baseline trước khi sửa: chạy full `pytest` backend + ai-service, ghi số pass/fail để so.
- Test mới (backend/tests): parse XML mẫu thật (vài file NĐ123 khác nhà cung cấp hoá đơn), chặn trùng, NCC mới, lệch PO; import Excel lỗi dòng + Nợ≠Có; bút toán cân, TK ngoài danh mục bị từ chối; ma trận ngưỡng duyệt (người gửi không duyệt, 19/20/500/501 tr); luật hạch toán chỉ sinh khi duyệt nguyên trạng; `budget.view_own` không đọc được phòng khác dù LLM truyền phòng khác; kiểm tra số lạ bị chặn; `test_gateway_grant_names_exist_in_registry` còn xanh; sửa `test_under_development_agents.py` cho FINANCE.
- Live test giống Legal (backend 8001 / ai-service 8101, `AGENT_ENGINES=FINANCE=langgraph`): seed tenant test bằng file Excel mẫu → upload XML → hỏi "hoá đơn này hạch toán thế nào" → duyệt ở Trung tâm phê duyệt bằng tài khoản khác → hỏi "số dư 331 tháng 9" và "công nợ quá hạn > 60 ngày" → đối chiếu số trả lời với SQL trực tiếp. Dọn dữ liệu test + cost log sau khi chạy.
