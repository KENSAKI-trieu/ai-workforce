"use client";

import { FormEvent, useMemo, useState } from "react";
import {
  BadgeCheck,
  CalendarDays,
  ChevronDown,
  ChevronUp,
  ClipboardCheck,
  Download,
  FileText,
  Loader2,
  MessageCircleQuestion,
  PlaneTakeoff,
  Search,
  UserRound,
  UserRoundPlus,
  XCircle,
} from "lucide-react";

import api from "@/lib/api";

export interface ChatAttachment {
  type: string;
  payload?: Record<string, unknown>;
}

interface HRChatToolsProps {
  disabled?: boolean;
  canManageHR: boolean;
  canApprove: boolean;
  canSearchEmployees: boolean;
  onPrompt: (prompt: string) => Promise<void>;
}

function businessDays(startValue: string, endValue: string) {
  const start = new Date(`${startValue}T12:00:00`);
  const end = new Date(`${endValue}T12:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end < start) return 0;
  let total = 0;
  const cursor = new Date(start);
  while (cursor <= end) {
    if (cursor.getDay() !== 0 && cursor.getDay() !== 6) total += 1;
    cursor.setDate(cursor.getDate() + 1);
  }
  return total;
}

export function HRChatTools({ disabled, canManageHR, canApprove, canSearchEmployees, onPrompt }: HRChatToolsProps) {
  const [form, setForm] = useState<"leave" | "onboarding" | "employee" | null>(null);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [reason, setReason] = useState("");
  const [employeeEmail, setEmployeeEmail] = useState("");
  const [employeeQuery, setEmployeeQuery] = useState("");
  const days = useMemo(() => businessDays(startDate, endDate), [startDate, endDate]);

  const submitLeave = async (event: FormEvent) => {
    event.preventDefault();
    if (!startDate || !endDate || !reason.trim() || days < 1) return;
    setForm(null);
    await onPrompt(
      `Tôi muốn xin nghỉ phép ${days} ngày từ ${startDate} đến ${endDate} vì ${reason.trim()}`,
    );
  };

  const submitOnboarding = async (event: FormEvent) => {
    event.preventDefault();
    if (!employeeEmail.trim()) return;
    setForm(null);
    await onPrompt(`Tạo onboarding cho ${employeeEmail.trim()}`);
  };

  const submitEmployeeSearch = async (event: FormEvent) => {
    event.preventDefault();
    if (!employeeQuery.trim()) return;
    setForm(null);
    await onPrompt(`Tìm nhân viên ${employeeQuery.trim()}`);
  };

  return (
    <div className="hr-chat-tools" aria-label="Nghiệp vụ AI HR">
      <div className="hr-chat-actions">
        <button type="button" disabled={disabled} onClick={() => void onPrompt("Hồ sơ của tôi")}>
          <UserRound size={15} /> Hồ sơ
        </button>
        {canSearchEmployees && (
          <button type="button" disabled={disabled} onClick={() => setForm(form === "employee" ? null : "employee")}>
            <Search size={15} /> Tìm nhân viên {form === "employee" ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        )}
        <button type="button" disabled={disabled} onClick={() => void onPrompt("Tôi còn bao nhiêu ngày phép?")}>
          <CalendarDays size={15} /> Ngày phép
        </button>
        <button type="button" disabled={disabled} onClick={() => setForm(form === "leave" ? null : "leave")}>
          <PlaneTakeoff size={15} /> Xin nghỉ {form === "leave" ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
        <button type="button" disabled={disabled} onClick={() => void onPrompt(canSearchEmployees ? "Hợp đồng sắp hết hạn" : "Hợp đồng của tôi")}>
          <FileText size={15} /> Hợp đồng
        </button>
        <button type="button" disabled={disabled} onClick={() => void onPrompt("Chính sách nghỉ phép hiện hành là gì?")}>
          <MessageCircleQuestion size={15} /> Hỏi chính sách
        </button>
        {canManageHR && (
          <button type="button" disabled={disabled} onClick={() => setForm(form === "onboarding" ? null : "onboarding")}>
            <UserRoundPlus size={15} /> Onboarding {form === "onboarding" ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        )}
        {canApprove && (
          <button type="button" disabled={disabled} onClick={() => void onPrompt("Đơn chờ duyệt")}>
            <ClipboardCheck size={15} /> Chờ duyệt
          </button>
        )}
      </div>

      {form === "leave" && (
        <form className="hr-chat-inline-form" onSubmit={submitLeave}>
          <label>
            Từ ngày
            <input type="date" required value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          </label>
          <label>
            Đến ngày
            <input type="date" required min={startDate} value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </label>
          <label className="wide">
            Lý do
            <input required value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Ví dụ: việc gia đình" />
          </label>
          <div className="hr-chat-inline-submit">
            <span>{days > 0 ? `${days} ngày làm việc` : "Chọn khoảng ngày hợp lệ"}</span>
            <button type="submit" disabled={disabled || days < 1 || !reason.trim()}>Gửi AI HR</button>
          </div>
        </form>
      )}

      {form === "employee" && canSearchEmployees && (
        <form className="hr-chat-inline-form onboarding" onSubmit={submitEmployeeSearch}>
          <label className="wide">
            Tên hoặc email nhân viên
            <input required value={employeeQuery} onChange={(event) => setEmployeeQuery(event.target.value)} placeholder="Ví dụ: Nguyễn Văn An hoặc an@company.com" />
          </label>
          <div className="hr-chat-inline-submit">
            <span>Kết quả tự động giới hạn theo cấp bậc và cây quản lý của bạn.</span>
            <button type="submit" disabled={disabled || !employeeQuery.trim()}>Tìm bằng AI HR</button>
          </div>
        </form>
      )}

      {form === "onboarding" && canManageHR && (
        <form className="hr-chat-inline-form onboarding" onSubmit={submitOnboarding}>
          <label className="wide">
            Email nhân viên
            <input type="email" required value={employeeEmail} onChange={(event) => setEmployeeEmail(event.target.value)} placeholder="new.hire@company.com" />
          </label>
          <div className="hr-chat-inline-submit">
            <span>AI sẽ tạo checklist và task liên phòng ban.</span>
            <button type="submit" disabled={disabled || !employeeEmail.trim()}>Tạo bằng AI HR</button>
          </div>
        </form>
      )}
    </div>
  );
}

type AnyRecord = Record<string, unknown>;

function display(value: unknown, fallback = "—") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function formatDate(value: unknown) {
  if (!value) return "Không thời hạn";
  const parsed = new Date(String(value));
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleDateString("vi-VN");
}

const LEAVE_STATUS_LABEL: Record<string, string> = {
  WAITING: "Chờ duyệt",
  APPROVED: "Đã duyệt",
  REJECTED: "Bị từ chối",
  CANCELLED: "Đã rút",
};

const LEAVE_STATUS_CLASS: Record<string, string> = {
  WAITING: "waiting",
  APPROVED: "approved",
  REJECTED: "rejected",
  CANCELLED: "rejected",
};

function ApprovalList({ items }: { items: AnyRecord[] }) {
  const [rows, setRows] = useState(items);
  const [acting, setActing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const decide = async (id: string, action: "APPROVE" | "REJECT") => {
    setActing(id);
    setError(null);
    try {
      const { data } = await api.post(`/api/v1/approvals/${id}/action`, { action });
      setRows((current) => current.map((row) => (
        String(row.id) === id ? { ...row, status: data.status } : row
      )));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể xử lý phê duyệt.");
    } finally {
      setActing(null);
    }
  };

  if (rows.length === 0) return <p className="hr-card-empty">Không có yêu cầu đang chờ xử lý.</p>;
  return (
    <div className="hr-card-list">
      {rows.map((row) => {
        const payload = (row.payload || {}) as AnyRecord;
        const waiting = row.status === "WAITING";
        return (
          <div className="hr-approval-row" key={String(row.id)}>
            <div>
              <strong>{display(row.workflow_title, display(row.action_type, "Yêu cầu phê duyệt"))}</strong>
              <span>{display(payload.requester_name, "Nhân viên")} · {display(payload.requested_days, "?")} ngày</span>
              <small>{display(payload.reason, "Không có ghi chú")}</small>
            </div>
            {waiting ? (
              <div className="hr-approval-actions">
                <button type="button" className="reject" disabled={acting === row.id} onClick={() => void decide(String(row.id), "REJECT")}>
                  <XCircle size={14} /> Từ chối
                </button>
                <button type="button" className="approve" disabled={acting === row.id} onClick={() => void decide(String(row.id), "APPROVE")}>
                  {acting === row.id ? <Loader2 className="spin" size={14} /> : <BadgeCheck size={14} />} Duyệt
                </button>
              </div>
            ) : <span className={`hr-card-status ${String(row.status).toLowerCase()}`}>{display(row.status)}</span>}
          </div>
        );
      })}
      {error && <p className="hr-card-error">{error}</p>}
    </div>
  );
}

function HRExportCard({ payload }: { payload: AnyRecord }) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const download = async () => {
    const downloadUrl = String(payload.download_url || "");
    if (!downloadUrl.startsWith("/api/v1/hr/employees/export?")) {
      setError("Liên kết tải file không hợp lệ.");
      return;
    }
    setDownloading(true);
    setError(null);
    try {
      const response = await api.get<Blob>(downloadUrl, { responseType: "blob" });
      const disposition = String(response.headers["content-disposition"] || "");
      const matchedFilename = disposition.match(/filename="?([^";]+)"?/i)?.[1];
      const extension = String(payload.format || "xlsx");
      const filename = matchedFilename || `hr-directory.${extension}`;
      const objectUrl = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể tải file.");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <section className="hr-message-card hr-export-card">
      <header>
        <FileText size={17} />
        <strong>File {display(payload.format_label)}</strong>
        <span>{display(payload.scope)}</span>
      </header>
      <div className="hr-export-card-body">
        <div>
          <strong>{display(payload.directory_label, "Danh sách nhân sự")}</strong>
          <span>Dữ liệu BASIC sẽ được kiểm tra lại theo quyền của bạn khi tải.</span>
        </div>
        <button type="button" disabled={downloading} onClick={() => void download()}>
          {downloading ? <Loader2 className="spin" size={15} /> : <Download size={15} />}
          {downloading ? "Đang tạo file…" : "Tải file"}
        </button>
      </div>
      {error && <p className="hr-card-error">{error}</p>}
    </section>
  );
}

export function HRMessageCard({ attachment }: { attachment: ChatAttachment }) {
  const payload = (attachment.payload || {}) as AnyRecord;
  if (attachment.type === "APPROVAL_CARD") {
    return (
      <section className="hr-message-card approval">
        <header><ClipboardCheck size={17} /><strong>Đơn nghỉ đã gửi duyệt</strong><span className="hr-card-status waiting">Đang chờ</span></header>
        <p>{display(payload.details, "Yêu cầu đang được chuyển tới người có thẩm quyền.")}</p>
      </section>
    );
  }
  if (attachment.type !== "HR_CARD") return null;

  const type = String(payload.type || "");
  if (type === "FILE_EXPORT") {
    return <HRExportCard payload={payload} />;
  }
  if (type === "EMPLOYEE_PROFILE") {
    const employee = (payload.employee || {}) as AnyRecord;
    const balance = (payload.leave_balance || {}) as AnyRecord;
    const privateProfile = (payload.private || {}) as AnyRecord;
    const compensation = (payload.compensation || {}) as AnyRecord;
    const access = (payload.access || {}) as AnyRecord;
    const allowedSections = (access.allowed_sections || []) as string[];
    const maskedFields = (access.masked_fields || []) as string[];
    return (
      <section className="hr-message-card">
        <header><UserRound size={17} /><strong>Hồ sơ của tôi</strong><span className="hr-card-status approved">Đã xác thực</span></header>
        <div className="hr-profile-grid">
          <span><small>Họ tên</small><strong>{display(employee.name)}</strong></span>
          <span><small>Email</small><strong>{display(employee.email)}</strong></span>
          <span><small>Vai trò</small><strong>{display(employee.role)}</strong></span>
          <span><small>Chức danh</small><strong>{display(employee.job_title, "Chưa cập nhật")}</strong></span>
          <span><small>Phòng ban</small><strong>{display(employee.department)}</strong></span>
          <span><small>Quản lý</small><strong>{display(employee.manager_name, "Chưa thiết lập")}</strong></span>
          <span><small>Trạng thái</small><strong>{display(employee.employment_status)}</strong></span>
          {Object.keys(balance).length > 0 && (
            <span><small>Phép còn lại</small><strong>{display(balance.remaining_days, "0")} ngày</strong></span>
          )}
          {Object.keys(privateProfile).length > 0 && (
            <span><small>Điện thoại</small><strong>{display(privateProfile.phone, "Chưa cập nhật")}</strong></span>
          )}
          {Object.keys(compensation).length > 0 && (
            <span>
              <small>Lương tháng</small>
              <strong>{compensation.monthly_salary == null ? "Chưa cập nhật" : `${Number(compensation.monthly_salary).toLocaleString("vi-VN")} ${display(compensation.salary_currency, "VND")}`}</strong>
            </span>
          )}
        </div>
        {allowedSections.length > 0 && (
          <p>Dữ liệu được cấp: {allowedSections.join(", ")} · Mục đích: {display(access.purpose)}</p>
        )}
        {maskedFields.length > 0 && <p>Đã che {maskedFields.length} trường nhạy cảm trước khi gửi cho AI.</p>}
      </section>
    );
  }
  if (type === "EMPLOYEE_SEARCH") {
    const items = (payload.items || []) as AnyRecord[];
    const isManagerDirectory = payload.directory_type === "MANAGERS";
    const totalCount = Number(payload.total_count ?? items.length);
    return (
      <section className="hr-message-card">
        <header>
          <Search size={17} />
          <strong>{isManagerDirectory ? "Danh sách quản lý" : "Kết quả tìm nhân viên"}</strong>
          <span>{totalCount} {isManagerDirectory ? "quản lý" : "hồ sơ"} · {display(payload.scope)}</span>
        </header>
        <div className="hr-card-list">
          {items.length === 0 && <p className="hr-card-empty">Không có hồ sơ trong phạm vi được phép.</p>}
          {items.map((item) => {
            const employee = (item.employee || {}) as AnyRecord;
            const balance = (item.leave_balance || {}) as AnyRecord;
            return (
              <div className="hr-employee-row" key={String(employee.id)}>
                <div>
                  <strong>{display(employee.name)}</strong>
                  <span>{display(employee.email)}</span>
                </div>
                <div><small>Cấp bậc</small><strong>{display(employee.role)}</strong></div>
                <div><small>Phòng ban</small><strong>{display(employee.department)}</strong></div>
                <div><small>Quản lý</small><strong>{display(employee.manager_name, "—")}</strong></div>
                <div><small>Phép còn</small><strong>{balance.remaining_days == null ? "—" : `${display(balance.remaining_days)} ngày`}</strong></div>
              </div>
            );
          })}
        </div>
      </section>
    );
  }
  if (type === "LEAVE_REQUEST_DRAFT") {
    const missingFields = (payload.missing_fields || []) as string[];
    const cancelled = payload.status === "CANCELLED";
    const missingLabels: Record<string, string> = {
      start_date: "ngày bắt đầu",
      end_date: "ngày kết thúc",
      reason: "lý do",
    };
    return (
      <section className="hr-message-card">
        <header>
          <PlaneTakeoff size={17} />
          <strong>Bản nháp đơn nghỉ phép</strong>
          <span className={`hr-card-status ${cancelled ? "rejected" : "waiting"}`}>
            {cancelled ? "Đã hủy" : "Chưa gửi"}
          </span>
        </header>
        <div className="hr-profile-grid">
          <span><small>Ngày bắt đầu</small><strong>{payload.start_date ? formatDate(payload.start_date) : "Cần bổ sung"}</strong></span>
          <span><small>Ngày kết thúc</small><strong>{payload.end_date ? formatDate(payload.end_date) : "Cần bổ sung"}</strong></span>
          <span className="hr-profile-reason"><small>Lý do</small><strong>{display(payload.reason, "Cần bổ sung")}</strong></span>
        </div>
        {!cancelled && missingFields.length > 0 && (
          <p>Còn thiếu: {missingFields.map((field) => missingLabels[field] || field).join(", ")}.</p>
        )}
        {Boolean(payload.validation_error) && <p className="hr-card-empty">{display(payload.validation_error)}</p>}
      </section>
    );
  }
  if (type === "LEAVE_BALANCE") {
    const balance = (payload.balance || {}) as AnyRecord;
    return (
      <section className="hr-message-card">
        <header><CalendarDays size={17} /><strong>Quỹ phép năm {display(balance.year, "")}</strong></header>
        <div className="hr-balance-row">
          <span><small>Tổng</small><strong>{display(balance.total_days, "0")}</strong></span>
          <span><small>Đã dùng</small><strong>{display(balance.used_days, "0")}</strong></span>
          <span className="highlight"><small>Còn lại</small><strong>{display(balance.remaining_days, "0")}</strong></span>
        </div>
      </section>
    );
  }
  if (type === "CONTRACTS") {
    const items = (payload.items || []) as AnyRecord[];
    return (
      <section className="hr-message-card">
        <header><FileText size={17} /><strong>Hợp đồng & thử việc</strong><span>{items.length} hồ sơ</span></header>
        <div className="hr-card-list">
          {items.length === 0 && <p className="hr-card-empty">Chưa có hợp đồng đang hiệu lực.</p>}
          {items.map((item) => {
            const employee = (item.employee || {}) as AnyRecord;
            const employeeName = item.employee_name || employee.name;
            return (
              <div className="hr-contract-row" key={String(item.id)}>
                <div><strong>{display(employeeName)}</strong><span>{display(item.contract_type)}</span></div>
                <div><small>Hết hạn</small><strong>{formatDate(item.end_date)}</strong></div>
                <div><small>Thử việc đến</small><strong>{formatDate(item.probation_end_date)}</strong></div>
              </div>
            );
          })}
        </div>
      </section>
    );
  }
  if (type === "ONBOARDING") {
    return (
      <section className="hr-message-card">
        <header><UserRoundPlus size={17} /><strong>Onboarding đã khởi tạo</strong><span className="hr-card-status waiting">{display(payload.status)}</span></header>
        <div className="hr-onboarding-summary">
          <strong>{display(payload.employee_name)}</strong>
          <span>{display(payload.task_count, "0")} task · Bắt đầu {formatDate(payload.start_date)}</span>
        </div>
      </section>
    );
  }
  if (type === "LEAVE_CALENDAR" || type === "LEAVE_REQUESTS") {
    const items = (payload.items || []) as AnyRecord[];
    const isCalendar = type === "LEAVE_CALENDAR";
    const showNames = isCalendar || payload.whose === "TEAM";
    const period = payload.start_date === payload.end_date
      ? formatDate(payload.start_date)
      : `${formatDate(payload.start_date)} – ${formatDate(payload.end_date)}`;
    return (
      <section className="hr-message-card">
        <header>
          <CalendarDays size={17} />
          <strong>{isCalendar ? `Lịch nghỉ ${period}` : showNames ? "Đơn nghỉ của nhân viên" : "Đơn nghỉ của tôi"}</strong>
          <span>
            {isCalendar ? `${display(payload.on_leave_count, "0")} người nghỉ` : `${display(payload.total_count, "0")} đơn`} · {display(payload.scope)}
          </span>
        </header>
        <div className="hr-card-list">
          {items.length === 0 && <p className="hr-card-empty">Không có đơn nghỉ trong phạm vi được phép.</p>}
          {items.map((item) => {
            const employee = (item.employee || {}) as AnyRecord;
            const status = String(item.status || "");
            return (
              <div className="hr-contract-row" key={String(item.id)}>
                <div>
                  <strong>{showNames ? display(employee.name) : `${formatDate(item.start_date)} – ${formatDate(item.end_date)}`}</strong>
                  <span>{showNames ? `${formatDate(item.start_date)} – ${formatDate(item.end_date)}` : display(item.reason, "")}</span>
                </div>
                <div><small>Số ngày</small><strong>{display(item.requested_days, "?")}</strong></div>
                <div>
                  <small>Trạng thái</small>
                  <span className={`hr-card-status ${LEAVE_STATUS_CLASS[status] || "waiting"}`}>{LEAVE_STATUS_LABEL[status] || status}</span>
                </div>
              </div>
            );
          })}
        </div>
      </section>
    );
  }
  if (type === "PENDING_APPROVALS") {
    return (
      <section className="hr-message-card">
        <header><ClipboardCheck size={17} /><strong>Yêu cầu chờ duyệt</strong></header>
        <ApprovalList items={(payload.items || []) as AnyRecord[]} />
      </section>
    );
  }
  return null;
}
