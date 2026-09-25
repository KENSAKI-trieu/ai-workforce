# AI Workforce — Hướng dẫn sử dụng

> Ngôn ngữ: **Tiếng Việt** · [日本語版](USER_MANUAL_JA.md)  
> Cập nhật: 27/08/2026

## 1. Giới thiệu

AI Workforce là nền tảng quản lý công việc doanh nghiệp kết hợp nhiều trợ lý AI chuyên trách. Hệ thống hỗ trợ điều phối công việc, hỏi đáp tài liệu nội bộ, quản lý task, phê duyệt có người kiểm soát, theo dõi chi phí AI và nhật ký kiểm toán.

Tài liệu này dành cho người dùng cuối, quản lý và quản trị viên. Hướng dẫn cài đặt dành cho kỹ thuật nằm trong [README của dự án](../../README.md).

## 2. Bắt đầu nhanh

### 2.1 Truy cập hệ thống

1. Mở địa chỉ do quản trị viên cung cấp. Khi chạy cục bộ, địa chỉ mặc định là <http://localhost:3000>.
2. Chọn **Đăng nhập**, nhập email và mật khẩu, rồi bấm **Đăng nhập**.
3. Sau khi đăng nhập thành công, hệ thống chuyển đến **Bảng điều khiển CEO**.

Nếu tổ chức chưa tồn tại, chọn **Tạo tổ chức mới** tại màn hình đăng ký. Tài khoản tạo tổ chức sẽ trở thành `Owner`. Trong môi trường doanh nghiệp, nên để quản trị viên tạo tổ chức và cấp tài khoản cho nhân viên.

### 2.2 Chọn ngôn ngữ

Ở đầu thanh bên trái, chọn:

- `VI` để dùng tiếng Việt.
- `JA` để dùng tiếng Nhật.

Lựa chọn được lưu trên trình duyệt hiện tại. Một số nội dung nghiệp vụ hoặc dữ liệu đã nhập có thể vẫn hiển thị bằng ngôn ngữ gốc.

### 2.3 Đăng xuất

Chọn **Đăng xuất** ở cuối thanh bên. Luôn đăng xuất khi dùng máy tính dùng chung.

## 3. Vai trò và quyền truy cập

Quyền thực tế còn phụ thuộc phòng ban, công cụ được cấp cho từng AI Agent và chính sách của tổ chức.

| Vai trò | Phạm vi sử dụng điển hình |
| --- | --- |
| `Owner` | Toàn quyền workspace; quản lý thành viên, cài đặt, agents và vòng đời dữ liệu. |
| `Admin` | Quản trị người dùng, agents, integrations và phần lớn cấu hình vận hành. |
| `CEO` | Theo dõi toàn hệ thống, điều phối agents, phê duyệt và cấu hình agents. |
| `Manager` | Quản lý công việc và dữ liệu trong phạm vi được cấp. |
| `Employee` | Sử dụng agents và các chức năng nghiệp vụ được cấp. |
| `Guest` | Quyền hạn chế theo cấu hình của workspace. |

Các mục không đủ quyền có thể ở chế độ chỉ xem, bị ẩn hoặc trả về thông báo từ chối truy cập. Không chia sẻ tài khoản để tránh làm sai lệch lịch sử kiểm toán.

## 4. Các khu vực chính

| Khu vực | Đường dẫn | Công dụng |
| --- | --- | --- |
| Bảng điều khiển | `/dashboard` | Tổng quan hoạt động, hiệu suất và trạng thái agents. |
| Phân tích quản trị | `/analytics` | Theo dõi số liệu và xu hướng vận hành. |
| Chi phí AI | `/costs` | Xem chi phí theo agent, nhân viên, phòng ban và workflow; quản lý ngân sách. |
| Kho tri thức | `/knowledge` | Tải lên, xử lý, kiểm tra và quản lý tài liệu dùng cho RAG. |
| Task | `/tasks` | Quản lý công việc bằng bảng Kanban và danh sách. |
| Lịch | `/calendar` | Theo dõi và tạo lịch công việc. |
| Phê duyệt | `/approvals` | Xem xét các hành động cần con người phê duyệt. |
| Nhân viên & phân quyền | `/users-mgmt` | Tạo tài khoản, gán vai trò, phòng ban và trạng thái hoạt động. |
| Nhật ký kiểm toán | `/audit-logs` | Tra cứu hành động và thay đổi quan trọng. |
| Tích hợp doanh nghiệp | `/integrations` | Quản lý kết nối dịch vụ ngoài và phạm vi agent được phép dùng. |
| Cấu hình AI Employees | `/ai-editor` | Bật/tắt và cấu hình prompt, model, công cụ, tri thức của agent. |
| Cài đặt công ty | `/settings` | Quản lý workspace, bảo mật, model, dữ liệu và xuất dữ liệu. |

## 5. Làm việc với AI Agents

### 5.1 Chọn đúng agent

Mở nhóm **Trợ lý AI** trên thanh bên và chọn agent phù hợp:

| Agent | Nên dùng khi |
| --- | --- |
| CEO | Yêu cầu phức tạp cần lập kế hoạch hoặc phối hợp nhiều phòng ban. |
| HR | Hỏi chính sách nhân sự, số ngày phép, nghỉ phép, onboarding và hồ sơ nhân viên. |
| Legal | Rà soát hợp đồng, so sánh phiên bản, kiểm tra dữ liệu nhạy cảm và tạo bản dự thảo pháp lý. |
| IT | Tra cứu hướng dẫn kỹ thuật và tạo yêu cầu hỗ trợ. |
| Finance | Đối soát hóa đơn/PO và xử lý nghiệp vụ tài chính được cấp quyền. |
| Sales | Tra cứu thông tin bán hàng và tạo báo giá. |
| Knowledge | Hỏi đáp tài liệu nội bộ kèm nguồn trích dẫn. |

Chấm xanh cạnh agent biểu thị đang hoạt động; trạng thái khác có thể cho biết agent tạm không sẵn sàng.

### 5.2 Gửi yêu cầu hiệu quả

1. Nêu rõ mục tiêu, phạm vi và thời hạn.
2. Cung cấp mã nhân viên, mã hợp đồng, PO hoặc tài liệu liên quan nếu được phép.
3. Nêu định dạng đầu ra mong muốn, ví dụ: “tóm tắt 5 ý”, “lập bảng”, “tạo task trước thứ Sáu”.
4. Kiểm tra câu trả lời, nguồn trích dẫn và mọi thẻ hành động trước khi tiếp tục.

Ví dụ:

```text
Kiểm tra số ngày phép còn lại của tôi và tạo yêu cầu nghỉ ngày 5–6/9,
lý do việc gia đình. Chỉ gửi yêu cầu sau khi tôi xác nhận.
```

```text
Tìm chính sách công tác phí mới nhất, tóm tắt hạn mức khách sạn và đi lại,
đồng thời dẫn nguồn tài liệu và phần liên quan.
```

### 5.3 Hành động cần phê duyệt

Các hành động ghi dữ liệu, gửi ra hệ thống ngoài hoặc có rủi ro có thể tạo yêu cầu phê duyệt. Khi đó:

1. Đọc nội dung, người yêu cầu, mức rủi ro và dữ liệu đầu vào.
2. Mở bản xem trước hoặc tài liệu đính kèm nếu có.
3. Nhập nhận xét khi cần.
4. Chọn **Phê duyệt** hoặc **Từ chối**.

Không phê duyệt chỉ dựa trên phần tóm tắt của AI. Người phê duyệt chịu trách nhiệm kiểm tra nội dung cuối cùng.

## 6. Quản lý kho tri thức

### 6.1 Tải tài liệu lên

1. Mở **Kho Tri thức (RAG)** → **Tạo mới**.
2. Chọn tệp. Định dạng được hỗ trợ trên giao diện: `.pdf`, `.docx`, `.txt`, `.md`, `.csv`.
3. Nhập tên bộ sưu tập và chọn **Phạm vi truy cập** theo phòng ban.
4. Chọn chiến lược chia đoạn:
   - **Từng đoạn**: phù hợp tài liệu đơn giản, các đoạn tương đối độc lập.
   - **Cha – con**: phù hợp tài liệu dài cần giữ ngữ cảnh rộng hơn.
5. Điều chỉnh kích thước chunk và overlap nếu cần, rồi chọn **Xem trước chunk**.
6. Kiểm tra bản xem trước và tiếp tục lập chỉ mục.
7. Chờ quy trình `tải lên → đọc nội dung → chia chunk → embedding → lập chỉ mục` hoàn tất.

Khi hệ thống phát hiện nội dung trùng, chọn **Giữ bản cũ** hoặc **Thay thế và xử lý** sau khi xác minh phiên bản tài liệu.

### 6.2 Kiểm tra tài liệu

Từ danh sách tài liệu, mở một tài liệu để xem trạng thái xử lý, cấu hình chunk và các đoạn đã lập chỉ mục. Dùng ô tìm kiếm để kiểm tra nội dung cụ thể. Chỉ dùng tài liệu có trạng thái **Hoàn tất/Ready** cho kết quả RAG ổn định.

### 6.3 Quy tắc dữ liệu

- Chọn đúng phòng ban; phạm vi quá rộng có thể làm lộ tài liệu nội bộ.
- Không tải mật khẩu, API key, secret hoặc dữ liệu cá nhân không cần thiết.
- Kiểm tra bản quyền và quyền sử dụng trước khi tải tài liệu.
- Việc xóa tài liệu sẽ xóa cả các chunk liên quan và có thể không khôi phục được.

## 7. Quản lý task

1. Mở **Quản lý Task (Kanban)**.
2. Chọn tạo task và nhập tiêu đề, mô tả, độ ưu tiên, hạn hoàn thành.
3. Gán cho nhân viên hoặc AI Agent phù hợp.
4. Theo dõi task qua các trạng thái như `DRAFT`, `PENDING`, `RUNNING`, `WAITING_APPROVAL`, `COMPLETED`.
5. Kéo thả thẻ hoặc chọn trạng thái trong danh sách. Hệ thống chỉ cho phép các bước chuyển hợp lệ.
6. Mở chi tiết task để xem lịch sử và thêm bình luận.

Chỉ task ở trạng thái `DRAFT` hoặc `COMPLETED` mới có thể bị xóa trực tiếp. Với task đang xử lý, hãy chuyển sang `CANCELLED` nếu quy trình cho phép.

## 8. Lịch, thông báo và workflow

- **Lịch Công việc:** tạo và xem sự kiện, hạn task hoặc sự kiện nhân sự trong phạm vi quyền hạn.
- **Thông báo:** chọn biểu tượng chuông để xem nhanh; mở `/notifications` để xem đầy đủ và đánh dấu đã đọc.
- **Workflows:** theo dõi tiến trình nhiều bước, agent phụ trách và trạng thái chờ phê duyệt. Nếu một bước thất bại, đọc lỗi trước khi thử lại hoặc giao cho người xử lý.

## 9. Trung tâm phê duyệt

1. Mở **Trung tâm Phê duyệt**.
2. Chọn một mục trong danh sách chờ duyệt.
3. Kiểm tra loại hành động, mức rủi ro, người yêu cầu, dữ liệu và tài liệu xem trước.
4. Thêm nhận xét rõ ràng để tạo dấu vết kiểm toán.
5. Phê duyệt khi thông tin đầy đủ; nếu từ chối, nêu lý do và hướng sửa.

Làm mới trang nếu mục vừa xử lý vẫn còn hiển thị. Nếu bạn không thấy một yêu cầu dự kiến, kiểm tra vai trò, phòng ban và trạng thái workflow.

## 10. Chức năng dành cho quản trị viên

### 10.1 Nhân viên và phân quyền

`Owner`, `Admin` và `CEO` có thể:

- Tạo tài khoản nhân viên với mật khẩu ban đầu tối thiểu 8 ký tự.
- Gán vai trò và phòng ban.
- Kích hoạt hoặc vô hiệu hóa tài khoản.
- Tìm kiếm và lọc danh sách người dùng.

Không thể chỉnh sửa một số thuộc tính bảo vệ của tài khoản `Owner`. Cấp quyền tối thiểu cần thiết và không dùng tài khoản quản trị cho công việc thường ngày.

### 10.2 Cấu hình AI Employees

Tại **Cấu hình AI Employees**, người có quyền có thể bật/tắt agent và điều chỉnh system prompt, model, công cụ, hành động và phạm vi tri thức. Sau khi thay đổi:

1. Kiểm tra lại danh sách quyền.
2. Lưu cấu hình.
3. Chạy một yêu cầu thử không phá hủy.
4. Kiểm tra nhật ký kiểm toán.

Không cấp công cụ ghi dữ liệu hoặc tích hợp ngoài nếu agent không thực sự cần.

### 10.3 Tích hợp doanh nghiệp

Khi tạo kết nối, chọn nhà cung cấp, nhập thông tin tham chiếu credential theo quy trình bảo mật và khai báo rõ các `agent roles` được phép. Để trống danh sách agent nghĩa là chưa cấp kết nối cho agent nào.

Credential và API key không được nhập vào chat. Các key hệ thống phải được quản lý bằng biến môi trường hoặc secret vault theo hướng dẫn vận hành.

### 10.4 Chi phí và ngân sách AI

Dùng **Quản lý Chi phí AI** để xem tổng quan và phân rã theo agent, nhân viên, phòng ban hoặc workflow. Quản trị viên có thể thiết lập hạn mức và quy tắc định tuyến model nếu giao diện cho phép. Khi chi phí tăng bất thường, kiểm tra khối lượng yêu cầu, model, số token và workflow lặp.

### 10.5 Cài đặt công ty và dữ liệu

Tại **Cài đặt Công ty**, quản trị viên có thể quản lý tên, logo, múi giờ, ngôn ngữ, model mặc định, thời gian lưu dữ liệu, timeout phiên, domain email và IP allowlist.

- **Export JSON** không bao gồm password hash hoặc tham chiếu credential.
- Yêu cầu xóa workspace cần `Owner`, nhập chính xác domain và lý do. Đây là yêu cầu chờ review; dữ liệu không bị xóa ngay khi gửi.

## 11. Nhật ký kiểm toán

Dùng **Nhật ký Kiểm toán** để truy vết ai đã thực hiện hành động nào, thời điểm, agent liên quan và thay đổi trước/sau nếu có. Khi điều tra sự cố:

1. Lọc theo thời gian gần nhất.
2. Thu hẹp theo người dùng, agent hoặc loại hành động.
3. Đối chiếu với task, workflow hoặc phê duyệt liên quan.
4. Không chỉnh sửa hay chia sẻ log ngoài phạm vi được phép.

## 12. Bảo mật và sử dụng AI có trách nhiệm

- Xem câu trả lời AI là đề xuất cần kiểm tra, không phải quyết định cuối cùng.
- Không nhập mật khẩu, token, API key hoặc dữ liệu ngoài phạm vi công việc.
- Kiểm tra nguồn trích dẫn; thiếu nguồn hoặc nguồn không phù hợp thì yêu cầu agent tìm lại.
- Luôn xem trước hợp đồng, báo giá, email và hành động bên ngoài trước khi phê duyệt.
- Báo cho quản trị viên nếu thấy dữ liệu không thuộc phòng ban hoặc quyền của mình.
- Khóa màn hình và đăng xuất trên thiết bị dùng chung.

## 13. Xử lý sự cố thường gặp

| Hiện tượng | Cách xử lý |
| --- | --- |
| Không đăng nhập được | Kiểm tra email/mật khẩu, trạng thái tài khoản và kết nối mạng; liên hệ quản trị viên nếu tài khoản bị vô hiệu hóa. |
| Trang tải mãi hoặc báo lỗi mạng | Tải lại một lần, kiểm tra backend/AI service với quản trị viên, không gửi lặp hành động ghi dữ liệu. |
| Agent không xuất hiện hoặc không dùng được tool | Kiểm tra agent đang bật, vai trò, phòng ban và cấu hình tool/knowledge access. |
| Câu trả lời không có nguồn | Dùng Knowledge Agent, yêu cầu dẫn nguồn rõ ràng và xác nhận tài liệu đã ở trạng thái `Ready`. |
| Tài liệu xử lý thất bại | Mở chi tiết để xem lỗi, kiểm tra định dạng/kích thước, rồi **Thử lại**; không tải nhiều bản trùng. |
| Không thể đổi trạng thái task | Chọn một bước chuyển được hệ thống cho phép; kiểm tra xem task có đang chờ phê duyệt không. |
| Không thấy yêu cầu phê duyệt | Kiểm tra quyền người duyệt, phòng ban, trạng thái workflow và làm mới danh sách. |
| Ngôn ngữ chưa đổi toàn bộ | Làm mới trang; nội dung người dùng nhập và một số dữ liệu nghiệp vụ không được dịch tự động. |

Khi báo lỗi, gửi cho quản trị viên: thời gian xảy ra, trang đang dùng, thao tác vừa thực hiện, thông báo lỗi và mã task/workflow nếu có. Không gửi secret hoặc ảnh chứa dữ liệu nhạy cảm.

## 14. Quy trình sử dụng khuyến nghị

```text
Chọn đúng agent → mô tả yêu cầu rõ ràng → kiểm tra kết quả và nguồn
→ xem trước hành động → phê duyệt khi cần → theo dõi task/workflow
→ đối chiếu thông báo và audit log
```

