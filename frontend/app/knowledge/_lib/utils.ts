import axios, { type AxiosError } from "axios";

type ErrorRecord = Record<string, unknown>;

const statusMessages: Record<number, string> = {
  400: "Yêu cầu không hợp lệ.",
  401: "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.",
  403: "Bạn không có quyền thực hiện thao tác này.",
  404: "Không tìm thấy dữ liệu yêu cầu.",
  409: "Dữ liệu đang xung đột với bản hiện có.",
  413: "Tệp tải lên vượt quá dung lượng cho phép.",
  422: "Dữ liệu gửi lên chưa hợp lệ.",
  429: "Có quá nhiều yêu cầu. Vui lòng thử lại sau.",
  500: "Máy chủ gặp lỗi khi xử lý yêu cầu.",
  502: "Dịch vụ AI đang gặp lỗi.",
  503: "Dịch vụ hiện chưa sẵn sàng.",
  504: "Dịch vụ xử lý quá thời gian.",
};

function isRecord(value: unknown): value is ErrorRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function concise(value: unknown, maxLength = 220): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.replace(/\s+/g, " ").trim();
  if (!normalized) return null;
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 1)}…` : normalized;
}

function validationMessage(detail: unknown): string | null {
  if (!Array.isArray(detail)) return null;
  const messages = detail.slice(0, 2).map((item) => {
    if (!isRecord(item)) return null;
    const location = Array.isArray(item.loc)
      ? item.loc.filter((part) => part !== "body").map(String).join(".")
      : "";
    const message = concise(item.msg);
    if (!message) return null;
    return location ? `${location}: ${message}` : message;
  }).filter(Boolean);
  return messages.length ? `Dữ liệu chưa hợp lệ: ${messages.join("; ")}.` : null;
}

function backendMessage(data: unknown): { message: string | null; code: string | null } {
  if (!isRecord(data)) return { message: concise(data), code: null };
  const detail = data.detail;
  const validation = validationMessage(detail);
  if (validation) return { message: validation, code: concise(data.code, 60) };
  if (typeof detail === "string") {
    return { message: concise(detail), code: concise(data.code, 60) };
  }
  if (isRecord(detail)) {
    return {
      message: concise(detail.message) || concise(detail.error) || concise(detail.detail),
      code: concise(detail.code, 60) || concise(data.code, 60),
    };
  }
  return {
    message: concise(data.message) || concise(data.error),
    code: concise(data.code, 60),
  };
}

function requestLabel(error: AxiosError): string | null {
  const method = error.config?.method?.toUpperCase();
  const rawUrl = error.config?.url?.split("?")[0];
  const path = rawUrl?.replace(/^https?:\/\/[^/]+/i, "");
  if (!method && !path) return null;
  return [method, path].filter(Boolean).join(" ");
}

function debugLabel(parts: Array<string | number | null | undefined>): string {
  return parts.filter((part) => part !== null && part !== undefined && part !== "").join(" · ");
}

function logApiError(error: AxiosError): void {
  console.error("[Knowledge API error]", {
    code: error.code,
    method: error.config?.method?.toUpperCase(),
    url: error.config?.url,
    status: error.response?.status,
    response: error.response?.data,
  });
}

export function messageFrom(error: unknown): string {
  if (!axios.isAxiosError(error)) {
    const detail = error instanceof Error ? concise(error.message, 120) : null;
    return `Không thể xử lý yêu cầu. [${debugLabel(["CLIENT", detail])}]`;
  }

  logApiError(error);
  const request = requestLabel(error);
  if (!error.response) {
    if (error.code === "ERR_CANCELED") {
      return `Yêu cầu đã bị hủy. [${debugLabel(["CANCELED", request])}]`;
    }
    if (error.code === "ECONNABORTED" || error.code === "ETIMEDOUT") {
      return `Máy chủ phản hồi quá lâu. [${debugLabel(["TIMEOUT", request])}]`;
    }
    return `Không thể kết nối backend. Kiểm tra server và CORS. [${debugLabel([error.code || "NETWORK", request])}]`;
  }

  const status = error.response.status;
  const parsed = backendMessage(error.response.data);
  const message = parsed.message || statusMessages[status] || "Không thể xử lý yêu cầu.";
  return `${message} [${debugLabel([`HTTP ${status}`, parsed.code, request])}]`;
}

export function processingMessage(message?: string | null): string {
  const normalized = concise(message);
  if (!normalized) return "Không thể xử lý tài liệu. [PROCESSING_FAILED]";
  const aiServicePath = normalized.match(/^AI service request failed:\s*(.+)$/i)?.[1];
  if (aiServicePath) {
    return `Dịch vụ AI gặp lỗi tại ${aiServicePath}. [PROCESSING_FAILED]`;
  }
  return `${normalized} [PROCESSING_FAILED]`;
}

export function formatFileSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(value?: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("vi-VN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
