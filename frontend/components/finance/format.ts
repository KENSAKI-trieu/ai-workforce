import axios from "axios";

/** Amounts arrive as exact decimal strings ("1234567.00"); shown the Vietnamese way. */
export function formatVnd(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return String(value);
  return `${amount.toLocaleString("vi-VN", { maximumFractionDigits: 2 })} ₫`;
}

/** The current accounting period, YYYY-MM. */
export function currentPeriod(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

/** A balance as the books write it: "Nợ 1.000 ₫", "Có 1.000 ₫", or both for 131/331 and the like. */
export function formatSide(side: { debit: string; credit: string } | undefined): string {
  if (!side) return "—";
  const parts: string[] = [];
  if (Number(side.debit)) parts.push(`Nợ ${formatVnd(side.debit)}`);
  if (Number(side.credit)) parts.push(`Có ${formatVnd(side.credit)}`);
  return parts.length ? parts.join(" / ") : "0";
}

export interface ImportError { row: number | null; column: string | null; message: string }

/** The server's message, including the per-row errors an import was rejected with. */
export function apiError(error: unknown): { message: string; rows?: ImportError[] } {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (detail && typeof detail === "object" && "errors" in detail) {
      return { message: String((detail as { message?: string }).message || "Có lỗi"), rows: (detail as { errors: ImportError[] }).errors };
    }
    return { message: String(detail || error.message) };
  }
  return { message: "Đã xảy ra lỗi không xác định." };
}

export async function downloadBlob(url: string, filename: string, get: (url: string) => Promise<{ data: Blob }>) {
  const response = await get(url);
  const objectUrl = URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(objectUrl);
}

export const INVOICE_STATUS: Record<string, { label: string; badge: string }> = {
  RECEIVED: { label: "Mới nhận", badge: "ta-badge-neutral" },
  MATCHED: { label: "Đã khớp", badge: "ta-badge-info" },
  EXCEPTION: { label: "Cần xem", badge: "ta-badge-danger" },
  POSTED: { label: "Đã ghi sổ", badge: "ta-badge-success" },
  PAID: { label: "Đã thanh toán", badge: "ta-badge-success" },
  REJECTED: { label: "Đã loại", badge: "ta-badge-neutral" },
};
